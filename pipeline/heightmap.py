"""높이맵 인코딩/디코딩과 범위 모자이크 ↔ 타일 슬라이스 규약(순수 함수, 테스트 대상)."""
from __future__ import annotations

from typing import Any

import numpy as np
from affine import Affine


def sample_spacing(cfg: dict[str, Any]) -> float:
    return cfg["tile_size_m"] / (cfg["heightmap"]["resolution"] - 1)


def mosaic_grid(cfg: dict[str, Any], bounds: tuple[float, float, float, float]) -> tuple[Affine, int, int]:
    """표본점 정렬 격자. 픽셀 중심 = 표본점, 첫 행 = 북쪽(래스터 관례)."""
    px = sample_spacing(cfg)
    minx, miny, maxx, maxy = bounds
    w = int(round((maxx - minx) / px)) + 1
    h = int(round((maxy - miny) / px)) + 1
    transform = Affine(px, 0.0, minx - px / 2, 0.0, -px, maxy + px / 2)
    return transform, w, h


def tile_window(cfg: dict[str, Any], bounds: tuple[float, float, float, float], e0: float, n0: float) -> tuple[slice, slice]:
    """모자이크(북쪽이 첫 행)에서 타일 하나의 행·열 범위. 가장자리 표본은 이웃과 공유한다."""
    res = cfg["heightmap"]["resolution"]
    step = res - 1
    ts = cfg["tile_size_m"]
    minx, _, _, maxy = bounds
    col0 = int(round((e0 - minx) / ts)) * step
    row0 = int(round((maxy - (n0 + ts)) / ts)) * step
    return slice(row0, row0 + res), slice(col0, col0 + res)


def encode(heights_north_up: np.ndarray, cfg: dict[str, Any]) -> bytes:
    """타일 표고(북쪽이 첫 행) → RAW 바이트(첫 행 = 남쪽, uint16 LE)."""
    hm = cfg["heightmap"]
    base, rng = hm["base_m"], hm["range_m"]
    norm = (heights_north_up[::-1, :] - base) / rng
    q = np.clip(np.round(norm * 65535.0), 0, 65535).astype("<u2")
    return q.tobytes()


def decode(raw: bytes, cfg: dict[str, Any]) -> np.ndarray:
    """RAW 바이트 → 표고(첫 행 = 남쪽)."""
    hm = cfg["heightmap"]
    res = hm["resolution"]
    q = np.frombuffer(raw, dtype="<u2").reshape(res, res).astype(np.float64)
    return hm["base_m"] + q / 65535.0 * hm["range_m"]


def hillshade(z: np.ndarray, px: float, azimuth: float = 315.0, altitude: float = 45.0) -> np.ndarray:
    """북쪽이 첫 행인 표고 배열의 음영기복(0..1)."""
    gy, gx = np.gradient(z, px)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = np.radians(azimuth), np.radians(altitude)
    hs = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    return np.clip(hs, 0, 1)
