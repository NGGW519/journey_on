"""교량 보완(2026-11-18, D-012) 순수 함수 고정: 수면 메시 표본, 수면 바닥·높이 공유 종단, 나란한 way 공유,
상판 밑 지형 낮추기, 지그재그 상판 감김, 원경 수면 아래 지면. 합성 데이터만 쓴다."""
import numpy as np
from affine import Affine
from shapely import contains_xy
from shapely.geometry import LineString

from pipeline.bridges import Bridge, deck_mesh, deck_profile, profile_breakpoints, resample_stations
from pipeline.config import load_config
from pipeline.dem import (DeckSpan, bilinear_support, deck_allowance, deck_footprint, lower_under_decks,
                          window_min)
from pipeline.roads import _Vtx, _key, _profile, bridge_chains, parallel_partners, share_decks
from pipeline.skirt import drop_under_water
from pipeline.surface import TriSurface

CFG = load_config()


# --------------------------------------------------------------------------- 수면 메시 표본
def test_trisurface_linear_and_outside():
    V = np.array([[0, 0, 10.0], [10, 0, 12.0], [10, 10, 14.0], [0, 10, 12.0]])
    F = np.array([[0, 1, 2], [0, 2, 3]])
    s = TriSurface(V, F, cell_m=3.0)
    h = s.height([5, 2, 9.5, 10, 11, -0.1], [5, 1, 0.5, 10, 5, 5])
    assert np.allclose(h[:4], [12.0, 10.6, 12.0, 14.0])        # 평면 z = 10 + 0.2x + 0.2y, 모서리 점 포함
    assert np.isnan(h[4]) and np.isnan(h[5])
    # 겹친 두 면: max / min
    V2 = np.vstack([V, V + [0, 0, 5.0]])
    F2 = np.vstack([F, F + 4])
    s2 = TriSurface(V2, F2)
    assert np.isclose(s2.height([5], [5])[0], 17.0) and np.isclose(s2.height([5], [5], mode="min")[0], 12.0)


# --------------------------------------------------------------------------- 종단: 수면 바닥, 높이 공유
def test_deck_profile_hard_floor_beats_grade():
    x = resample_stations(10.0, 1.0)
    floor = np.full(len(x), np.nan)
    floor[3:8] = 51.0                       # 좁은 물줄기 위: 수면 50.8 + 0.2
    top, info = deck_profile(x, 50.5, 50.5, floor + 1.3, 0.08, 20.0, floor=floor)
    assert (top[3:8] >= 51.0 - 1e-9).all()                     # 바닥은 경사 상한보다 앞선다
    assert info["floor_over_grade_m"] > 0 and info["clearance_short_m"] > 0
    assert np.isclose(top[0], 50.5) and np.isclose(top[-1], 50.5)


def test_deck_profile_share_is_followed_within_grade_without_ratchet():
    x = resample_stations(200.0, 1.0)
    share = np.full(len(x), 52.0)
    top, info = deck_profile(x, 50.0, 50.0, np.full(len(x), np.nan), 0.08, 20.0, share=share)
    mid = (x >= 30) & (x <= 170)
    assert np.allclose(top[mid], 52.0)                       # 가운데 = 짝 높이(다듬기 몫을 더하지 않음 → 번갈아 오르지 않는다)
    assert np.isclose(top[0], 50.0) and np.isclose(top[-1], 50.0)
    assert (np.abs(np.diff(top)) / np.diff(x)).max() <= 0.08 + 1e-9   # 끝에서 경사 상한으로만 오른다
    assert info["share_short_m"] > 1.9                        # 끝에서는 못 따른다(기록)


def test_profile_breakpoints_keep_floor_peak():
    x = resample_stations(40.0, 1.0)
    y = np.where(np.abs(x - 20) <= 1.0, 51.0, 50.0) + 0.001 * x
    bp = profile_breakpoints(x, y, 0.005)
    rec = np.interp(x, bp, np.interp(bp, x, y))
    assert np.abs(rec - y).max() <= 0.005 + 1e-12 and len(bp) < len(x)
    assert 0.0 in bp and 40.0 in bp


