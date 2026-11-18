"""계약 C2(도로 마스크)·C3(교량 상판) 순수 함수 고정. 합성 데이터만 쓴다."""
import numpy as np
from shapely.geometry import LineString, box

from pipeline.bridges import Bridge, deck_mesh, deck_profile, footprint_ring, resample_stations
from pipeline.roads import _Vtx, _key, bridge_chains, mask_tile, pixel_center, road_channel, world_to_pixel


# --------------------------------------------------------------------------- C2 마스크
def test_road_channels():
    assert road_channel("roads", "primary") == 0 and road_channel("roads", "service") == 0
    assert road_channel("roads", "secondary_link") == 0
    assert road_channel("roads", "footway") == 1 and road_channel("roads", "track") == 1
    assert road_channel("roads", "steps") == 1 and road_channel("roads", "cycleway") == 1
    assert road_channel("railways", "rail") == 2
    assert road_channel("roads", "construction") is None and road_channel("railways", "abandoned") is None


def test_pixel_center_contract():
    e0, n0 = 245000, 579000
    x, y = pixel_center(e0, n0, 0, 0)
    assert np.isclose(x, e0 + 0.5 * 1000 / 1024) and np.isclose(y, n0 + 1000 - 0.5 * 1000 / 1024)   # 행 0 = 북쪽
    x, y = pixel_center(e0, n0, 1023, 1023)
    assert np.isclose(x, e0 + 1000 - 0.5 * 1000 / 1024) and np.isclose(y, n0 + 0.5 * 1000 / 1024)
    c, r = world_to_pixel(e0, n0, *pixel_center(e0, n0, np.array([5, 700]), np.array([900, 12])))
    assert list(c) == [5, 700] and list(r) == [900, 12]


def test_mask_rows_north_first_and_antialiased():
    e0, n0 = 0.0, 0.0
    road = LineString([(-50, 750.3), (1050, 750.3)]).buffer(5.0, cap_style="flat")   # 북쪽 1/4 지점 동서 도로, 폭 10 m
    foot = LineString([(100, -50), (100, 1050)]).buffer(1.5, cap_style="flat")   # 서쪽 100 m 남북 보도, 폭 3 m
    img = mask_tile([(0, road), (1, foot)], e0, n0)
    assert img.shape == (1024, 1024, 3) and img.dtype == np.uint8
    R, G, B = (img[:, :, k].astype(float) for k in range(3))
    rows = np.nonzero(R[:, 512] > 0)[0]
    r_mid = (1000 - 750.3) * 1024 / 1000                      # 북쪽에서 256 행 근처
    assert abs(rows.mean() - (r_mid - 0.5)) < 1.0 and rows.max() < 300
    cols = np.nonzero(G[512, :] > 0)[0]
    assert abs(cols.mean() - (100 * 1024 / 1000 - 0.5)) < 1.0
    assert B.max() == 0
    # 덮임 비율 합 = 넓이(타일 안, 부분 표본 0.24 m 단위 오차), 가장자리는 중간값(안티에일리어싱)
    sub = 1000 / 1024 / 4
    assert abs(R.sum() / 255 * (1000 / 1024) ** 2 - 1000 * 10) <= 1000 * sub
    assert abs(G.sum() / 255 * (1000 / 1024) ** 2 - 1000 * 3) <= 1000 * sub
    edge = R[:, 512][(R[:, 512] > 0) & (R[:, 512] < 255)]
    assert edge.size >= 1
    # 같은 채널끼리 겹친 곳은 한 번만(포화 255)
    img2 = mask_tile([(0, road), (0, road)], e0, n0)
    assert np.array_equal(img2[:, :, 0], img[:, :, 0])


# --------------------------------------------------------------------------- C3 상판 종단
def _water_req(x, a, b, level):
    req = np.full(len(x), np.nan)
    req[(x >= a) & (x <= b)] = level
    return req


