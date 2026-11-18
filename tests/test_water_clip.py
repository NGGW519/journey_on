"""06a 규칙 고정(합성 데이터): 보이는 수면 자르기(D-007), 접속 길 없는 교량 육지 끝 = 끝 지형(D-012),
폭 이상값 거르기(D-012), manifest water(계약 v2 선택 필드)."""
import numpy as np
import shapely
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import LineString, Point, box

from pipeline.bridges import Bridge, bridge_width, width_check
from pipeline.config import load_config
from pipeline.export import water_spec
from pipeline.roads import _Vtx, _key, bridge_chains, compute_decks, end_tapers
from pipeline.surface import TriSurface
from pipeline.validate import unity_boundary_edges
from pipeline.water import CLIP_MIN_SEP_M, below_region, clean_region, clip_triangles, delta_grid

CFG = load_config()
RNG = np.random.default_rng(7)


def _grid_mesh(n=9, size=80.0, x0=0.0, y0=0.0):
    """n×n 격자점(약간 흔든) Delaunay + 정점마다 다른 높이(삼각형마다 다른 평면)."""
    g = np.linspace(0.0, size, n)
    X, Y = np.meshgrid(g, g)
    P = np.column_stack([X.ravel() + x0, Y.ravel() + y0])
    inner = (P[:, 0] > x0) & (P[:, 0] < x0 + size) & (P[:, 1] > y0) & (P[:, 1] < y0 + size)
    P[inner] += RNG.uniform(-2.0, 2.0, (int(inner.sum()), 2))
    F = Delaunay(P).simplices
    z = 50.0 + RNG.uniform(-0.5, 0.5, len(P))
    return P, F, z


def _area(P, F):
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    return 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))


# --------------------------------------------------------------------------- 자르기(clip_triangles)
def test_clip_keeps_original_surface_and_region_area():
    P, F, z = _grid_mesh()
    region = Point(40.0, 40.0).buffer(27.0, quad_segs=32)
    P2, F2, z2, st = clip_triangles(P, F, z, region)
    assert st["cut"] > 0 and st["kept"] > 0 and st["dropped"] > 0
    assert np.isclose(_area(P2, F2).sum(), region.intersection(box(0, 0, 80, 80)).area, rtol=2e-3)
    # 남은 면의 높이 = 자르기 전 면(삼각형 평면 그대로). 무작위 점 2,000개
    s0 = TriSurface(np.column_stack([P, z]), F, cell_m=5.0)
    s1 = TriSurface(np.column_stack([P2, z2]), F2, cell_m=5.0)
    q = RNG.uniform(10.0, 70.0, (2000, 2))
    h1 = s1.height(q[:, 0], q[:, 1])
    ok = np.isfinite(h1)
    assert ok.sum() > 1000
    assert np.abs(h1[ok] - s0.height(q[ok, 0], q[ok, 1])).max() < 2e-3   # 짧은 변 접기(≤ 8 cm)의 높이 몫 안
    assert not np.isfinite(s1.height([40.0 + 30.0], [40.0])).any()        # 영역 밖은 없음
    # 위를 보는 감김
    a, b, c = P2[F2[:, 0]], P2[F2[:, 1]], P2[F2[:, 2]]
    assert (((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])) > 0).all()


