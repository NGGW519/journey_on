"""건물 단계: 외곽선 정리 → 층수·용도 → 높이·바닥 → 타일별 압출 glb + buildings.json.

기획서 규칙: 높이 = 층수 × 층고(주거 2.8m, 상업·업무 3.5m), 층수 없으면 1층,
바닥 = 외곽선 아래 DEM 최저값(경사지에서 뜨지 않게).

glb 형식(계약 C1, D-011): 건물 하나 = 노드 `B_<id>` 하나, 프리미티브 [0] 벽·[1] 지붕(바닥면 없음).
두 프리미티브 모두 POSITION·NORMAL·TEXCOORD_0. 벽 UV 는 미터·층 정렬(u = 변마다 정수 칸, v = 높이 ÷ 층고),
지붕 UV 는 roof_tile_m 격자. 평면 음영(면마다 정점 따로).
"""
from __future__ import annotations

import csv
import math
import re
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import trimesh
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely import affinity, contains_xy
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from . import sources
from .config import area_cfg, area_paths, write_json
from .grid import area_tiles
from .meshio import Primitive, write_glb_nodes
from .tilejson import update_tile_json
from .vectors import load_layer
from .zones import no_build_polygons

CLASSES = ("residential", "commercial", "public", "industrial", "other")
CLASS_KO = {"residential": "주거", "commercial": "상업·업무", "public": "공공",
            "industrial": "공업", "other": "기타"}

# 건축물대장 주용도코드 앞 2자리 → 재질 클래스
USAGE_CLASS = {
    "01": "residential", "02": "residential",                       # 단독·공동주택
    "03": "commercial", "04": "commercial", "07": "commercial",     # 근린생활·판매
    "14": "commercial", "15": "commercial", "16": "commercial",     # 업무·숙박·위락
    "05": "public", "06": "public", "08": "public", "09": "public", # 문화집회·종교·운수·의료
    "10": "public", "11": "public", "12": "public", "13": "public", # 교육연구·노유자·수련·운동
    "23": "public", "24": "public",                                 # 교정군사·방송통신
    "17": "industrial", "18": "industrial", "19": "industrial",     # 공장·창고·위험물
    "20": "industrial", "22": "industrial", "25": "industrial",     # 자동차·자원순환·발전
}

OSM_CLASS = {
    "residential": {"house", "residential", "apartments", "detached", "semidetached_house",
                    "terrace", "dormitory", "bungalow", "cabin", "villa"},
    "commercial": {"commercial", "retail", "office", "hotel", "kiosk", "supermarket", "motel"},
    "public": {"public", "civic", "government", "school", "university", "college", "hospital",
               "church", "temple", "chapel", "train_station", "transportation", "kindergarten",
               "fire_station", "police", "community_centre", "sports_hall", "stadium", "museum"},
    "industrial": {"industrial", "warehouse", "factory", "manufacture", "hangar", "service"},
}
OSM_AMENITY_PUBLIC = {"townhall", "police", "fire_station", "school", "hospital", "clinic",
                      "library", "post_office", "place_of_worship", "community_centre"}

# GIS건물통합정보(AL_D010) 열 이름 후보. 실제 파일을 받은 뒤 inspect 결과로 확정한다.
OFFICIAL_FIELDS = {
    "id": ["A1", "GIS건물통합식별번호", "BD_MGT_SN", "UFID"],
    "floors": ["A26", "지상층수", "GRND_FLR", "FLOORS"],
    "usage_code": ["A8", "건축물용도코드", "BDTYP_CD", "MAIN_PURPS_CD"],
    "usage_name": ["A9", "건축물용도명", "MAIN_PURPS_NM"],
    "address": ["A4", "법정동명", "ADDR"],
    "address2": ["A5", "지번"],
    "height": ["A16", "높이", "HEIGHT"],
}


def classify_usage_code(code: Any) -> str:
    s = re.sub(r"\D", "", str(code or ""))
    if len(s) >= 2:
        return USAGE_CLASS.get(s[:2].zfill(2), "other")
    return "other"


def classify_osm(tags: dict[str, Any]) -> tuple[str, str]:
    b = str(tags.get("building") or "yes").lower()
    for cls, vals in OSM_CLASS.items():
        if b in vals:
            return cls, b
    if tags.get("shop") or tags.get("tourism") in ("hotel", "motel", "guest_house"):
        return "commercial", b
    if tags.get("amenity") in OSM_AMENITY_PUBLIC:
        return "public", b
    if tags.get("amenity"):
        return "commercial", b
    return "other", b


