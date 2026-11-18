"""1km 타일 격자. 타일 이름은 남서 모서리의 km 좌표: Tile_{E km}_{N km}."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

from shapely.geometry import box

from .config import area_cfg

_NAME = re.compile(r"^Tile_(-?\d+)_(-?\d+)$")


@dataclass(frozen=True, order=True)
class Tile:
    e0: int
    n0: int
    size: int = 1000

    @property
    def name(self) -> str:
        return f"Tile_{self.e0 // 1000}_{self.n0 // 1000}"

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return (self.e0, self.n0, self.e0 + self.size, self.n0 + self.size)

    @property
    def polygon(self):
        return box(*self.bounds)

    def unity_position(self, origin: dict[str, float]) -> list[float]:
        """타일 남서 모서리의 Unity 좌표 [X, Y, Z] (X=동, Z=북)."""
        return [float(self.e0 - origin["easting"]), 0.0, float(self.n0 - origin["northing"])]


def tile_from_name(name: str, size: int = 1000) -> Tile:
    m = _NAME.match(name)
    if not m:
        raise ValueError(f"타일 이름 형식이 아닙니다: {name}")
    return Tile(int(m.group(1)) * 1000, int(m.group(2)) * 1000, size)


def tiles_for_bounds(bounds: Iterable[float], size: int = 1000) -> list[Tile]:
    """bounds [minE, minN, maxE, maxN]를 덮는 격자 타일(격자에 맞춰 바깥으로 넓힘)."""
    minx, miny, maxx, maxy = bounds
    e_start = math.floor(minx / size) * size
    n_start = math.floor(miny / size) * size
    e_end = math.ceil(maxx / size) * size
    n_end = math.ceil(maxy / size) * size
    return [
        Tile(e, n, size)
        for n in range(n_start, n_end, size)
        for e in range(e_start, e_end, size)
    ]


def area_tiles(cfg: dict[str, Any], area: str) -> list[Tile]:
    a = area_cfg(cfg, area)
    return tiles_for_bounds(a["bounds"], cfg["tile_size_m"])


def area_bounds(cfg: dict[str, Any], area: str) -> tuple[int, int, int, int]:
    """격자에 맞춘 범위 경계."""
    ts = area_tiles(cfg, area)
    return (
        min(t.e0 for t in ts),
        min(t.n0 for t in ts),
        max(t.e0 + t.size for t in ts),
        max(t.n0 + t.size for t in ts),
    )
