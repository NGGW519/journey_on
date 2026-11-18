"""격자 단계: 타일 격자를 GeoJSON 으로 내보낸다(QGIS 확인용)."""
from __future__ import annotations

from typing import Any

import geopandas as gpd

from .config import area_paths
from .grid import area_tiles


def run(cfg: dict[str, Any], area: str, args=None) -> None:
    ap = area_paths(cfg, area).ensure()
    ts = area_tiles(cfg, area)
    gdf = gpd.GeoDataFrame(
        {"name": [t.name for t in ts], "e0": [t.e0 for t in ts], "n0": [t.n0 for t in ts],
         "unity_x": [t.e0 - cfg["origin"]["easting"] for t in ts],
         "unity_z": [t.n0 - cfg["origin"]["northing"] for t in ts]},
        geometry=[t.polygon for t in ts], crs=cfg["crs"])
    gdf.to_file(ap.interim / "grid.geojson", driver="GeoJSON")
    print(f"  타일 {len(ts)}개 → {ap.interim / 'grid.geojson'}")