def parse_floors(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    m = re.search(r"\d+(\.\d+)?", str(v))
    if not m:
        return None
    f = float(m.group(0))
    return f if f > 0 else None


def resolve_floors(raw: Any, cfg: dict[str, Any]) -> tuple[int, str]:
    """층수와 그 출처(source/default/invalid)."""
    b = cfg["buildings"]
    f = parse_floors(raw)
    if f is None:
        return b["default_floors"], "default"
    if f > b["max_floors"]:
        return b["default_floors"], "invalid"
    return max(1, int(round(f))), "source"


def building_height(floors: int, cls: str, cfg: dict[str, Any]) -> float:
    return floors * float(cfg["buildings"]["floor_height_m"].get(cls, cfg["buildings"]["floor_height_m"]["other"]))


def class_value(table: dict[str, Any], cls: str) -> float:
    return float(table.get(cls, table.get("other")))


def facade_spec(cfg: dict[str, Any]) -> dict[str, Any]:
    """manifest 최상위 `facade`(계약 C1). Unity 는 텍스처 칸 비율을 정할 때 이 값을 쓴다."""
    b = cfg["buildings"]
    return {"bay_m": {c: class_value(b["facade_bay_m"], c) for c in CLASSES},
            "roof_tile_m": float(b["roof_tile_m"]),
            "floor_height_m": {c: class_value(b["floor_height_m"], c) for c in CLASSES}}


def wall_bays(length: float, bay_m: float) -> int:
    """변 하나의 창 칸 수 n = max(1, round(변 길이 ÷ bay_m)). 순수 함수(계약 C1)."""
    return max(1, int(round(length / bay_m)))


def building_primitives(poly: Polygon, base: float, height: float, cls: str, bay_m: float,
                        floor_h: float, roof_tile_m: float) -> list[Primitive]:
    """로컬 좌표 외곽선 → [벽, 지붕] 프리미티브(로컬 E, N, Up, Unity 기준 UV). 순수 함수(테스트 대상).

    벽: 모든 링(바깥·구멍)의 옆면. 링마다 u 는 0 에서 시작해 변마다 정수 칸(wall_bays)만큼 늘어난다.
        v = (z − base) ÷ floor_h → 바닥 0, 지붕선 height ÷ floor_h.
    지붕: 윗면(earcut). u = (E − 최소 E) ÷ roof_tile_m, v = (N − 최소 N) ÷ roof_tile_m.
    """
    poly = orient(poly, sign=1.0)              # 바깥 링 반시계, 구멍 시계 → 오른쪽 = 건물 바깥
    top = base + height
    vtop = height / floor_h
    P, Nn, UV, F = [], [], [], []
    for ring in [poly.exterior, *poly.interiors]:
        c = np.asarray(ring.coords, dtype=np.float64)[:, :2]
        u0 = 0
        for a, b in zip(c[:-1], c[1:]):
            d = b - a
            L = float(np.hypot(*d))
            if L < 1e-6:
                continue
            n = wall_bays(L, bay_m)
            k = len(P)
            P += [(a[0], a[1], base), (b[0], b[1], base), (b[0], b[1], top), (a[0], a[1], top)]
            nrm = (d[1] / L, -d[0] / L, 0.0)
            Nn += [nrm] * 4
            UV += [(u0, 0.0), (u0 + n, 0.0), (u0 + n, vtop), (u0, vtop)]
            F += [(k, k + 1, k + 2), (k, k + 2, k + 3)]
            u0 += n
    wall = Primitive(position=np.asarray(P, dtype=np.float64), normal=np.asarray(Nn, dtype=np.float64),
                     uv=np.asarray(UV, dtype=np.float64), indices=np.asarray(F, dtype=np.int64).reshape(-1, 3),
                     material=f"M_{cls}_wall")
    v2, f2 = trimesh.creation.triangulate_polygon(poly, engine="earcut")
    v2 = np.asarray(v2, dtype=np.float64)
    f2 = np.asarray(f2, dtype=np.int64).reshape(-1, 3)
    if len(f2):
        a, b, c3 = v2[f2[:, 0]], v2[f2[:, 1]], v2[f2[:, 2]]
        cw = ((b[:, 0] - a[:, 0]) * (c3[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c3[:, 0] - a[:, 0])) < 0
        f2[cw] = f2[cw][:, ::-1]               # 위에서 볼 때 반시계(법선 +Up)
    mn = v2.min(axis=0) if len(v2) else np.zeros(2)
    roof = Primitive(position=np.column_stack([v2, np.full(len(v2), top)]),
                     normal=np.tile([0.0, 0.0, 1.0], (len(v2), 1)),
                     uv=(v2 - mn) / roof_tile_m, indices=f2, material="M_roof")
    return [wall, roof]


def _pick(cols, names):
    for n in names:
        if n in cols:
            return n
    return None


def normalize(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """원천별 열을 공통 열(src_id, floors_raw, usage_class, usage_name, address)로."""
    rows = []
    for kind, part in gdf.groupby("_src_kind", sort=False):
        cols = set(part.columns)
        if kind == "osm":
            for i, r in part.iterrows():
                tags = {k: r[k] for k in ("building", "shop", "amenity", "tourism") if k in cols and r[k] == r[k]}
                cls, uname = classify_osm(tags)
                addr = " ".join(str(r[k]) for k in ("addr:city", "addr:street", "addr:housenumber")
                                if k in cols and isinstance(r[k], str))
                rows.append({"src_id": f"osm{r.get('osm_type', 'w')[0]}{r.get('osm_id', i)}",
                             "floors_raw": r.get("building:levels") if "building:levels" in cols else None,
                             "usage_class": cls, "usage_name": uname,
                             "name": r.get("name") if "name" in cols and isinstance(r.get("name"), str) else "",
                             "address": addr, "geometry": r.geometry})
        else:
            f = {k: _pick(cols, v) for k, v in OFFICIAL_FIELDS.items()}
            for i, r in part.iterrows():
                code = r[f["usage_code"]] if f["usage_code"] else None
                addr = " ".join(str(r[c]) for c in (f["address"], f["address2"]) if c and r[c] == r[c])
                rows.append({"src_id": str(r[f["id"]]) if f["id"] else f"b{i}",
                             "floors_raw": r[f["floors"]] if f["floors"] else None,
                             "usage_class": classify_usage_code(code),
                             "usage_name": str(r[f["usage_name"]]) if f["usage_name"] else str(code or ""),
                             "name": "", "address": addr, "geometry": r.geometry})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=gdf.crs)


def clean(gdf: gpd.GeoDataFrame, min_area: float) -> tuple[gpd.GeoDataFrame, dict[str, int]]:
    stats = {"input": len(gdf)}
    gdf = gdf.copy()
    gdf["geometry"] = gdf.geometry.make_valid()
    gdf = gdf.explode(index_parts=False)
    gdf = gdf[gdf.geometry.geom_type == "Polygon"]
    stats["after_explode"] = len(gdf)
    gdf = gdf[gdf.geometry.area >= min_area]
    stats["after_min_area"] = len(gdf)
    gdf["_wkb"] = gdf.geometry.normalize().to_wkb()
    gdf = gdf.drop_duplicates("_wkb").drop(columns="_wkb")
    stats["after_dedupe"] = len(gdf)
    gdf["geometry"] = gdf.geometry.simplify(0.05, preserve_topology=True)
    gdf = gdf[gdf.geometry.is_valid & ~gdf.geometry.is_empty]
    stats["clean"] = len(gdf)
    return gdf.reset_index(drop=True), stats


def base_heights(gdf: gpd.GeoDataFrame, dem_path) -> np.ndarray:
    """외곽선 아래(경계 걸친 셀 포함) DEM 최저값."""
    out = np.full(len(gdf), np.nan)
    with rasterio.open(dem_path) as src:
        z = src.read(1)
        T = src.transform
        H, W = z.shape
        for i, g in enumerate(gdf.geometry):
            minx, miny, maxx, maxy = g.bounds
            win = from_bounds(minx - 1.5, miny - 1.5, maxx + 1.5, maxy + 1.5, T).round_offsets().round_lengths()
            r0, c0 = max(0, win.row_off), max(0, win.col_off)
            r1, c1 = min(H, win.row_off + win.height), min(W, win.col_off + win.width)
            if r1 <= r0 or c1 <= c0:
                continue
            sub = z[r0:r1, c0:c1]
            wt = rasterio.windows.transform(rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0), T)
            m = geometry_mask([g], out_shape=sub.shape, transform=wt, invert=True, all_touched=True)
            vals = sub[m] if m.any() else sub.ravel()
            out[i] = float(np.nanmin(vals))
    return out


def run(cfg: dict[str, Any], area: str, args=None) -> None:
    a = area_cfg(cfg, area)
    ap = area_paths(cfg, area).ensure()
    tiles = area_tiles(cfg, area)
    if not a.get("buildings", True):
        print("  이 범위는 건물을 넣지 않습니다.")
        return
    src = load_layer(cfg, area, "buildings", margin_m=0)
    if src is None or src.empty:
        print("  건물 원천이 없습니다 → 건너뜀")
        for t in tiles:
            update_tile_json(ap.tile_dir(t.name), t, cfg, "buildings", None)
        return
    dem_path = ap.interim / "dem_samples.tif"
    if not dem_path.exists():
        raise SystemExit("dem 단계를 먼저 실행하세요(dem_samples.tif 없음).")

    gdf, stats = clean(normalize(src), cfg["buildings"]["min_area_m2"])
    rep = gdf.geometry.representative_point()
    keep = np.ones(len(gdf), dtype=bool)
    for _, zgeom in no_build_polygons(cfg, area):
        keep &= ~contains_xy(zgeom, rep.x.to_numpy(), rep.y.to_numpy())
    stats["no_build_zone_removed"] = int((~keep).sum())
    gdf, rep = gdf[keep].reset_index(drop=True), rep[keep].reset_index(drop=True)

    fl = [resolve_floors(v, cfg) for v in gdf["floors_raw"]]
    gdf["floors"] = [f for f, _ in fl]
    gdf["floors_source"] = [s for _, s in fl]
    gdf["height_m"] = [building_height(f, c, cfg) for f, c in zip(gdf["floors"], gdf["usage_class"])]
    gdf["base_m"] = base_heights(gdf, dem_path)
    # 수면 위 건물(수상 시설 또는 수면 폴리곤 오차): 깎은 하상이 아니라 수면 높이에 올린다
    lvl_path = ap.interim / "water_level.tif"
    if lvl_path.exists():
        with rasterio.open(lvl_path) as src:
            lv = np.array([v[0] for v in src.sample(zip(rep.x, rep.y))], dtype=float)
        on_water = ~np.isnan(lv) & (gdf["base_m"].to_numpy() < lv)
        gdf.loc[on_water, "base_m"] = lv[on_water]
        stats["on_water_raised"] = int(on_water.sum())
    gdf["rep_x"], gdf["rep_y"] = rep.x.to_numpy(), rep.y.to_numpy()
    gdf["id"] = [f"{i:06d}" for i in range(len(gdf))]

    missing_path = ap.reports / "floors_missing.csv"
    with open(missing_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "src_id", "floors_source", "usage_class", "name", "address", "rep_x", "rep_y", "area_m2"])
        for r in gdf[gdf["floors_source"] != "source"].itertuples():
            w.writerow([r.id, r.src_id, r.floors_source, r.usage_class, r.name, r.address,
                        round(r.rep_x, 1), round(r.rep_y, 1), round(r.geometry.area, 1)])

    prov = sources.provenance(sources.select(cfg, "buildings"))
    facade = facade_spec(cfg)
    failed: list[str] = []
    total = 0
    for t in tiles:
        x0, y0, x1, y1 = t.bounds
        sel = gdf[(gdf.rep_x >= x0) & (gdf.rep_x < x1) & (gdf.rep_y >= y0) & (gdf.rep_y < y1)]
        d = ap.tile_dir(t.name)
        if sel.empty:
            (d / "buildings.glb").unlink(missing_ok=True)
            update_tile_json(d, t, cfg, "buildings", None)
            continue
        items, meta = [], []
        for r in sel.itertuples():
            local = affinity.translate(r.geometry, -t.e0, -t.n0)
            cls = r.usage_class
            try:
                prims = building_primitives(Polygon(local.exterior.coords, [i.coords for i in local.interiors]),
                                            float(r.base_m), float(r.height_m), cls,
                                            facade["bay_m"][cls], facade["floor_height_m"][cls], facade["roof_tile_m"])
                if len(prims[1].indices) == 0:
                    raise ValueError("지붕 삼각분할 결과 없음")
            except Exception as e:  # 삼각분할 실패는 목록에 남기고 건너뜀
                print(f"    압출 실패 {r.src_id}: {e}")
                failed.append(r.src_id)
                continue
            node = f"B_{r.id}"
            items.append((node, prims))
            meta.append({"id": r.id, "node": node, "src_id": r.src_id, "usage_class": r.usage_class,
                         "usage_ko": CLASS_KO[r.usage_class], "usage_name": r.usage_name, "name": r.name,
                         "floors": int(r.floors), "floors_source": r.floors_source,
                         "height_m": round(float(r.height_m), 2), "base_m": round(float(r.base_m), 2),
                         "address": r.address, "area_m2": round(float(r.geometry.area), 1)})
        n = write_glb_nodes(d / "buildings.glb", items)
        write_json(d / "buildings.json", meta)
        update_tile_json(d, t, cfg, "buildings", {
            "count": n, "file": "buildings.glb", "meta": "buildings.json",
            "interim": bool(prov and prov["interim"]),
        })
        total += n
    stats["exported"] = total
    stats["mesh_failed"] = failed
    stats["floors_default"] = int((gdf["floors_source"] != "source").sum())
    stats["floors_default_ratio"] = round(stats["floors_default"] / max(1, len(gdf)), 3)
    write_json(ap.reports / "buildings_stats.json", stats)
    print(f"  건물 {total}동 내보냄, 층수 기본값 {stats['floors_default_ratio']:.0%} → {missing_path.name}")
    print(f"  정리 통계: {stats}")