# --------------------------------------------------------------------------- 지그재그 계단 상판 감김
def test_deck_mesh_switchback_has_no_folded_faces():
    xy = np.array([[0.0, 0.0], [0.0, -1.0], [-5.0, -2.2], [-4.6, -3.5], [0.4, -2.4]])   # w1394969774 모양
    top = np.full(len(xy), 85.0)
    V, N, F, _, _ = deck_mesh(xy, top, 2.0, 0.8, 1.0, 0.25)
    tn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    zc = V[F][:, :, 2]
    flat = np.all(np.abs(zc - zc[:, :1]) < 1e-9, axis=1) & (np.linalg.norm(tn, axis=1) > 1e-9)
    bottom = flat & np.isclose(zc[:, 0], 84.2)
    deck = flat & np.isclose(zc[:, 0], 85.0)
    assert (tn[bottom, 2] < 0).all()                         # 밑면 삼각형은 모두 아래
    assert (tn[deck, 2] > 0).all()                           # 상판 윗면은 모두 위(구멍 없음)


# --------------------------------------------------------------------------- 상판 밑 지형
def test_window_min_and_allowance():
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    y = np.array([5.0, 4.0, 3.0, 4.0, 5.0])
    assert np.allclose(window_min(np.array([0.0, 1.5, 4.0]), x, y, 1.0), [4.0, 3.0, 4.0])
    xs = resample_stations(20.0, 1.0)
    top = np.full(len(xs), 60.0)
    A = deck_allowance(np.array([-1.0, 0.0, 1.0, 2.0, 3.0, 10.0, 20.0]), 20.0, xs, top, 0.8, 0.3, 3.0, (True, False),
                       eps=0.05, win_m=1.0)
    assert np.allclose(A[:3], 59.95)                         # 땅에 닿는 끝: 첫 win_m 은 윗면 − 0.05
    assert A[3] < 59.95 and np.isclose(A[4], 58.9)           # end_m 에서 밑면 − 0.3
    assert np.isclose(A[5], 58.9) and np.isclose(A[6], 58.9)  # 끝이 땅에 닿지 않는 쪽(물 위·다른 다리)은 바로 규칙


def _grid(n=120, px=1.0):
    T = Affine(px, 0.0, -px / 2, 0.0, -px, (n - 1) * px + px / 2)    # 표본 (c, r) = (c·px, (n−1−r)·px)
    return T, n


def _bilinear(Z, T, E, N):
    c = (np.asarray(E) - (T.c + T.a / 2)) / T.a
    r = (np.asarray(N) - (T.f + T.e / 2)) / T.e
    c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
    tx, ty = c - c0, r - r0
    return ((Z[r0, c0] * (1 - tx) + Z[r0, c0 + 1] * tx) * (1 - ty) + (Z[r0 + 1, c0] * (1 - tx) + Z[r0 + 1, c0 + 1] * tx) * ty)


def test_bilinear_support_covers_every_point():
    T, n = _grid(60, 0.9766)
    g = LineString([(10.3, 20.1), (40.7, 33.9)])
    fp = deck_footprint(g, 5.0)
    win, m = bilinear_support(fp, T, (n, n))
    r0, r1, c0, c1 = win
    rng = np.random.default_rng(1)
    E = rng.uniform(fp.bounds[0], fp.bounds[2], 4000)
    N = rng.uniform(fp.bounds[1], fp.bounds[3], 4000)
    k = contains_xy(fp, E, N)
    c = (E[k] - (T.c + T.a / 2)) / T.a
    r = (N[k] - (T.f + T.e / 2)) / T.e
    for dr in (0, 1):
        for dc in (0, 1):
            rr, cc = np.floor(r).astype(int) + dr, np.floor(c).astype(int) + dc
            assert m[rr - r0, cc - c0].all()


