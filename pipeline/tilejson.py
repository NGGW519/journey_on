"""타일별 tile.json 읽고 쓰기. 각 단계는 자기 키만 갱신한다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import read_json, write_json
from .grid import Tile


def update_tile_json(tile_dir: Path, tile: Tile, cfg: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    path = Path(tile_dir) / "tile.json"
    tj = read_json(path, None) or {
        "name": tile.name, "e0": tile.e0, "n0": tile.n0, "size_m": tile.size,
        "unity_position": tile.unity_position(cfg["origin"]),
        "dem": None, "ortho": None, "buildings": None, "water": None, "roads": None, "bridges": None,
    }
    tj[key] = value
    write_json(path, tj)
    return tj