def test_clip_boundary_is_region_boundary_without_t_junctions():
    """경계 변은 영역 경계(원)나 원래 메시 경계 위에만 있고(안쪽 T 자 이음 없음), Unity 5 cm 키로 세도 같다."""
    P, F, z = _grid_mesh()
    region = Point(30.0, 45.0).buffer(30.0, quad_segs=24).difference(Point(30.0, 45.0).buffer(8.0, quad_segs=16))
    P2, F2, z2, _ = clip_triangles(P, F, z, region)
    V = np.column_stack([P2, z2])
    e = np.sort(np.concatenate([F2[:, [0, 1]], F2[:, [1, 2]], F2[:, [2, 0]]]), axis=1)
    u, cnt = np.unique(e, axis=0, return_counts=True)
    assert cnt.max() <= 2                                                  # 다양체
    be = u[cnt == 1]
    mid = (P2[be[:, 0]] + P2[be[:, 1]]) / 2.0
    bnd = shapely.union_all([region.boundary, box(0, 0, 80, 80).boundary])
    assert shapely.distance(bnd, shapely.points(mid)).max() < 0.1          # 접기(≤ 8 cm) 오차 안
    A, B, _ = unity_boundary_edges(V, F2, tile_size=1e6)                  # 타일 경계 제외 없이
    assert len(A) == len(be)
    # 자르며 생긴 정점끼리·원래 정점과 CLIP_MIN_SEP_M 이상 떨어짐(원래 정점끼리는 그대로)
    pairs = cKDTree(P2).query_pairs(CLIP_MIN_SEP_M - 1e-6, output_type="ndarray")
    orig = cKDTree(P)
    if len(pairs):
        d0, _ = orig.query(P2[pairs[:, 0]])
        d1, _ = orig.query(P2[pairs[:, 1]])
        assert ((d0 < 1e-9) & (d1 < 1e-9)).all()


def test_clip_near_vertex_collapses_short_edges():
    """영역 경계가 원래 정점 1 cm 옆을 지나면 그 교점은 원래 정점으로 접힌다(5 cm 키에서 겹치지 않게)."""
    P = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [5, 5.01]], dtype=float)
    F = Delaunay(P).simplices
    z = np.array([50.0, 50.1, 50.2, 50.3, 50.15])
    region = box(-1, -1, 11, 5.0)                                          # y = 5.0 (정점 (5, 5.01) 1 cm 아래)
    P2, F2, z2, st = clip_triangles(P, F, z, region)
    assert st["collapsed"] >= 1
    pairs = cKDTree(P2).query_pairs(CLIP_MIN_SEP_M - 1e-6)
    assert not pairs


def test_clip_drops_small_pieces_by_centroid():
    P, F, z = _grid_mesh()
    region = shapely.union_all([box(0, 0, 80, 40), Point(60, 70).buffer(3.0)])   # 작은 조각 약 28 m²
    small = [Point(60, 70).buffer(3.0)]
    P2, F2, _, st = clip_triangles(P, F, z, region, small)
    assert st["small_dropped"] > 0
    assert np.isclose(_area(P2, F2).sum(), 80 * 40, rtol=1e-3)


# --------------------------------------------------------------------------- 영역(등고선)·정리
def test_delta_grid_uses_mesh_then_fill():
    step = 1.0
    Z = np.full((21, 21), 50.5)
    Wf = np.full((21, 21), 49.0)                                           # 메시 밖: 가장 가까운 수면
    surf = TriSurface(np.array([[0, 0, 50.0], [10, 0, 50.0], [10, 20, 50.0], [0, 20, 50.0]]),
                      np.array([[0, 1, 2], [0, 2, 3]]))
    near = np.ones((21, 21), dtype=bool)
    near[:, 20] = False
    D = delta_grid(Z, Wf, near, surf, 0.0, 0.0, step)
    assert np.allclose(D[:, :11], 0.5) and np.allclose(D[:, 11:20], 1.5) and np.isnan(D[:, 20]).all()


def test_below_region_contour_and_clean():
    """경사 둑(지형 − 수면 = 0.1·(x − 50)) + 물속 둔덕(구멍 ≈ 13 m²) + 뭍 위 웅덩이(≈ 28 m²)."""
    step = 1000.0 / 1024
    n = 103
    x = np.arange(n) * step
    X, Y = np.meshgrid(x, x)
    D = 0.1 * (X - 50.0)
    D[np.hypot(X - 20, Y - 50) < 2.0] = 1.0                               # 물속 둔덕(지형 > 수면 + 0.4)
    D[np.hypot(X - 85, Y - 50) < 3.0] = -0.2                              # 뭍 위 웅덩이(보이는 작은 물)
    region = below_region(D.astype(np.float32), 0.0, 0.0, step, 0.4, 0.15)
    # 둑의 등고선: x = 54 (지형 − 수면 = 0.4), 단순화 0.15 m 안
    assert region.contains(Point(53.8, 30.0)) and not region.contains(Point(54.2, 30.0))
    assert not region.contains(Point(20, 50))                              # 둔덕은 구멍
    footprint = box(0, 0, (n - 1) * step, (n - 1) * step)
    region2, small, st = clean_region(footprint, region, 50.0)
    assert st["holes_filled"] == 1 and region2.contains(Point(20, 50))     # 작은 구멍은 메운다
    assert st["pieces_removed"] == 1 and any(p.contains(Point(85, 50)) for p in small)
    # 섬 구멍(메시 밖)은 메우지 않는다
    fp_island = footprint.difference(Point(30, 80).buffer(2.0))
    D2 = 0.1 * (X - 50.0)
    region3 = below_region(D2.astype(np.float32), 0.0, 0.0, step, 0.4, 0.15)
    r4, _, st4 = clean_region(fp_island, region3, 50.0)
    assert st4["holes_filled"] == 0 and not shapely.intersection(fp_island, r4).contains(Point(30, 80))


