"""도로 단계: 타일별 도로 지면 마스크(계약 C2)와 교량 상판 메시(계약 C3).

- `roads_mask.png`: 1024×1024 RGB. R = 차도, G = 보행·자전거, B = 철도. 값 = 덮임 비율(0~255, 4×4 부분 표본).
  행 0 = 북쪽 가장자리, 열 0 = 서쪽 가장자리. 다리(bridge)·터널(tunnel) 구간은 뺀다. 폭은 bridges.bridge_width.
- `bridges.glb` + tile.json `bridges.items`: OSM way 하나에 노드 `BR_w<osm_id>` 하나. 상판 상자 + 양옆 난간.
  상판 높이 규칙은 D-012(양 끝 = 접속 지점 지형, 가운데 = max(선형 보간, 수면 + 여유), 경사 상한,
  수면 메시 + 0.2 m 바닥, 나란한 way 높이 공유).
상판 높이는 dem 단계가 `plan_decks` 로 정한다(상판 밑 지형을 낮춘 뒤 RAW 를 쓰기 위해). dem 단계는 그 결과를
`data/interim/<area>/decks.json` 에 남기고, roads 단계는 그것을 읽어 메시만 만든다(`load_decks`).
정사영상이 오면 마스크 대신(또는 함께) 정사영상을 쓰는 방법은 D-012 에 적었다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from rasterio.features import rasterize
from scipy import ndimage
from shapely import STRtree
from shapely.geometry import LineString, Polygon, box

from . import sources
import shapely

from .bridges import (Bridge, bridge_width, deck_mesh, deck_profile, footprint_ring, is_bridge, is_tunnel,
                      load_bridges, offset_lines, profile_breakpoints, resample_stations)
from .config import area_paths, read_json, write_json
from .grid import area_bounds, area_tiles
from .meshio import Primitive, write_glb_nodes
from .surface import TriSurface
from .terrain import TerrainSampler
from .tilejson import update_tile_json
from .vectors import load_layer

CAR = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
       "living_street", "service", "road", "busway",
       "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
FOOT = {"footway", "path", "cycleway", "pedestrian", "steps", "track", "bridleway"}
RAIL = {"rail", "light_rail", "subway", "narrow_gauge", "tram", "monorail", "funicular"}
CHANNELS = ("R", "G", "B")   # 0 차도, 1 보행·자전거, 2 철도
DECKS_VERSION = 1
SHARE_ITER_MAX = 20          # 나란한 way 높이 공유 고정점 반복 상한
SHARE_TOL_M = 0.01           # 반복 사이 상판 변화가 이보다 작으면 멈춘다(1 m 표본 선형 보간이 오목한 곳에서 회당 수 mm 씩 올리므로 1 cm)


# --------------------------------------------------------------------------- 마스크(순수 함수)
def road_channel(layer: str, cls: str) -> int | None:
    """OSM 등급 → 마스크 채널(0 R 차도, 1 G 보행, 2 B 철도). 넣지 않는 등급은 None."""
    if layer == "railways":
        return 2 if cls in RAIL else None
    if cls in CAR:
        return 0
    if cls in FOOT:
        return 1
    return None


def pixel_center(e0: float, n0: float, c, r, size: float = 1000.0, px: int = 1024):
    """마스크 픽셀 (c, r)의 중심 월드 좌표(계약 C2): 행 0 = 북쪽."""
    s = size / px
    return e0 + (np.asarray(c) + 0.5) * s, n0 + size - (np.asarray(r) + 0.5) * s


def world_to_pixel(e0: float, n0: float, E, N, size: float = 1000.0, px: int = 1024):
    """월드 좌표 → 마스크 픽셀 (열, 행) 정수(바깥이면 범위 밖 값)."""
    s = size / px
    return (np.floor((np.asarray(E) - e0) / s).astype(int),
            np.floor((n0 + size - np.asarray(N)) / s).astype(int))


def mask_tile(features: list[tuple[int, Any]], e0: float, n0: float, size: float = 1000.0,
              px: int = 1024, ss: int = 4) -> np.ndarray:
    """(채널, 폴리곤) 목록 → (px, px, 3) uint8 덮임 비율. 행 0 = 북쪽. 같은 채널끼리 겹친 곳은 한 번만 센다."""
    n = px * ss
    T = Affine(size / n, 0.0, e0, 0.0, -size / n, n0 + size)
    out = np.zeros((px, px, 3), dtype=np.uint8)
    for ch in range(3):
        geoms = [g for c, g in features if c == ch and not g.is_empty]
        if not geoms:
            continue
        hi = rasterize([(g, 1) for g in geoms], out_shape=(n, n), transform=T, fill=0, dtype="uint8")
        cov = hi.reshape(px, ss, px, ss).sum(axis=(1, 3), dtype=np.int32)
        out[:, :, ch] = np.round(cov * (255.0 / (ss * ss))).astype(np.uint8)
    return out


def _text(v: Any) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    return str(v).strip()


@dataclass
class RoadFeature:
    osm_id: int
    channel: int
    cls: str
    width_m: float
    line: Any          # LineString(5186) 또는 면(area=yes)
    polygon: Any       # 마스크에 칠할 면


def mask_features(cfg: dict[str, Any], area: str, margin_m: float = 60.0) -> tuple[list[RoadFeature], dict[str, int]]:
    """마스크에 칠할 도로·철도 면(다리·터널 제외). 폭 규칙은 교량과 같다(bridge_width)."""
    roads_cfg = cfg.get("roads", {}) or {}
    out: list[RoadFeature] = []
    stats = {"bridge_skipped": 0, "tunnel_skipped": 0, "class_skipped": 0}
    for layer, cls_col in (("roads", "highway"), ("railways", "railway")):
        df = load_layer(cfg, area, layer, margin_m=margin_m)
        if df is None or df.empty:
            continue
        cols = set(df.columns)
        for _, r in df.iterrows():
            cls = _text(r[cls_col]) if cls_col in cols else ""
            ch = road_channel(layer, cls)
            if ch is None:
                stats["class_skipped"] += 1
                continue
            if "bridge" in cols and is_bridge(r["bridge"]):
                stats["bridge_skipped"] += 1
                continue
            if "tunnel" in cols and is_tunnel(r["tunnel"]):
                stats["tunnel_skipped"] += 1
                continue
            g = r.geometry
            if g is None or g.is_empty:
                continue
            tags = {k: r[k] for k in ("width", "lanes") if k in cols}
            w, _ = bridge_width(tags, cls, roads_cfg)
            area_yes = "area" in cols and _text(r["area"]).lower() == "yes"
            for part in getattr(g, "geoms", [g]):
                if part.geom_type == "LineString" and part.length > 0:
                    if area_yes and part.is_ring and len(part.coords) >= 4:
                        poly = Polygon(part.coords).buffer(0)
                    else:
                        poly = part.buffer(w / 2.0, cap_style="round", join_style="round", quad_segs=4)
                elif part.geom_type == "Polygon":
                    poly = part
                else:
                    continue
                out.append(RoadFeature(int(r["osm_id"]) if "osm_id" in cols else -1, ch, cls, round(w, 2), part, poly))
    out.sort(key=lambda f: (f.channel, f.osm_id, f.line.bounds))
    return out, stats


def width_outliers(cfg: dict[str, Any], area: str, margin_m: float = 200.0) -> list[dict[str, Any]]:
    """width·lanes 태그가 등급 기본값 × width_outlier_ratio 를 넘어 거른 way 목록(도로 마스크·교량 공통, 06a)."""
    from .bridges import class_width, width_check
    roads_cfg = cfg.get("roads", {}) or {}
    bounds = area_bounds(cfg, area)
    rows = []
    for layer, cls_col in (("roads", "highway"), ("railways", "railway")):
        df = load_layer(cfg, area, layer, margin_m=margin_m)
        if df is None or df.empty:
            continue
        cols = set(df.columns)
        for _, r in df.iterrows():
            cls = _text(r[cls_col]) if cls_col in cols else ""
            tags = {k: r[k] for k in ("width", "lanes") if k in cols}
            w, src, rej = width_check(tags, cls, roads_cfg)
            if not rej:
                continue
            g = r.geometry
            c = g.interpolate(0.5, normalized=True) if g is not None and g.geom_type in ("LineString", "MultiLineString") \
                else (g.representative_point() if g is not None else None)
            inside = c is not None and bounds[0] <= c.x <= bounds[2] and bounds[1] <= c.y <= bounds[3]
            rows.append({"osm_id": f"w{int(r['osm_id'])}" if "osm_id" in cols else "", "layer": layer, "class": cls,
                         "bridge": bool("bridge" in cols and is_bridge(r["bridge"])), "rejected": ";".join(rej),
                         "width_used_m": round(w, 2), "source_used": src,
                         "class_default_m": round(class_width(cls, roads_cfg), 2),
                         "e": round(c.x, 1) if c is not None else None, "n": round(c.y, 1) if c is not None else None,
                         "in_area": bool(inside)})
    rows.sort(key=lambda d: d["osm_id"])
    return rows


# --------------------------------------------------------------------------- 교량 연결 관계
def _key(p) -> tuple[float, float]:
    return (round(float(p[0]), 3), round(float(p[1]), 3))


@dataclass
class _Vtx:
    osm_id: int
    bridge: bool
    tunnel: bool
    end: bool


def vertex_index(cfg: dict[str, Any], area: str, margin_m: float = 200.0) -> dict[tuple[float, float], list[_Vtx]]:
    """roads·railways 모든 way 의 정점 → 그 정점을 쓰는 way 목록(OSM 노드 공유 = 같은 좌표)."""
    idx: dict[tuple[float, float], list[_Vtx]] = {}
    for layer in ("roads", "railways"):
        df = load_layer(cfg, area, layer, margin_m=margin_m)
        if df is None or df.empty:
            continue
        cols = set(df.columns)
        for _, r in df.iterrows():
            g = r.geometry
            br = "bridge" in cols and is_bridge(r["bridge"])
            tu = "tunnel" in cols and is_tunnel(r["tunnel"])
            for part in getattr(g, "geoms", [g]):
                if part.geom_type != "LineString":
                    continue
                cs = list(part.coords)
                for i, c in enumerate(cs):
                    idx.setdefault(_key(c), []).append(_Vtx(int(r["osm_id"]), br, tu, i in (0, len(cs) - 1)))
    return idx


@dataclass
class Chain:
    """끝끼리 이어진 다리 way 들(다리 아닌 길이 끼지 않은 노드로만 이어짐). members: (다리 번호, 뒤집음)."""
    members: list[tuple[int, bool]]
    line: LineString = None                         # type: ignore[assignment]
    bounds_x: list[tuple[float, float]] = field(default_factory=list)   # 사슬 좌표 x 에서 각 way 의 범위
    ends: list[dict[str, Any]] = field(default_factory=list)
    x: np.ndarray | None = None
    top: np.ndarray | None = None
    info: dict[str, Any] = field(default_factory=dict)


def bridge_chains(bridges: list[Bridge], vidx: dict[tuple[float, float], list[_Vtx]]) -> list[Chain]:
    """다리를 사슬로 묶고 양 끝을 분류한다. 끝 종류: shared(다리 아닌 길과 노드 공유), bridge(다른 다리에 붙음), free."""
    ends: dict[tuple[float, float], list[tuple[int, int]]] = {}
    for i, b in enumerate(bridges):
        cs = b.geometry.coords
        ends.setdefault(_key(cs[0]), []).append((i, 0))
        ends.setdefault(_key(cs[-1]), []).append((i, 1))

    def node_info(i: int, side: int):
        b = bridges[i]
        k = _key(b.geometry.coords[0 if side == 0 else -1])
        others = [v for v in vidx.get(k, []) if not (v.bridge and v.osm_id == b.osm_id)]
        non_bridge = [v.osm_id for v in others if not v.bridge]
        partners = [(j, s) for j, s in ends.get(k, []) if j != i]
        mids = [v.osm_id for v in others if v.bridge and not v.end]
        return k, non_bridge, partners, mids

    link: dict[tuple[int, int], tuple[int, int]] = {}
    for i in range(len(bridges)):
        for side in (0, 1):
            _, nb, partners, mids = node_info(i, side)
            if not nb and len(partners) == 1 and not mids:
                link[(i, side)] = partners[0]
    seen: set[int] = set()
    chains: list[Chain] = []
    for i0 in range(len(bridges)):
        if i0 in seen:
            continue
        seen.add(i0)
        seq: list[tuple[int, bool]] = [(i0, False)]
        cur, side = i0, 1
        while (cur, side) in link:
            nb, ns = link[(cur, side)]
            if nb in seen:
                break
            seen.add(nb)
            seq.append((nb, ns == 1))
            cur, side = nb, 1 - ns
        cur, side = i0, 0
        while (cur, side) in link:
            nb, ns = link[(cur, side)]
            if nb in seen:
                break
            seen.add(nb)
            seq.insert(0, (nb, ns == 0))
            cur, side = nb, 1 - ns
        coords: list[tuple[float, float]] = []
        bx = []
        x0 = 0.0
        for j, rev in seq:
            cs = list(bridges[j].geometry.coords)
            cs = [c[:2] for c in (cs[::-1] if rev else cs)]
            if coords and _key(coords[-1]) == _key(cs[0]):
                cs = cs[1:]
            coords.extend(cs)
            L = bridges[j].geometry.length
            bx.append((x0, x0 + L))
            x0 += L
        ch = Chain(seq, LineString(coords), bx)
        for pos, (j, rev) in ((0, seq[0]), (1, seq[-1])):
            side = (1 if rev else 0) if pos == 0 else (0 if rev else 1)
            k, nb, partners, mids = node_info(j, side)
            cs = np.asarray(bridges[j].geometry.coords)[:, :2]
            p, q = (cs[0], cs[1]) if side == 0 else (cs[-1], cs[-2])
            out_dir = (p - q) / max(float(np.hypot(*(p - q))), 1e-9)
            if nb:
                kind = "shared"
            elif mids or partners:
                kind = "bridge"
            else:
                kind = "free"
            attached = sorted({v for v in mids} | {bridges[pj].osm_id for pj, _ in partners})
            ch.ends.append({"kind": kind, "e": float(p[0]), "n": float(p[1]), "out": out_dir.tolist(),
                            "with": nb[:4], "attached": attached})
        chains.append(ch)
    return chains


# --------------------------------------------------------------------------- 수면 높이(넓힌 띠 포함)
def _water_level(ap, overlap_m: float):
    p = ap.interim / "water_level.tif"
    if not p.exists():
        return None, None
    with rasterio.open(p) as s:
        F = s.read(1).astype(np.float32)
        T = s.transform
    r = int(np.ceil(overlap_m / abs(T.a)))
    if r > 0:
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        fp = np.hypot(yy, xx) * abs(T.a) <= overlap_m + 1e-6
        G = ndimage.maximum_filter(np.where(np.isnan(F), -np.inf, F), footprint=fp)
        F = np.where(np.isfinite(G), G, np.nan).astype(np.float32)
    return F, T


def _sample_nearest(F, T, E, N):
    out = np.full(np.shape(E), np.nan)
    if F is None:
        return out
    c, r = ~T @ (np.asarray(E), np.asarray(N))
    r, c = np.floor(r).astype(int), np.floor(c).astype(int)
    ok = (r >= 0) & (r < F.shape[0]) & (c >= 0) & (c < F.shape[1])
    out[ok] = F[r[ok], c[ok]]
    return out




def _coords(line, s) -> np.ndarray:
    """선 위 위치 s(배열) → (n, 2) 좌표."""
    return shapely.get_coordinates(shapely.line_interpolate_point(line, np.asarray(s, dtype=np.float64)))


def _water_at(water: TriSurface | None, pts: np.ndarray, nvec: np.ndarray, half: np.ndarray) -> np.ndarray:
    """중심선 표본마다 중심에서 ±half 안(가운데·양 가장자리·½ 지점) 수면 메시 높이의 최대(없으면 NaN)."""
    lv = np.full(len(pts), np.nan)
    if water is None or len(water) == 0 or len(pts) == 0:
        return lv
    for f in (-1.0, -0.5, 0.0, 0.5, 1.0):
        q = pts + nvec * (half * f)[:, None]
        lv = np.fmax(lv, water.height(q[:, 0], q[:, 1]))
    return lv


def end_tapers(ch: Chain) -> tuple[bool, bool]:
    """사슬 양 끝이 땅에 닿는가(접속 길 공유 끝, 물 위가 아닌 바깥 지형 끝). 다른 다리에 붙은 끝·물 위 끝은 False."""
    out = []
    for end in ch.ends:
        rule = str(end.get("rule", end["kind"]))
        out.append(end["kind"] == "shared" or (end["kind"] == "free" and "water" not in rule))
    return out[0], out[1]


def chain_label(ch: Chain, bridges: list[Bridge]) -> str:
    return "+".join(f"w{bridges[j].osm_id}" for j, _ in ch.members)


# --------------------------------------------------------------------------- 교량 상판 높이(dem 단계가 부른다)
def compute_decks(cfg: dict[str, Any], bridges: list[Bridge], chains: list[Chain], terrain: TerrainSampler,
                  water: TriSurface | None, share: dict[int, np.ndarray] | None = None) -> list[str]:
    """사슬마다 상판 종단(x, top)과 끝 높이를 정한다. 범위 밖 끝·서로 기다린 끝 메모를 돌려준다.

    share: 사슬 번호 → 나란한 다리의 같은 단면 상판 높이(1 m 표본, 없으면 NaN). plan_decks 가 반복해서 넘긴다.
    """
    rc = cfg["roads"]
    clear = rc["bridge_clearance_m"]
    fb = float(rc.get("bridge_end_fallback_m", 10.0))
    minx, miny, maxx, maxy = terrain.bounds
    notes: list[str] = []
    by_osm: dict[int, Chain] = {}
    for ch in chains:
        for j, _ in ch.members:
            by_osm[bridges[j].osm_id] = ch

    def deck_at(ch: Chain, e: float, n: float) -> float | None:
        if ch.top is None or ch.x is None:
            return None
        s = ch.line.project(_pt((e, n)))
        return float(np.interp(s, ch.x, ch.top))

    def end_height(ch: Chain, end: dict[str, Any], label: str) -> tuple[float | None, str]:
        e, n = end["e"], end["n"]
        if end["kind"] == "shared":
            h = float(terrain.height(e, n)[0])
            rule = "shared"
        elif end["kind"] == "bridge":
            hs = [deck_at(by_osm[o], e, n) for o in end["attached"] if o in by_osm and by_osm[o] is not ch]
            hs = [h for h in hs if h is not None]
            if not hs:
                return None, "bridge"          # 붙은 다리가 아직 계산 전
            h, rule = max(hs), "bridge"
        else:
            ox, oy = end["out"]
            pe, pn = e + ox * fb, n + oy * fb
            h_out = float(terrain.height(pe, pn)[0])
            end["sample_e"], end["sample_n"] = round(pe, 1), round(pn, 1)
            # 접속 길 없는 끝이 물 위(선착장·잔교)면 바깥 지형은 깎은 하상이다 → 수면 + 여유 높이로 둔다
            lv = np.nan
            if water is not None and len(water):
                lv = float(np.fmax(water.height([e], [n]), water.height([pe], [pn]))[0])
            if np.isfinite(lv):
                kind = bridges[ch.members[0 if end is ch.ends[0] else -1][0]].kind
                h = max(h_out, lv + float(clear.get(kind, 3.0)))
                rule = f"outward{fb:g}+water"
            else:
                # 육지 끝(06a, 계약 C3 "끝 = 그 자리 지형 ±0.3"): 끝 지점 지형에 고정한다. 바깥 fb m 지형은
                # 끝 밖 땅의 경사(ground_grade, 보고용)를 재는 데만 쓴다(전에는 그 높이를 그대로 써서 +0.98 m 단).
                h = float(terrain.height(e, n)[0])
                rule = "end_terrain"
                if np.isfinite(h) and np.isfinite(h_out):
                    end["outward_terrain_m"] = round(h_out, 2)
                    end["ground_grade"] = round((h_out - h) / fb, 3)
        if not np.isfinite(h):                 # 범위 밖: 가장 가까운 범위 안 점
            ce, cn = float(np.clip(e, minx, maxx)), float(np.clip(n, miny, maxy))
            h = float(terrain.height(ce, cn)[0])
            rule += "+clamped"
            notes.append(f"{label}: 끝 E{e:.0f} N{n:.0f} 이 범위 밖 → 가장 가까운 경계 지형 사용")
        return h, rule

    pending = list(range(len(chains)))
    for _ in range(len(chains) + 1):
        if not pending:
            break
        nxt = []
        for ci in pending:
            ch = chains[ci]
            label = chain_label(ch, bridges)
            hs = []
            for end in ch.ends:
                h, rule = end_height(ch, end, label)
                hs.append(h)
                end["rule"] = rule
            if any(h is None for h in hs):
                nxt.append(ci)
                continue
            h0, h1 = _profile(ch, bridges, float(hs[0]), float(hs[1]), cfg, water, terrain,
                              None if share is None else share.get(ci))
            for end, h, h_raw in zip(ch.ends, (h0, h1), hs):
                end["deck_m"] = round(float(h), 2)
                end["terrain_m"] = round(float(terrain.height(end["e"], end["n"])[0]), 2)
                end["raise_m"] = round(float(h - h_raw), 2)
        if len(nxt) == len(pending):           # 서로 기다림(고리) → 지형으로
            for ci in nxt:
                for end in chains[ci].ends:
                    if end["kind"] == "bridge":
                        end["kind"] = "shared"
            notes.append(f"다리끼리 서로 붙은 끝 {len(nxt)}개 사슬: 지형 높이로 처리")
        pending = nxt
    return notes


def _profile(ch: Chain, bridges, h0: float, h1: float, cfg, water, terrain, share=None) -> tuple[float, float]:
    """사슬 하나의 종단. 끝 높이는 수면 바닥(수면 메시 + bridge_min_water_gap_m) 이상으로 올린다(올린 끝 높이를 돌려준다)."""
    rc = cfg["roads"]
    clear, grade = rc["bridge_clearance_m"], rc["bridge_max_grade"]
    sm = float(rc.get("bridge_smooth_m", 20.0))
    gap = float(rc.get("bridge_min_water_gap_m", 0.2))
    kinds = [bridges[j].kind for j, _ in ch.members]
    gmax = min(float(grade.get(k, 0.08)) for k in kinds)
    L = ch.line.length
    x = resample_stations(L, 1.0)
    pts = _coords(ch.line, x)
    # 그 자리 way 의 폭·여유
    wid = np.zeros(len(x))
    clr = np.zeros(len(x))
    for (j, _), (a, b) in zip(ch.members, ch.bounds_x):
        sel = (x >= a - 1e-6) & (x <= b + 1e-6)
        wid[sel] = bridges[j].width_m
        clr[sel] = float(clear.get(bridges[j].kind, 3.0))
    if len(pts) >= 2:
        Lp, _, _ = offset_lines(pts, 1.0)
        nvec = Lp - pts
    else:
        nvec = np.zeros_like(pts)
    # 여유(목표)는 상판 바깥 폭, 바닥(지킬 값)은 걷는 윗면(난간 안쪽) 폭에서 잰 수면 최대
    pt_ = float(rc.get("parapet_thickness_m", 0.25))
    lv = _water_at(water, pts, nvec, wid / 2.0)
    lv_top = _water_at(water, pts, nvec, np.maximum(wid / 2.0 - pt_, 0.0))
    req = lv + clr
    floor = lv_top + gap
    e0 = max(h0, float(floor[0])) if np.isfinite(floor[0]) else h0
    e1 = max(h1, float(floor[-1])) if np.isfinite(floor[-1]) else h1
    top, info = deck_profile(x, e0, e1, req, gmax, sm, floor=floor, share=share)
    info["grade_max"] = gmax
    info["clearance_m"] = float(np.max(clr)) if len(clr) else 0.0
    if np.isfinite(lv).any():
        info["water_max_m"] = round(float(np.nanmax(lv)), 2)
        info["over_water_m"] = round(float(np.isfinite(lv).sum() * (x[1] - x[0] if len(x) > 1 else 0)), 1)
        if np.isfinite(lv_top).any():
            info["water_gap_min_m"] = round(float(np.nanmin(top - lv_top)), 3)
    tg = terrain.height(pts[:, 0], pts[:, 1])
    buried = np.isfinite(tg) & (tg > top + 0.3)
    info["buried_before_m"] = round(float(buried.sum() * (x[1] - x[0] if len(x) > 1 else 0)), 1)
    ch.x, ch.top, ch.info = x, top, info
    return e0, e1


# --------------------------------------------------------------------------- 나란한 way 높이 공유
@dataclass
class Partner:
    """사슬 a 의 1 m 표본 ia 와 같은 단면에 있는 사슬 b 위치 sb(m), 중심선 간격 dist."""
    a: int
    b: int
    ia: np.ndarray
    sb: np.ndarray
    dist: np.ndarray


def _tangents(line, s: np.ndarray, h: float = 1.0) -> np.ndarray:
    L = line.length
    p = _coords(line, np.clip(s - h, 0.0, L))
    q = _coords(line, np.clip(s + h, 0.0, L))
    v = q - p
    return v / np.maximum(np.hypot(v[:, 0], v[:, 1]), 1e-12)[:, None]


def parallel_partners(chains: list[Chain], bridges: list[Bridge], rc: dict[str, Any]) -> list[Partner]:
    """중심선 간격 ≤ bridge_parallel_m 이고 방향 차 ≤ bridge_parallel_angle_deg 로 나란한 사슬 쌍(순서 있음).

    같은 단면 = a 의 표본에서 b 중심선에 내린 가장 가까운 점(b 끝 0.5 m 안쪽에 떨어질 때만). 계단(steps)이 든 사슬과
    서로 끝이 붙은 사슬(다리에서 내려오는 계단 등)은 뺀다. 5 m 이상 나란해야 쌍으로 본다. 순수 함수(테스트 대상).
    """
    dmax = float(rc.get("bridge_parallel_m", 12.0))
    cmin = float(np.cos(np.radians(float(rc.get("bridge_parallel_angle_deg", 20.0)))))
    n = len(chains)
    skip = [any(bridges[j].cls == "steps" for j, _ in ch.members) for ch in chains]
    ids = [{bridges[j].osm_id for j, _ in ch.members} for ch in chains]
    att = [{o for e in ch.ends for o in e.get("attached", [])} for ch in chains]
    st = []
    for ch in chains:
        x = resample_stations(ch.line.length, 1.0)
        st.append((x, _coords(ch.line, x)))
    out: list[Partner] = []
    for a in range(n):
        for b in range(n):
            if a == b or skip[a] or skip[b] or (att[a] & ids[b]) or (att[b] & ids[a]):
                continue
            A, B = chains[a].line, chains[b].line
            if A.distance(B) > dmax:
                continue
            x, P = st[a]
            sb = shapely.line_locate_point(B, shapely.points(P))
            Q = _coords(B, sb)
            d = np.hypot(*(P - Q).T)
            cos = np.abs((_tangents(A, x) * _tangents(B, sb)).sum(axis=1))
            ok = (d <= dmax) & (cos >= cmin) & (sb > 0.5) & (sb < B.length - 0.5)
            if ok.sum() >= 5:
                k = np.nonzero(ok)[0]
                out.append(Partner(a, b, k, sb[k], d[k]))
    return out


def share_targets(chains: list[Chain], partners: list[Partner]) -> dict[int, np.ndarray]:
    """사슬마다 나란한 사슬들의 같은 단면 상판 높이 최대(1 m 표본, 짝이 없는 곳 NaN)."""
    out: dict[int, np.ndarray] = {}
    for p in partners:
        A, B = chains[p.a], chains[p.b]
        assert A.x is not None and B.x is not None and B.top is not None
        arr = out.setdefault(p.a, np.full(len(A.x), np.nan))
        arr[p.ia] = np.fmax(arr[p.ia], np.interp(p.sb, B.x, B.top))
    return out


def _pair_report(chains, bridges, partners, tops0) -> list[dict[str, Any]]:
    """나란한 쌍마다 같은 단면 상판 높이 차: 공유 전(tops0) → 뒤. 남은 단은 양 끝(지형에 고정)에서 경사 상한으로 오르는 구간."""
    seen = set()
    rows = []
    for p in partners:
        key = (min(p.a, p.b), max(p.a, p.b))
        if key in seen:
            continue
        seen.add(key)
        A, B = chains[p.a], chains[p.b]
        before = tops0[p.a][p.ia] - np.interp(p.sb, B.x, tops0[p.b])
        after = A.top[p.ia] - np.interp(p.sb, B.x, B.top)
        k = int(np.argmax(np.abs(after)))
        pt = _coords(A.line, A.x[p.ia[k:k + 1]])[0]
        big = np.abs(after) > 0.05
        rows.append({"a": chain_label(A, bridges), "b": chain_label(B, bridges),
                     "parallel_m": int(len(p.ia)), "dist_m": [round(float(p.dist.min()), 1), round(float(np.median(p.dist)), 1),
                                                              round(float(p.dist.max()), 1)],
                     "step_before_max_m": round(float(np.abs(before).max()), 2),
                     "step_before_over_0_05_m": int((np.abs(before) > 0.05).sum()),
                     "step_after_max_m": round(float(np.abs(after).max()), 2),
                     "step_after_over_0_05_m": int(big.sum()),
                     "step_after_at": [round(float(pt[0]), 1), round(float(pt[1]), 1)]})
    return rows


@dataclass
class DeckPlan:
    chains: list[Chain]
    warnings: list[str]
    parallel: list[dict[str, Any]]
    outside: list[int]
    iterations: int


def deck_warnings(chains: list[Chain], bridges: list[Bridge], rc: dict[str, Any]) -> list[str]:
    rmax = float(rc.get("bridge_end_raise_max_m", 0.5))
    gap = float(rc.get("bridge_min_water_gap_m", 0.2))
    warns = []
    for ch in chains:
        label = chain_label(ch, bridges)
        info = ch.info
        if "grade_forced" in info:
            warns.append(f"{label}: 양 끝 높이 차가 경사 상한 {info['grade_max']:.0%} 를 넘음 → {info['grade_forced']:.1%} 로 잇는다")
        if "floor_over_grade_m" in info:
            warns.append(f"{label}: 수면 + {gap:g} m 바닥을 지키느라 경사 상한을 넘김(최대 {info['floor_over_grade_m']:.2f} m)")
        if "clearance_short_m" in info:
            warns.append(f"{label}: 수면 위 여유 {info['clearance_m']:g} m 를 양 끝·경사 상한 안에서 못 맞춤"
                         f"(최대 {info['clearance_short_m']:.2f} m 모자람, 시작에서 {info['short_at_m']:.0f} m)")
        for end in ch.ends:
            if end.get("raise_m", 0.0) > rmax + 1e-6:
                warns.append(f"{label}: 끝 E{end['e']:.0f} N{end['n']:.0f} 상판이 지형보다 {end['raise_m']:.2f} m 높음"
                             f"(끝 단면 걷는 폭 안 수면 메시 + {gap:g} m 바닥이 지형 + {rmax:g} m 보다 높음 — 원천 불일치)")
            if str(end["rule"]).startswith("end_terrain") and abs(end["deck_m"] - end.get("raise_m", 0.0) - end["terrain_m"]) > 0.3:
                warns.append(f"{label}: 접속 길 없는 끝 E{end['e']:.0f} N{end['n']:.0f} 상판 {end['deck_m']} m ↔ "
                             f"끝 지형 {end['terrain_m']} m (끝 지형 고정인데 ±0.3 m 밖)")
    return warns


def plan_decks(cfg: dict[str, Any], area: str, bridges: list[Bridge], terrain: TerrainSampler,
               water: TriSurface | None) -> DeckPlan:
    """다리 사슬을 만들고 상판 높이를 정한다(dem 단계, 상판 밑 지형을 낮추기 전 지형으로).

    나란한 사슬은 고정점 반복으로 같은 단면 높이를 공유한다: 1회차 = 자기 규칙만, 그다음부터 짝의 직전 상판을
    share 로 넘긴다(끝에서 경사 상한으로 닿는 만큼). 공유값은 올라가기만 하므로 수렴한다.
    """
    rc = cfg["roads"]
    bounds = area_bounds(cfg, area)
    chains = bridge_chains(bridges, vertex_index(cfg, area))

    def inside(j: int) -> bool:
        c = bridges[j].geometry.interpolate(0.5, normalized=True)
        return bounds[0] <= c.x < bounds[2] and bounds[1] <= c.y < bounds[3]
    outside = [bridges[j].osm_id for ch in chains for j, _ in ch.members if not inside(j)]
    chains = [ch for ch in chains if any(inside(j) for j, _ in ch.members)]
    partners, tops0, notes, it = share_decks(cfg, bridges, chains, terrain, water)
    parallel = _pair_report(chains, bridges, partners, tops0) if partners else []
    return DeckPlan(chains, notes + deck_warnings(chains, bridges, rc), parallel, outside, it)


def share_decks(cfg: dict[str, Any], bridges: list[Bridge], chains: list[Chain], terrain, water):
    """나란한 사슬 높이 공유 고정점 반복(chains 의 x·top·ends 를 채운다). (짝 목록, 1회차 상판, 메모, 반복 수)."""
    partners = parallel_partners(chains, bridges, cfg["roads"])
    share: dict[int, np.ndarray] | None = None
    prev = tops0 = None
    notes: list[str] = []
    it = 0
    for it in range(1, SHARE_ITER_MAX + 1):
        for ch in chains:
            ch.top = None
        notes = compute_decks(cfg, bridges, chains, terrain, water, share)
        tops = [np.asarray(ch.top).copy() for ch in chains]
        if tops0 is None:
            tops0 = tops
        if not partners or (prev is not None and max(float(np.abs(t - q).max()) for t, q in zip(tops, prev)) < SHARE_TOL_M):
            break
        prev = tops
        share = share_targets(chains, partners)
    return partners, tops0, notes, it


# --------------------------------------------------------------------------- decks.json (dem → roads)
def save_decks(path, plan: DeckPlan, bridges: list[Bridge], extra: dict[str, Any] | None = None) -> None:
    rows = []
    for ch in plan.chains:
        assert ch.x is not None and ch.top is not None
        rows.append({"members": [{"j": int(j), "osm_id": int(bridges[j].osm_id), "reversed": bool(rev)} for j, rev in ch.members],
                     "line": [[float(a), float(b)] for a, b in ch.line.coords],
                     "bounds_x": [[float(a), float(b)] for a, b in ch.bounds_x],
                     "x": [float(v) for v in ch.x], "top": [float(v) for v in ch.top],
                     "ends": ch.ends, "taper": list(end_tapers(ch)), "info": ch.info})
    write_json(path, {"version": DECKS_VERSION, "chains": rows, "warnings": plan.warnings, "parallel": plan.parallel,
                      "outside_area": [f"w{o}" for o in plan.outside], "share_iterations": plan.iterations, **(extra or {})})


def load_decks(data: dict[str, Any], bridges: list[Bridge]) -> list[Chain]:
    """decks.json → Chain 목록. 원천 다리 목록과 어긋나면 dem 단계를 다시 돌리라고 멈춘다."""
    if not data or data.get("version") != DECKS_VERSION:
        raise SystemExit("decks.json 형식이 다릅니다 → dem 단계를 다시 실행하세요.")
    out = []
    for r in data["chains"]:
        members = []
        for m in r["members"]:
            j = int(m["j"])
            if j >= len(bridges) or bridges[j].osm_id != int(m["osm_id"]):
                raise SystemExit("decks.json 이 지금 다리 원천과 다릅니다 → dem 단계를 다시 실행하세요.")
            members.append((j, bool(m["reversed"])))
        ch = Chain(members, LineString(r["line"]), [tuple(b) for b in r["bounds_x"]], r["ends"],
                   np.asarray(r["x"], dtype=np.float64), np.asarray(r["top"], dtype=np.float64), r["info"])
        out.append(ch)
    return out


# --------------------------------------------------------------------------- 실행
def run(cfg: dict[str, Any], area: str, args=None) -> None:
    ap = area_paths(cfg, area).ensure()
    tiles = area_tiles(cfg, area)
    bounds = area_bounds(cfg, area)
    rc = cfg["roads"]
    px, ss = int(rc.get("mask_px", 1024)), int(rc.get("mask_supersample", 4))
    ts = float(cfg["tile_size_m"])
    prov = sources.provenance(sources.select(cfg, "roads"))
    interim = bool(prov and prov["interim"])

    # 1) 도로 마스크
    feats, st = mask_features(cfg, area)
    tree = STRtree([f.polygon for f in feats])
    n_mask = 0
    cover = {}
    for t in tiles:
        d = ap.tile_dir(t.name)
        tb = box(*t.bounds)
        hits = [feats[i] for i in tree.query(tb, predicate="intersects")]
        if not hits:
            (d / "roads_mask.png").unlink(missing_ok=True)
            update_tile_json(d, t, cfg, "roads", None)
            continue
        img = mask_tile([(f.channel, f.polygon.intersection(tb.buffer(2.0))) for f in hits], t.e0, t.n0, ts, px, ss)
        Image.fromarray(img, "RGB").save(d / "roads_mask.png", optimize=False)
        segs = len({(f.osm_id, f.line.wkb) for f in hits if f.line.intersects(tb)})
        update_tile_json(d, t, cfg, "roads", {"mask": "roads_mask.png", "segments": segs, "interim": interim})
        cover[t.name] = [round(float(img[:, :, c].mean() / 255.0), 4) for c in range(3)]
        n_mask += 1
    print(f"  도로 마스크 {n_mask}타일, 면 {len(feats)}개(다리 {st['bridge_skipped']}·터널 {st['tunnel_skipped']}·"
          f"다른 등급 {st['class_skipped']} 제외)")
    # 폭 이상값(06a): 거른 width·lanes 태그 목록(원천 확인용)
    wo = width_outliers(cfg, area)
    import csv
    with open(ap.reports / "width_outliers.csv", "w", newline="", encoding="utf-8-sig") as fh:
        cols = ["osm_id", "layer", "class", "bridge", "rejected", "width_used_m", "source_used", "class_default_m", "e", "n", "in_area"]
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        wr.writerows(wo)
    print(f"  폭 이상값(태그 > 등급 기본값 × {float(rc.get('width_outlier_ratio', 3.0)):g}) 거름 {len(wo)}건"
          + (": " + ", ".join(f"{w['osm_id']} {w['class']} {w['rejected']} → {w['width_used_m']} m({w['source_used']})" for w in wo[:8]) if wo else "")
          + " → reports/width_outliers.csv")

    # 2) 교량: 상판 높이는 dem 단계가 정했다(decks.json). 여기서는 메시와 tile.json·보고서만 만든다.
    bridges = load_bridges(cfg, area)
    dk = read_json(ap.interim / "decks.json", None)
    if dk is None:
        raise SystemExit("dem 단계를 먼저 실행하세요(data/interim/<area>/decks.json 없음).")
    chains = load_decks(dk, bridges)
    terrain = TerrainSampler(cfg, area)

    def inside(j: int) -> bool:
        c = bridges[j].geometry.interpolate(0.5, normalized=True)
        return bounds[0] <= c.x < bounds[2] and bounds[1] <= c.y < bounds[3]
    thick = float(rc.get("deck_thickness_m", 0.8))
    ph, pt = float(rc.get("parapet_height_m", 1.0)), float(rc.get("parapet_thickness_m", 0.25))
    ox, oy = cfg["origin"]["easting"], cfg["origin"]["northing"]
    per_tile: dict[str, list] = {t.name: [] for t in tiles}
    tile_of = {t.name: t for t in tiles}
    report = []
    names: dict[str, int] = {}
    for ch in chains:
        assert ch.x is not None and ch.top is not None
        bp = profile_breakpoints(ch.x, ch.top, 0.005)      # 종단이 꺾이는 곳(메시 직선 구간이 바닥을 깎지 않게)
        for (j, rev), (a, b) in zip(ch.members, ch.bounds_x):
            br = bridges[j]
            c = br.geometry.interpolate(0.5, normalized=True)
            ie = int((c.x - bounds[0]) // ts)
            jn = int((c.y - bounds[1]) // ts)
            if not inside(j):
                continue
            t = tile_of[f"Tile_{(bounds[0] + ie * ts) // 1000:.0f}_{(bounds[1] + jn * ts) // 1000:.0f}"]
            vx = [ch.line.project(_pt(p)) for p in br.geometry.coords]
            xs = np.unique(np.concatenate([np.clip(vx, a, b), a + resample_stations(b - a, 4.0),
                                           bp[(bp > a) & (bp < b)]]))
            xs = xs[np.concatenate([[True], np.diff(xs) > 0.05])]
            xy = _coords(ch.line, xs)
            top = np.interp(xs, ch.x, ch.top)
            if rev:                             # way 방향으로 되돌린다(노드 이름·면 방향은 상관없지만 보기 좋게)
                xy, top = xy[::-1], top[::-1]
            V, Nn, F, _, _ = deck_mesh(xy - [t.e0, t.n0], top, br.width_m, thick, ph, pt)
            base = f"BR_w{br.osm_id}"
            k = names.get(base, 0)
            names[base] = k + 1
            node = base if k == 0 else f"{base}_{k}"
            ring = footprint_ring(xy, br.width_m) - [ox, oy]
            item = {"node": node, "osm_id": f"w{br.osm_id}", "kind": br.kind, "name": br.name,
                    "length_m": round(float(br.geometry.length), 1), "width_m": br.width_m,
                    "deck_min_m": round(float(top.min()), 2), "deck_max_m": round(float(top.max()), 2),
                    "deck_thickness_m": thick,
                    "footprint_xz": [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in ring]}
            per_tile[t.name].append((node, Primitive(position=V, normal=Nn, indices=F, material=f"M_bridge_{br.kind}"), item))
        # 최종 지형(상판 밑을 낮춘 RAW)으로 끝·묻힘을 다시 잰다
        pts = _coords(ch.line, ch.x)
        tg = terrain.height(pts[:, 0], pts[:, 1])
        ends = []
        for end in ch.ends:
            e2 = dict(end)
            e2["terrain_final_m"] = round(float(terrain.height(end["e"], end["n"])[0]), 2)
            ends.append(e2)
        info = dict(ch.info)
        step = ch.x[1] - ch.x[0] if len(ch.x) > 1 else 0
        info["buried_m"] = round(float((np.isfinite(tg) & (tg > ch.top + 0.3)).sum() * step), 1)
        report.append({"members": [f"w{bridges[j].osm_id}" for j, _ in ch.members],
                       "reversed": [bool(r) for _, r in ch.members],
                       "kinds": sorted({bridges[j].kind for j, _ in ch.members}),
                       "names": sorted({bridges[j].name for j, _ in ch.members if bridges[j].name}),
                       "widths_m": [bridges[j].width_m for j, _ in ch.members],
                       "length_m": round(ch.line.length, 1), "ends": ends, "taper": list(end_tapers(ch)),
                       "deck_min_m": round(float(ch.top.min()), 2), "deck_max_m": round(float(ch.top.max()), 2),
                       **info,
                       "profile": [[round(float(xv), 1), round(float(hv), 2)]
                                   for xv, hv in zip(ch.x[::5], ch.top[::5])] + [[round(float(ch.x[-1]), 1), round(float(ch.top[-1]), 2)]]})
    n_nodes = 0
    for t in tiles:
        d = ap.tile_dir(t.name)
        rows = per_tile[t.name]
        if not rows:
            (d / "bridges.glb").unlink(missing_ok=True)
            update_tile_json(d, t, cfg, "bridges", None)
            continue
        n = write_glb_nodes(d / "bridges.glb", [(node, [prim]) for node, prim, _ in rows])
        update_tile_json(d, t, cfg, "bridges", {"file": "bridges.glb", "interim": interim,
                                                "items": [it for _, _, it in rows]})
        n_nodes += n
    warns = list(dk.get("warnings", []))
    write_json(ap.reports / "bridges.json", {"chains": report, "warnings": warns, "parallel": dk.get("parallel", []),
                                             "under_deck": dk.get("under_deck", {}),
                                             "share_iterations": dk.get("share_iterations"),
                                             "outside_area": dk.get("outside_area", []),
                                             "mask_cover": cover})
    kinds = {}
    for rows in per_tile.values():
        for _, _, it in rows:
            kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    print(f"  교량 {n_nodes}개 {kinds}(사슬 {len(chains)}개, 범위 밖 중심 {len(dk.get('outside_area', []))}개 제외), "
          f"경고 {len(warns)}건, 나란한 쌍 {len(dk.get('parallel', []))}개(공유 반복 {dk.get('share_iterations')}회)")
    for p in dk.get("parallel", []):
        print(f"    = {p['a']} | {p['b']}: {p['parallel_m']} m, 간격 {p['dist_m']} m, 같은 단면 높이 차 "
              f"{p['step_before_max_m']} -> {p['step_after_max_m']} m(0.05 m 넘는 곳 {p['step_after_over_0_05_m']} m)")
    for w in warns:
        print(f"    ! {w}")


def _pt(p):
    from shapely.geometry import Point
    return Point(p[0], p[1])