def test_lower_under_decks_no_poke_clearance_and_ends():
    T, n = _grid(120, 1.0)
    yy, xx = np.mgrid[0:n, 0:n]
    E, N = xx * 1.0, (n - 1 - yy) * 1.0
    z = (50.0 + 3.0 * np.exp(-((E - 60) ** 2 + (N - 60) ** 2) / 200.0)).astype(np.float32)   # 다리 가운데 언덕(관통)
    line = LineString([(30.0, 60.0), (90.0, 60.0)])
    x = resample_stations(60.0, 1.0)
    top = np.full(len(x), 51.0)
    top[0], top[-1] = 50.0, 50.0
    top = np.minimum(top, np.minimum(50.0 + 0.08 * x, 50.0 + 0.08 * (60 - x)))
    span = DeckSpan(line, x, top, [(line, 5.0)], (True, True), 0.8)
    out, st = lower_under_decks(z, T, [span], clear=0.3, end_m=3.0, slope=0.5)
    assert (out <= z + 1e-6).all() and st["lowered_max_m"] > 2.0
    fp = deck_footprint(line, 5.0)
    rng = np.random.default_rng(2)
    P = np.column_stack([rng.uniform(28, 92, 20000), rng.uniform(56, 64, 20000)])
    P = P[contains_xy(fp, P[:, 0], P[:, 1])]
    s = np.clip(P[:, 0] - 30.0, 0, 60)
    tz = _bilinear(out, T, P[:, 0], P[:, 1])
    tt = np.interp(s, x, top)
    assert (tz <= tt + 1e-4).all()                                     # 관통 0
    inner = (s >= 3.0 + 1.5) & (s <= 60 - 3.0 - 1.5)
    assert (tz[inner] <= tt[inner] - 0.8 - 0.3 + 1e-4).all()           # 끝 구간 밖: 밑면 − 0.3 이하
    end = _bilinear(out, T, [30.1], [60.0])[0]
    assert abs(end - _bilinear(z, T, [30.1], [60.0])[0]) <= 0.15       # 끝 접속 지형은 거의 그대로
    # 둘레는 경사 0.5 로 섞고, 끝 너머(사슬 밖)는 섞지 않는다
    side = out[n - 1 - 66, 60]
    assert side >= out[n - 1 - 63, 60] and side - out[n - 1 - 63, 60] <= 0.5 * 3 + 1e-3
    assert np.allclose(out[n - 1 - 60, :26], z[n - 1 - 60, :26])
    # 순서와 무관
    out2, _ = lower_under_decks(z, T, [span, span], clear=0.3, end_m=3.0, slope=0.5)
    assert np.array_equal(out, out2)


def test_lower_under_decks_ramp_does_not_enter_other_deck_zone():
    T, n = _grid(80, 1.0)
    z = np.full((n, n), 60.0, dtype=np.float32)
    low = LineString([(10.0, 40.0), (70.0, 40.0)])
    high = LineString([(10.0, 50.0), (70.0, 50.0)])             # 10 m 옆 다른 상판(높다)
    x = resample_stations(60.0, 1.0)
    a = DeckSpan(low, x, np.full(len(x), 58.0), [(low, 4.0)], (False, False), 0.8)
    b = DeckSpan(high, x, np.full(len(x), 62.0), [(high, 4.0)], (False, False), 0.8)   # 허용 60.9 > 지형 60
    out, _ = lower_under_decks(z, T, [a, b], clear=0.3, end_m=3.0, slope=0.5)
    sup, m = bilinear_support(deck_footprint(high, 4.0), T, (n, n))
    r0, r1, c0, c1 = sup
    assert np.allclose(out[r0:r1, c0:c1][m], 60.0)              # 낮은 상판의 둘레 섞기가 높은 상판 자리를 깎지 않는다
    assert out[n - 1 - 40, 40] <= 58.0 - 1.1 + 1e-4


# --------------------------------------------------------------------------- 나란한 way 높이 공유·끝 수면 바닥
class _Terrain:
    bounds = (-1000.0, -1000.0, 1000.0, 1000.0)

    def __init__(self, f):
        self.f = f

    def height(self, E, N):
        return self.f(np.atleast_1d(np.asarray(E, dtype=float)), np.atleast_1d(np.asarray(N, dtype=float)))


def _bridge(osm, coords, cls="primary", width=7.0, kind="road"):
    return Bridge(osm_id=osm, geometry=LineString(coords), kind=kind, cls=cls, name="", width_m=width,
                  width_source="default", layer=1)


def _vidx(ways):
    idx = {}
    for osm, coords, br in ways:
        for i, p in enumerate(coords):
            idx.setdefault(_key(p), []).append(_Vtx(osm, br, False, i in (0, len(coords) - 1)))
    return idx


