"""내보내기 단계: tile.json 들을 모아 Unity 가 읽을 manifest.json 과 axis_test.glb 를 만든다.

manifest_version 2(계약 C1~C4): 최상위 `facade`(외벽 UV 칸 크기)·`skirt`(원경)·`water`(수면 메시 규칙 값, 06a 선택 필드),
타일 tile.json 의 `roads`·`bridges`. v1 필드는 그대로 둔다.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from . import sources
from .buildings import facade_spec
from .config import area_cfg, area_paths, read_json, write_json
from .grid import area_tiles
from .meshio import axis_test_meshes, write_glb
from .zones import no_build_polygons

MANIFEST_VERSION = 2


def water_spec(cfg: dict[str, Any]) -> dict[str, float]:
    """manifest 최상위 `water`(v2 선택 필드, 06a): Unity 가 config/project.yaml 을 직접 읽지 않게 수면 메시 규칙 값을 싣는다.
    shore_overlap_m = 수면 폴리곤을 넓힌 폭(C3 교량 여유 = 이 값 + 0.5 m), shore_clip_above_m = 보이는 수면 자르기 기준."""
    wc = cfg["water"]
    return {"shore_overlap_m": float(wc["shore_overlap_m"]), "shore_clip_above_m": float(wc.get("shore_clip_above_m", 0.0))}


def find_spawn(cfg: dict[str, Any], area: str) -> dict[str, Any] | None:
    """config.spawn.near 에서 가장 가까운, 물가에서 min_shore_distance_m 이상 떨어진 육지 표본."""
    import numpy as np
    import rasterio
    from rasterio.features import rasterize
    from scipy import ndimage
    from shapely.ops import unary_union

    from .dem import water_polygons
    from .grid import area_bounds

    sp = cfg.get("spawn")
    ap = area_paths(cfg, area)
    dem_path = ap.interim / "dem_samples.tif"
    if not sp or not dem_path.exists():
        return None
    from rasterio.windows import from_bounds
    from rasterio.windows import transform as win_transform

    ex, ny = sp["near"]
    half = sp["search_radius_m"] + sp["min_shore_distance_m"] + 20.0
    with rasterio.open(dem_path) as src:
        win = from_bounds(ex - half, ny - half, ex + half, ny + half, src.transform).round_offsets().round_lengths()
        z = src.read(1, window=win, boundless=True, fill_value=np.nan)
        T = win_transform(win, src.transform)
    polys = water_polygons(cfg, area, area_bounds(cfg, area))
    wet = np.zeros(z.shape, dtype=bool)
    if polys:
        wu = unary_union(polys).buffer(cfg["water"]["shore_overlap_m"])
        wet = rasterize([(wu, 1)], out_shape=z.shape, transform=T, fill=0, dtype="uint8").astype(bool)
    px = T.a
    dist_to_water = ndimage.distance_transform_edt(~wet) * px
    gy, gx = np.gradient(z, px)
    ok = (dist_to_water >= sp["min_shore_distance_m"]) & (np.hypot(gx, gy) < 0.35) & ~np.isnan(z)  # 경사 19° 미만
    col, row = ~T @ (ex, ny)
    rr, cc = np.mgrid[0:z.shape[0], 0:z.shape[1]]
    d = np.hypot((rr - row) * px, (cc - col) * px)
    d[~ok] = np.inf
    d[d > sp["search_radius_m"]] = np.inf
    if not np.isfinite(d).any():
        return None
    r, c = np.unravel_index(np.argmin(d), d.shape)
    e, n = T @ (c + 0.5, r + 0.5)
    lx, ly = sp["look_at"]
    yaw = float(np.degrees(np.arctan2(lx - e, ly - n)))   # Unity Y 회전: 0 = 북(+Z), 90 = 동(+X)
    return {"e": round(float(e), 1), "n": round(float(n), 1), "ground_m": round(float(z[r, c]), 2),
            "yaw_deg": round(yaw, 1), "shore_distance_m": round(float(dist_to_water[r, c]), 1),
            "from_near_m": round(float(d[r, c]), 1)}


def run(cfg: dict[str, Any], area: str, args=None) -> None:
    a = area_cfg(cfg, area)
    ap = area_paths(cfg, area).ensure()
    tiles = []
    n_build = n_bridge = n_roads = 0
    for t in area_tiles(cfg, area):
        tj = read_json(ap.tile_dir(t.name) / "tile.json", None)
        if tj is None:
            continue
        n_build += (tj.get("buildings") or {}).get("count", 0)
        n_bridge += len((tj.get("bridges") or {}).get("items", []))
        n_roads += 1 if tj.get("roads") else 0
        tiles.append({"name": t.name, "e0": t.e0, "n0": t.n0,
                      "unity_position": t.unity_position(cfg["origin"]),
                      "dir": f"tiles/{t.name}"})
    write_glb(ap.out / "axis_test.glb", axis_test_meshes())
    bstats = read_json(ap.reports / "buildings_stats.json", {})
    # waterways: 경사 수면 흐름 방향(D-007 하류 단조)에 쓴다(05a부터) → 출력 provenance 에도 싣는다(06a)
    prov = {layer: sources.provenance(sources.select(cfg, layer))
            for layer in ("dem", "ortho", "buildings", "water_areas", "waterways", "islands", "roads", "railways",
                          "skirt_water_areas")}
    sk = read_json(ap.reports / "skirt.json", None)
    skirt = None
    if sk and (ap.out / sk["file"]).exists():
        # 원경은 Copernicus 원천만 쓴다(공식 DEM 이 와도 범위 밖은 덮지 않을 수 있음) → interim 유지
        skirt = {"file": sk["file"], "bounds": sk["bounds"], "margin_m": sk["margin_m"],
                 "spacing_m": sk["spacing_m"], "inner_overlap_m": sk["inner_overlap_m"], "interim": True}
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "area": area,
        "title": a.get("title", area),
        "crs": cfg["crs"],
        "origin": cfg["origin"],
        "tile_size_m": cfg["tile_size_m"],
        "heightmap": cfg["heightmap"],
        "gltf_axes": "x=-east,y=up,z=north (glTFast flips x on import)",
        "axis_test": "axis_test.glb",
        "no_build_zones": [{"name": n, "bounds": [round(v, 1) for v in g.bounds]}
                           for n, g in no_build_polygons(cfg, area)],
        "facade": facade_spec(cfg),
        "water": water_spec(cfg),
        "skirt": skirt,
        "tiles": tiles,
        "spawn": find_spawn(cfg, area),
        "provenance": prov,
        "interim_layers": sorted(k for k, v in prov.items() if v and v["interim"]),
        "attributions": sorted({s["attribution"] for v in prov.values() if v for s in v["sources"] if s.get("attribution")}),
        "stats": {"tiles": len(tiles), "buildings": n_build, "bridges": n_bridge, "roads_tiles": n_roads,
                  "floors_default_ratio": bstats.get("floors_default_ratio")},
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    write_json(ap.out / "manifest.json", manifest)
    print(f"  manifest v{MANIFEST_VERSION}: 타일 {len(tiles)}개, 건물 {n_build}동, 교량 {n_bridge}개, 도로 마스크 {n_roads}타일, "
          f"원경 {'있음' if skirt else '없음'}, interim {manifest['interim_layers']}")
    print(f"  스폰: {manifest['spawn']}")
