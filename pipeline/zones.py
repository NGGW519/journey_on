"""건물 금지 구역(no_build_zones) 폴리곤. 섬 이름이 있으면 islands 레이어의 폴리곤, 없으면 bounds 사각형."""
from __future__ import annotations

from typing import Any

from shapely.geometry import box
from shapely.ops import unary_union

from .config import area_cfg
from .vectors import load_layer

ISLAND_MARGIN_M = 10.0


def no_build_polygons(cfg: dict[str, Any], area: str) -> list[tuple[str, Any]]:
    zones = area_cfg(cfg, area).get("no_build_zones") or []
    islands = None
    out = []
    for z in zones:
        geom = None
        if z.get("island"):
            if islands is None:
                islands = load_layer(cfg, area, "islands")
            if islands is not None and "name" in islands.columns:
                hit = islands[islands["name"] == z["island"]]
                if not hit.empty:
                    geom = unary_union(list(hit.geometry)).buffer(ISLAND_MARGIN_M)
        if geom is None and z.get("bounds"):
            geom = box(*z["bounds"])
        if geom is not None:
            out.append((z.get("name", ""), geom))
    return out
