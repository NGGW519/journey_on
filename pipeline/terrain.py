"""최종 지형(타일 RAW) 표본: Unity Terrain 과 같은 이중선형 보간을 월드 좌표(E, N)로 쓴다.

교량 상판 끝 높이(C3), 원경 스커트 경계(C4), validate 가 같은 함수를 쓴다.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .config import area_paths
from .grid import area_bounds
from .heightmap import decode, sample_spacing


def raw_bilinear(Zs: np.ndarray, x: np.ndarray, y: np.ndarray, step: float) -> np.ndarray:
    """타일 RAW(첫 행 = 남쪽) 이중선형 표본. x, y 는 타일 로컬 m(Unity Terrain 과 같은 보간)."""
    n = Zs.shape[0]
    fx = np.clip(np.asarray(x, dtype=np.float64) / step, 0, n - 1 - 1e-9)
    fy = np.clip(np.asarray(y, dtype=np.float64) / step, 0, n - 1 - 1e-9)
    i0, j0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    tx, ty = fx - i0, fy - j0
    return ((Zs[j0, i0] * (1 - tx) + Zs[j0, i0 + 1] * tx) * (1 - ty)
            + (Zs[j0 + 1, i0] * (1 - tx) + Zs[j0 + 1, i0 + 1] * tx) * ty)


def quantize(z: np.ndarray, cfg: dict[str, Any]) -> np.ndarray:
    """RAW 인코딩(uint16)을 거친 값과 같은 표고(heightmap.encode → decode 왕복)."""
    hm = cfg["heightmap"]
    base, rng = hm["base_m"], hm["range_m"]
    norm = (np.asarray(z) - base) / rng                     # encode 와 같은 dtype·연산 순서(반올림 경계 일치)
    q = np.clip(np.round(norm * 65535.0), 0, 65535).astype("<u2").astype(np.float64)
    return base + q / 65535.0 * rng


def raw_mosaic(cfg: dict[str, Any], area: str) -> np.ndarray | None:
    """범위 전체 타일 RAW 를 한 장으로(첫 행 = 남쪽, 표본 (r, c) = (minx + c·간격, miny + r·간격)). 이웃 타일이 공유하는
    가장자리 표본은 같은 값이다. 타일 RAW 가 하나라도 없으면 None. Unity Terrain 표본 그대로라 이 격자 위 이중선형이
    Unity 지형이다(수면 자르기, D-007)."""
    ap = area_paths(cfg, area)
    minx, miny, maxx, maxy = area_bounds(cfg, area)
    ts = float(cfg["tile_size_m"])
    n = int(cfg["heightmap"]["resolution"]) - 1
    nx, ny = int(round((maxx - minx) / ts)), int(round((maxy - miny) / ts))
    out = np.empty((ny * n + 1, nx * n + 1), dtype=np.float64)
    for j in range(ny):
        for i in range(nx):
            p = ap.tile_dir(f"Tile_{int(minx + i * ts) // 1000}_{int(miny + j * ts) // 1000}") / "heightmap.raw"
            if not p.exists():
                return None
            out[j * n:(j + 1) * n + 1, i * n:(i + 1) * n + 1] = decode(p.read_bytes(), cfg)
    return out


class TerrainSampler:
    """data/out/<area>/tiles/*/heightmap.raw 를 필요할 때 읽어 월드 좌표 표고를 돌려준다. 범위 밖은 NaN.

    `from_mosaic` 로 만들면 RAW 를 쓰기 전 범위 모자이크(북쪽이 첫 행, 표본점 = 픽셀 중심)를 RAW 와 같은
    양자화·이중선형으로 잰다(dem 단계가 교량 상판 높이를 정할 때). 타일은 모자이크를 자른 것이므로 값이 같다.
    """

    def __init__(self, cfg: dict[str, Any], area: str):
        self.cfg = cfg
        self.ap = area_paths(cfg, area)
        self.bounds = area_bounds(cfg, area)
        self.ts = float(cfg["tile_size_m"])
        self.step = sample_spacing(cfg)
        self._cache: dict[tuple[int, int], np.ndarray | None] = {}
        self._mosaic: np.ndarray | None = None

    @classmethod
    def from_mosaic(cls, cfg: dict[str, Any], area: str, z: np.ndarray) -> "TerrainSampler":
        s = cls(cfg, area)
        s._mosaic = quantize(z, cfg)
        return s

    def _mosaic_height(self, E: np.ndarray, N: np.ndarray) -> np.ndarray:
        Z = self._mosaic
        assert Z is not None
        minx, _, _, maxy = self.bounds
        H, W = Z.shape
        fx = np.clip((E - minx) / self.step, 0, W - 1 - 1e-9)
        fy = np.clip((maxy - N) / self.step, 0, H - 1 - 1e-9)
        i0, j0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
        tx, ty = fx - i0, fy - j0
        return ((Z[j0, i0] * (1 - tx) + Z[j0, i0 + 1] * tx) * (1 - ty)
                + (Z[j0 + 1, i0] * (1 - tx) + Z[j0 + 1, i0 + 1] * tx) * ty)

    def _tile(self, e0: int, n0: int) -> np.ndarray | None:
        key = (e0, n0)
        if key not in self._cache:
            p = self.ap.tile_dir(f"Tile_{e0 // 1000}_{n0 // 1000}") / "heightmap.raw"
            self._cache[key] = decode(p.read_bytes(), self.cfg) if p.exists() else None
        return self._cache[key]

    def inside(self, E, N) -> np.ndarray:
        minx, miny, maxx, maxy = self.bounds
        E, N = np.asarray(E, dtype=np.float64), np.asarray(N, dtype=np.float64)
        return (E >= minx) & (E <= maxx) & (N >= miny) & (N <= maxy)

    def height(self, E, N) -> np.ndarray:
        E = np.atleast_1d(np.asarray(E, dtype=np.float64))
        N = np.atleast_1d(np.asarray(N, dtype=np.float64))
        out = np.full(E.shape, np.nan)
        minx, miny, maxx, maxy = self.bounds
        ok = self.inside(E, N)
        if not ok.any():
            return out
        if self._mosaic is not None:
            out[ok] = self._mosaic_height(E[ok], N[ok])
            return out
        ts = self.ts
        # 경계 위 점(E = maxx)은 마지막 타일에 넣는다(가장자리 표본은 이웃과 같으므로 어느 쪽이든 같은 값)
        ie = np.clip(np.floor((E[ok] - minx) / ts), 0, round((maxx - minx) / ts) - 1).astype(int)
        jn = np.clip(np.floor((N[ok] - miny) / ts), 0, round((maxy - miny) / ts) - 1).astype(int)
        idx = np.nonzero(ok)[0]
        for a, b in sorted(set(zip(ie.tolist(), jn.tolist()))):
            sel = (ie == a) & (jn == b)
            e0, n0 = int(minx + a * ts), int(miny + b * ts)
            Zs = self._tile(e0, n0)
            if Zs is None:
                continue
            k = idx[sel]
            out[k] = raw_bilinear(Zs, E[k] - e0, N[k] - n0, self.step)
        return out
