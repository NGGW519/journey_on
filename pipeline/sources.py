"""원천 데이터 목록(data/raw/manifest.json) 읽기·등록·선택.

레이어마다 공식(interim=false) 항목이 하나라도 있으면 그것만 쓰고, 없을 때만 interim 을 쓴다.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from .config import ROOT, read_json, rel, write_json

LAYERS = (
    "dem", "ortho", "buildings", "water_areas", "waterways",
    "islands", "roads", "railways", "boundaries",
    "skirt_water_areas",   # 원경 스커트(C4) 범위 수면(범위 + margin)
)


def manifest_path(cfg: dict[str, Any]) -> Path:
    return ROOT / cfg["paths"]["raw"] / "manifest.json"


def load(cfg: dict[str, Any]) -> dict[str, Any]:
    m = read_json(manifest_path(cfg), None)
    if m is None:
        m = {"version": 1, "layers": {}}
    m.setdefault("layers", {})
    return m


def save(cfg: dict[str, Any], m: dict[str, Any]) -> None:
    write_json(manifest_path(cfg), m)


def register(
    cfg: dict[str, Any],
    layer: str,
    path: Path,
    *,
    source: str,
    license: str,
    attribution: str = "",
    date: str = "",
    crs: str = "",
    interim: bool = False,
    kind: str = "",
    notes: str = "",
    url: str = "",
) -> dict[str, Any]:
    if layer not in LAYERS:
        raise SystemExit(f"레이어 이름이 올바르지 않습니다: {layer} (가능: {', '.join(LAYERS)})")
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise SystemExit(f"파일이 없습니다: {path}")
    m = load(cfg)
    entries = m["layers"].setdefault(layer, [])
    rp = rel(path)
    entries[:] = [e for e in entries if e["path"] != rp]  # 같은 경로는 덮어쓴다
    entry = {
        "path": rp,
        "source": source,
        "license": license,
        "attribution": attribution,
        "date": date,
        "crs": crs,
        "interim": interim,
        "kind": kind,
        "url": url,
        "notes": notes,
        "bytes": path.stat().st_size if path.is_file() else None,
        "registered_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    entries.append(entry)
    save(cfg, m)
    return entry


def select(cfg: dict[str, Any], layer: str) -> list[dict[str, Any]]:
    entries = load(cfg)["layers"].get(layer, [])
    official = [e for e in entries if not e.get("interim")]
    return official or [e for e in entries if e.get("interim")]


def abspath(entry: dict[str, Any]) -> Path:
    return ROOT / entry["path"]


def provenance(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None
    return {
        "interim": any(e.get("interim") for e in entries),
        "sources": [
            {k: e.get(k) for k in ("path", "source", "license", "attribution", "date", "kind", "url")}
            for e in entries
        ],
    }