def test_parallel_ways_share_height_and_keep_ends():
    A = [(0.0, 0.0), (200.0, 0.0)]
    B = [(200.0, 10.0), (0.0, 10.0)]                  # 상·하행: 거꾸로 그린 나란한 way, 10 m 간격
    S = [(0.0, 18.0), (30.0, 18.0)]                   # 계단은 공유에서 뺀다
    br = [_bridge(1, A), _bridge(2, B), _bridge(3, S, cls="steps", width=2.0, kind="foot")]
    vidx = _vidx([(1, A, True), (2, B, True), (3, S, True), (10, [(-20, 0), A[0]], False), (11, [A[1], (220, 0)], False),
                  (12, [(220, 10), B[0]], False), (13, [B[1], (-20, 10)], False), (14, [(-9, 18), S[0]], False),
                  (15, [S[1], (39, 18)], False)])
    chains = bridge_chains(br, vidx)
    terrain = _Terrain(lambda E, N: 50.0 + 0.2 * N)  # 같은 단면에서 B 끝이 2 m 높다
    partners = parallel_partners(chains, br, CFG["roads"])
    assert sorted({(p.a, p.b) for p in partners}) == [(0, 1), (1, 0)]
    _, tops0, _, it = share_decks(CFG, br, chains, terrain, None)
    a, b = chains[0], chains[1]
    sb = 200.0 - a.x                                  # B 는 거꾸로
    dz = a.top - np.interp(sb, b.x, b.top)
    mid = (a.x >= 30) & (a.x <= 170)
    assert np.abs(dz[mid]).max() <= 0.011 and np.isclose(a.top[mid], 52.0, atol=0.011).all()   # 가운데 = 큰 값
    assert np.isclose(a.top[0], 50.0) and np.isclose(a.top[-1], 50.0)                          # 끝은 제 지형
    assert (np.abs(np.diff(a.top)) / np.diff(a.x)).max() <= 0.08 + 1e-6
    before = tops0[0] - np.interp(sb, b.x, tops0[1])
    assert np.abs(before[mid]).max() > 1.9 and 2 <= it <= 6


def test_end_raised_to_water_floor_within_step():
    br = [_bridge(1, [(0.0, 0.0), (20.0, 0.0)], cls="footway", width=2.0, kind="foot")]
    vidx = _vidx([(1, [(0.0, 0.0), (20.0, 0.0)], True), (10, [(-10, 0), (0, 0)], False), (11, [(20, 0), (30, 0)], False)])
    chains = bridge_chains(br, vidx)
    terrain = _Terrain(lambda E, N: np.full(E.shape, 49.9))
    V = np.array([[-5, -5, 50.0], [25, -5, 50.0], [25, 5, 50.0], [-5, 5, 50.0]])   # 다리 전체가 수면 메시(넓힌 띠) 위
    water = TriSurface(V, np.array([[0, 1, 2], [0, 2, 3]]))
    ch = chains[0]
    h0, h1 = _profile(ch, br, 49.9, 49.9, CFG, water, terrain)
    assert np.isclose(h0, 50.2) and np.isclose(h1, 50.2)                    # 끝 +0.3 m(허용 0.5 안)
    assert (ch.top >= 50.2 - 1e-9).all() and ch.info["water_gap_min_m"] >= 0.2 - 1e-9


# --------------------------------------------------------------------------- 원경 수면 아래 지면
def test_skirt_vertices_under_water_drop_two_metres():
    P = np.array([[0.0, 0.0], [5.0, 5.0], [50.0, 50.0], [5.0, 1.0]])
    H = np.array([49.0, 49.0, 49.0, 49.0])
    sel = np.array([True, True, True, False])          # 마지막은 경계 줄(건드리지 않음)
    V = np.array([[-1, -1, 49.0], [10, -1, 49.0], [10, 10, 49.0], [-1, 10, 49.0]])
    surf = TriSurface(V, np.array([[0, 1, 2], [0, 2, 3]]))
    H2, wet = drop_under_water(P, H, sel, surf, 2.0)
    assert list(wet) == [True, True, False, False]
    assert np.allclose(H2, [47.0, 47.0, 49.0, 49.0])