def test_deck_profile_ends_clearance_and_grade():
    x = resample_stations(200.0, 1.0)
    top, info = deck_profile(x, 50.0, 52.0, _water_req(x, 60, 140, 49.0 + 3.0), 0.08, 20.0)
    assert np.isclose(top[0], 50.0) and np.isclose(top[-1], 52.0)          # 양 끝 = 지형
    over = (x >= 60) & (x <= 140)
    assert (top[over] >= 52.0 - 1e-6).all() and "clearance_short_m" not in info
    g = np.abs(np.diff(top)) / np.diff(x)
    assert g.max() <= 0.08 + 0.003                                         # 경사 상한(다듬기 오차 포함)
    # 수면이 없으면 양 끝 선형 보간
    top2, _ = deck_profile(x, 50.0, 52.0, np.full(len(x), np.nan), 0.08, 20.0)
    assert np.allclose(top2, 50.0 + 2.0 * x / 200.0)


def test_deck_profile_infeasible_cases_are_reported():
    x = resample_stations(30.0, 1.0)
    top, info = deck_profile(x, 50.0, 50.0, _water_req(x, 5, 25, 49.0 + 3.0), 0.08, 20.0)
    assert info["clearance_short_m"] > 0.5                                 # 30 m 다리에서 2 m 를 8% 로 못 올림
    assert np.isclose(top[0], 50.0) and np.isclose(top[-1], 50.0)
    assert (np.abs(np.diff(top)) / np.diff(x)).max() <= 0.08 + 0.003
    top, info = deck_profile(x, 50.0, 56.0, np.full(len(x), np.nan), 0.08, 20.0)
    assert np.isclose(info["grade_forced"], 0.2) and np.isclose(top[-1], 56.0)


def test_deck_mesh_shape_and_winding():
    xy = np.array([[0.0, 0.0], [20.0, 0.0], [40.0, 10.0]])
    top = np.array([60.0, 61.0, 62.0])
    V, N, F, Lo, Ro = deck_mesh(xy, top, 8.0, 0.8, 1.0, 0.25)
    cr = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    assert np.all((cr * N[F[:, 0]]).sum(1) > 0)                           # 감김과 법선이 같은 쪽
    assert np.isclose(V[:, 2].max(), 63.0) and np.isclose(V[:, 2].min(), 59.2)   # 난간 꼭대기, 상판 밑면
    # 첫 단면 폭 = 8 m, 왼쪽은 +N 쪽
    assert np.allclose(Lo[0], [0, 4]) and np.allclose(Ro[0], [0, -4])
    ring = footprint_ring(xy, 8.0)
    assert ring.shape == (6, 2) and not np.allclose(ring[0], ring[-1])     # 닫지 않은 링


# --------------------------------------------------------------------------- C3 사슬·끝 분류
def _br(osm, coords, kind="road"):
    return Bridge(osm_id=osm, geometry=LineString(coords), kind=kind, cls="primary", name="", width_m=9.0,
                  width_source="default", layer=1)


def test_bridge_chains_serial_ways_and_end_kinds():
    a = _br(1, [(0, 0), (100, 0)])
    b = _br(2, [(200, 0), (100, 0)])        # 거꾸로 그려진 이어진 way
    c = _br(3, [(300, 50), (320, 50)])      # 끝이 아무 길에도 안 붙음
    idx = {}

    def add(osm, coords, bridge):
        for i, p in enumerate(coords):
            idx.setdefault(_key(p), []).append(_Vtx(osm, bridge, False, i in (0, len(coords) - 1)))
    add(1, [(0, 0), (100, 0)], True)
    add(2, [(200, 0), (100, 0)], True)
    add(3, [(300, 50), (320, 50)], True)
    add(10, [(-50, 0), (0, 0)], False)      # 서쪽 접속 도로
    add(11, [(200, 0), (260, 0)], False)    # 동쪽 접속 도로
    chains = bridge_chains([a, b, c], idx)
    assert len(chains) == 2
    ch = next(ch for ch in chains if len(ch.members) == 2)
    assert ch.members == [(0, False), (1, True)]
    assert np.isclose(ch.line.length, 200.0)
    assert [e["kind"] for e in ch.ends] == ["shared", "shared"]
    assert ch.ends[0]["with"] == [10] and ch.ends[1]["with"] == [11]
    assert np.allclose(ch.ends[0]["out"], [-1, 0]) and np.allclose(ch.ends[1]["out"], [1, 0])
    lone = next(ch for ch in chains if len(ch.members) == 1)
    assert [e["kind"] for e in lone.ends] == ["free", "free"]
