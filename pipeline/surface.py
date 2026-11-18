"""2.5D 삼각형 면(수면 메시 등) 높이 표본: 점 (E, N) 을 덮는 삼각형의 높이(선형 보간).

수면 메시(water.glb, 원경 SKW)는 정점 높이를 선형으로 잇는다. 교량 상판의 '수면 + 0.2 m' 바닥(C3)과
원경 수면 아래 지면(C4)은 이 실제 면과 비교해야 하므로 래스터 대신 삼각형을 직접 잰다. 순수 함수(테스트 대상).
"""
from __future__ import annotations

import numpy as np


class TriSurface:
    """삼각형 묶음(V: (n, 3) 월드 E, N, 높이 / F: (m, 3)). 격자 칸(cell_m)마다 걸친 삼각형 목록을 둔다."""

    def __init__(self, V: np.ndarray, F: np.ndarray, cell_m: float = 10.0):
        self.V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
        self.F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
        self.cell = float(cell_m)
        if len(self.F) == 0:
            self.keys = np.zeros(0, dtype=np.int64)
            self.tris = np.zeros(0, dtype=np.int64)
            return
        P = self.V[self.F][:, :, :2]
        lo = np.floor(P.min(axis=1) / self.cell).astype(np.int64)
        hi = np.floor(P.max(axis=1) / self.cell).astype(np.int64)
        nx, ny = hi[:, 0] - lo[:, 0] + 1, hi[:, 1] - lo[:, 1] + 1
        cnt = nx * ny
        tri = np.repeat(np.arange(len(self.F)), cnt)
        k = np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt)   # 삼각형 안 칸 번호
        cx = lo[tri, 0] + k % nx[tri]
        cy = lo[tri, 1] + k // nx[tri]
        key = self._key(cx, cy)
        order = np.argsort(key, kind="stable")
        self.keys, self.tris = key[order], tri[order]

    @staticmethod
    def _key(cx, cy):
        return (np.asarray(cx, dtype=np.int64) + (1 << 30)) * (1 << 31) + (np.asarray(cy, dtype=np.int64) + (1 << 30))

    def __len__(self) -> int:
        return len(self.F)

    def height(self, E, N, mode: str = "max") -> np.ndarray:
        """점마다 덮는 삼각형 높이(여럿이면 mode = max|min), 없으면 NaN. 경계 위 점도 덮인 것으로 본다."""
        E = np.atleast_1d(np.asarray(E, dtype=np.float64))
        N = np.atleast_1d(np.asarray(N, dtype=np.float64))
        out = np.full(E.shape, np.nan)
        if len(self.keys) == 0 or E.size == 0:
            return out
        key = self._key(np.floor(E / self.cell), np.floor(N / self.cell))
        a = np.searchsorted(self.keys, key, "left")
        b = np.searchsorted(self.keys, key, "right")
        cnt = b - a
        if cnt.sum() == 0:
            return out
        q = np.repeat(np.arange(E.size), cnt)
        pos = np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt) + np.repeat(a, cnt)
        t = self.tris[pos]
        A, B, C = (self.V[self.F[t, i]] for i in range(3))
        x, y = E[q], N[q]
        det = (B[:, 1] - C[:, 1]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (A[:, 1] - C[:, 1])
        ok = np.abs(det) > 1e-12
        d = np.where(ok, det, 1.0)
        l1 = ((B[:, 1] - C[:, 1]) * (x - C[:, 0]) + (C[:, 0] - B[:, 0]) * (y - C[:, 1])) / d
        l2 = ((C[:, 1] - A[:, 1]) * (x - C[:, 0]) + (A[:, 0] - C[:, 0]) * (y - C[:, 1])) / d
        l3 = 1.0 - l1 - l2
        eps = 1e-9
        inside = ok & (l1 >= -eps) & (l2 >= -eps) & (l3 >= -eps)
        z = l1 * A[:, 2] + l2 * B[:, 2] + l3 * C[:, 2]
        q, z = q[inside], z[inside]
        if mode == "min":
            acc = np.full(E.shape, np.inf)
            np.minimum.at(acc, q, z)
        else:
            acc = np.full(E.shape, -np.inf)
            np.maximum.at(acc, q, z)
        hit = np.isfinite(acc)
        out[hit] = acc[hit]
        return out

    @classmethod
    def concat(cls, parts: list[tuple[np.ndarray, np.ndarray]], cell_m: float = 10.0) -> "TriSurface":
        """(V, F) 여러 개를 하나로."""
        Vs, Fs, off = [], [], 0
        for V, F in parts:
            V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
            Vs.append(V)
            Fs.append(np.asarray(F, dtype=np.int64).reshape(-1, 3) + off)
            off += len(V)
        if not Vs:
            return cls(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), cell_m)
        return cls(np.concatenate(Vs), np.concatenate(Fs), cell_m)
