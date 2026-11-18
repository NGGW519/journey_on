"""계약 C4(원경 스커트, D-013) 순수 함수 고정: 고리 줄, 지퍼 삼각분할, 덩어리 나누기, 겹침 띠 높이."""
import numpy as np
import rasterio
from affine import Affine

from pipeline.skirt import (band_heights, chunk_loops, face_up, loop_points, ring_offsets, skirt_colors,
                            vertex_normals, zip_loops)

B = (0.0, 0.0, 400.0, 700.0)


def test_ring_offsets_spacing():
    offs = ring_offsets(1260, 300, 60, 120, 60)
    ds = [d for d, _ in offs]
    assert ds[:3] == [-60.0, -1.0, 0.0]
    assert ds[-1] == 1260.0
    near = [sp for d, sp in offs if 0 < d <= 300]
    far = [sp for d, sp in offs if d > 300]
    assert set(near) == {60.0} and set(far) == {120.0}
    assert np.allclose(np.diff([d for d in ds if d > 0 and d <= 300]), 60)
    assert np.allclose(np.diff([d for d in ds if d >= 300]), 120)


def test_loop_points_corners_and_spacing():
    P, t = loop_points(B, 60, 60)
    assert np.allclose(P[0], [-60, -60])
    corners = P[np.isclose(t, np.round(t))]
    assert np.allclose(corners, [[-60, -60], [460, -60], [460, 760], [-60, 760]])
    seg = np.hypot(*np.diff(np.vstack([P, P[:1]]), axis=0).T)
    assert seg.max() <= 60 + 1e-9


def _ring_area(F, V):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])).sum()


def test_zip_loops_fill_ring_exactly():
    PA, tA = loop_points(B, 0, 3.9)       # 촘촘한 경계 줄
    PB, tB = loop_points(B, 60, 60)
    V = np.vstack([PA, PB])
    F = zip_loops(tA, tB, 0, len(PA))
    assert len(F) == len(PA) + len(PB)                       # 띠 삼각형 수 = 두 줄 정점 수의 합
    area = (400 + 120) * (700 + 120) - 400 * 700
    assert np.isclose(_ring_area(F, V), area, rtol=1e-9)     # 겹침·빈틈 없이 고리를 덮는다
    F2 = face_up(np.column_stack([V, np.zeros(len(V))]), F)
    a, b, c = V[F2[:, 0]], V[F2[:, 1]], V[F2[:, 2]]
    cr = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    assert (cr >= 0).all()
    # 변마다 정확히 두 삼각형(안쪽 줄 변·바깥 줄 변은 하나): 열린 변 = 두 줄의 변
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    _, cnt = np.unique(e, axis=0, return_counts=True)
    assert (cnt <= 2).all() and (cnt == 1).sum() == len(PA) + len(PB)


def test_chunk_loops_vertex_limit_and_shared_loop():
    counts = [400, 2000, 2000, 380, 390, 400, 410, 420, 430, 440, 450, 460]
    ch = chunk_loops(counts, 4500)
    assert len(ch) >= 3
    for c in ch:
        assert sum(counts[k] for k in c) <= 4500
    for a, b in zip(ch[:-1], ch[1:]):
        assert a[-1] == b[0]                                 # 이웃 덩어리는 줄 하나를 함께 가진다
    assert sorted({k for c in ch for k in c}) == list(range(len(counts)))
    import pytest
    with pytest.raises(ValueError):                         # 이웃한 두 줄만으로 상한을 넘으면 나눌 수 없다
        chunk_loops([100, 3000, 3000, 100], 4500)


def test_band_heights_below_terrain(tmp_path):
    # 합성 지형: 동쪽으로 오르는 비탈 + 경계 근처 구덩이. 겹침 띠 정점은 붙은 삼각형 범위의 최저 지형 − 2 m 이하
    px = 1.0
    W, H = 420, 720
    T = Affine(px, 0, -10, 0, -px, 710)
    xs = -10 + (np.arange(W) + 0.5) * px
    ys = 710 - (np.arange(H) + 0.5) * px
    gx, gy = np.meshgrid(xs, ys)
    z = (50 + 0.3 * gx).astype(np.float32)
    z[(np.abs(gx - 30) < 5) & (np.abs(gy - 30) < 5)] -= 12.0
    p = tmp_path / "dem.tif"
    with rasterio.open(p, "w", driver="GTiff", width=W, height=H, count=1, dtype="float32", crs="EPSG:5186",
                       transform=T) as dst:
        dst.write(z, 1)
    PI, tI = loop_points(B, -60, 60)
    PE, tE = loop_points(B, -1, 3.9)
    PB, tB = loop_points(B, 0, 3.9)
    V = np.vstack([PI, PE, PB])
    F = np.vstack([zip_loops(tI, tE, 0, len(PI)), zip_loops(tE, tB, len(PI), len(PI) + len(PE))])
    sel = np.zeros(len(V), dtype=bool)
    sel[:len(PI) + len(PE)] = True
    h = band_heights(V, F, sel, p, 2.0)
    # 각 정점 높이 ≤ 그 자리 지형 − 2
    col = np.clip(((V[sel, 0] + 10) / px).astype(int), 0, W - 1)
    row = np.clip(((710 - V[sel, 1]) / px).astype(int), 0, H - 1)
    assert (h <= z[row, col] - 2.0 + 1e-6).all()
    # 구덩이(지형 38 m 근처)를 덮는 삼각형의 정점은 구덩이 바닥 − 2 m 이하
    near = np.hypot(V[sel, 0] - 30, V[sel, 1] - 30) < 20
    assert (h[near] <= z.min() + 0.3 * 40 - 2.0).all()


def test_normals_and_colors():
    V = np.array([[0, 0, 0.0], [10, 0, 0], [0, 10, 10]])
    F = np.array([[0, 1, 2]])
    n = vertex_normals(V, face_up(V, F))
    assert np.allclose(n[0], [0, -1 / np.sqrt(2), 1 / np.sqrt(2)])
    col = skirt_colors(np.array([50.0, 400.0, 1000.0]), np.array([1.0, 1.0, 0.5]), np.array([False, False, True]))
    assert col.shape == (3, 3) and (col >= 0).all() and (col <= 1).all()
    assert not np.allclose(col[0], col[1])                   # 저지대 ↔ 산림 색이 다르다
