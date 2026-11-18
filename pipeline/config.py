"""설정 로드와 경로 규약. 모든 값은 config/project.yaml 에서 온다."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "project.yaml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class AreaPaths:
    area: str
    raw: Path
    interim: Path
    out: Path

    @property
    def tiles(self) -> Path:
        return self.out / "tiles"

    @property
    def reports(self) -> Path:
        return self.out / "reports"

    def tile_dir(self, tile_name: str) -> Path:
        return self.tiles / tile_name

    def ensure(self) -> "AreaPaths":
        for p in (self.raw, self.interim, self.out, self.tiles, self.reports):
            p.mkdir(parents=True, exist_ok=True)
        return self


def area_paths(cfg: dict[str, Any], area: str) -> AreaPaths:
    p = cfg["paths"]
    return AreaPaths(
        area=area,
        raw=ROOT / p["raw"],
        interim=ROOT / p["interim"] / area,
        out=ROOT / p["out"] / area,
    )


def area_cfg(cfg: dict[str, Any], area: str) -> dict[str, Any]:
    try:
        a = cfg["areas"][area]
    except KeyError as e:
        raise SystemExit(f"알 수 없는 범위: {area} (config/project.yaml areas 확인)") from e
    if not a.get("bounds"):
        raise SystemExit(f"범위 {area}의 bounds가 아직 정해지지 않았습니다(config/project.yaml).")
    return a


def rel(path: Path) -> str:
    """프로젝트 루트 기준 상대 경로(manifest 기록용, 슬래시 고정)."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def read_json(path: Path, default: Any = None) -> Any:
    if not Path(path).exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