# --------------------------------------------------------------------------- 교량 접속 길 없는 육지 끝(D-012, 06a)
class _Terrain:
    bounds = (-1000.0, -1000.0, 1000.0, 1000.0)

    def __init__(self, f):
        self.f = f

    def height(self, E, N):
        return self.f(np.atleast_1d(np.asarray(E, dtype=float)), np.atleast_1d(np.asarray(N, dtype=float)))


def test_free_land_end_is_fixed_to_end_terrain():
    A = [(0.0, 0.0), (40.0, 0.0)]
    br = [Bridge(osm_id=1, geometry=LineString(A), kind="rail", cls="rail", name="", width_m=5.0,
                 width_source="default", layer=1)]
    vidx = {}
    for osm, coords, b in ((1, A, True), (10, [(-30.0, 0.0), A[0]], False)):   # 서쪽만 접속 길, 동쪽 끝은 접속 길 없음
        for i, p in enumerate(coords):
            vidx.setdefault(_key(p), []).append(_Vtx(osm, b, False, i in (0, len(coords) - 1)))
    chains = bridge_chains(br, vidx)
    terrain = _Terrain(lambda E, N: 50.0 + 0.1 * np.maximum(E - 40.0, 0.0))   # 끝 밖으로 오르막(10 m 밖 +1 m)
    compute_decks(CFG, br, chains, terrain, None)
    end = chains[0].ends[1]
    assert end["kind"] == "free" and end["rule"] == "end_terrain"
    assert np.isclose(end["deck_m"], 50.0) and np.isclose(chains[0].top[-1], 50.0)   # 전에는 바깥 10 m 지형 51.0
    assert np.isclose(end["outward_terrain_m"], 51.0) and np.isclose(end["ground_grade"], 0.1)
    assert end_tapers(chains[0]) == (True, True)                          # 땅에 닿는 끝(상판 밑 지형 규칙)


# --------------------------------------------------------------------------- 폭 이상값(D-012, 06a)
def test_width_outliers_fall_through_to_next_rule():
    R = CFG["roads"]
    cyc = float(R["width_m"]["cycleway"])
    assert width_check({"width": "43"}, "cycleway", R) == (cyc, "default", ["width=43"])           # w588836894
    assert width_check({"width": "43", "lanes": "1"}, "primary", R) == (4.0, "lanes", ["width=43"])  # 43 > 3 × 10 m
    assert width_check({"width": "40", "lanes": "2"}, "secondary", R) == (7.0, "lanes", ["width=40"])
    assert width_check({"lanes": "6"}, "residential", R) == (float(R["width_m"]["residential"]), "default", ["lanes=6"])  # 19 m > 15 m
    assert width_check({"width": str(3 * cyc)}, "cycleway", R) == (3 * cyc, "width", [])          # 경계(= 3배)는 그대로
    assert bridge_width({"width": "43"}, "cycleway", R) == (cyc, "default")


# --------------------------------------------------------------------------- manifest water(06a)
def test_manifest_water_spec_matches_config():
    w = water_spec(CFG)
    assert w == {"shore_overlap_m": float(CFG["water"]["shore_overlap_m"]),
                 "shore_clip_above_m": float(CFG["water"]["shore_clip_above_m"])}
    assert 0.0 < w["shore_clip_above_m"] < w["shore_overlap_m"]
