"""벡터 레이어 읽기: manifest 에서 고른 원천을 EPSG:5186 GeoDataFrame 으로."""
from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from . import sources
from .grid import area_bounds


def load_layer(cfg: dict[str, Any], area: str, layer: str, margin_m: float = 200.0) -> gpd.GeoDataFrame | None:
    entries = sources.select(cfg, layer)
    if not entries:
        return None
    minx, miny, maxx, maxy = area_bounds(cfg, area)
    clip = box(minx - margin_m, miny - margin_m, maxx + margin_m, maxy + margin_m)
    frames = []
    for e in entries:
        path = sources.abspath(e)
        gdf = gpd.read_file(path, engine="pyogrio")
        if gdf.crs is None:
            if not e.get("crs"):
                raise SystemExit(f"좌표계를 알 수 없습니다: {path} (register --crs 로 지정)")
            gdf = gdf.set_crs(e["crs"].split()[0])
        gdf = gdf.to_crs(cfg["crs"])
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
        gdf = gdf[gdf.intersects(clip)]
        gdf["_src_kind"] = e.get("kind", "")
        gdf["_src_path"] = e["path"]
        frames.append(gdf)
    if not frames:
        return None
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=cfg["crs"])
