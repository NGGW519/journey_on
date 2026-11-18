"""지형·수면 처리 규칙 고정: 깎기 보호(D-007), 수면 분류, 수면체 이음, DSM 평탄화(D-009)."""
import numpy as np
from shapely.geometry import Polygon, box

from pipeline.config import load_config
from pipeline.dem import (JUNCTION_BAND_M, carve, flatten_footprints, monotone_downstream, slope_limit,
                          sloped_level, water_level_field, water_surface)
from pipeline.heightmap import mosaic_grid, sample_spacing

CFG = load_config()
PX = sample_spacing(CFG)


def _grid(bounds=(0, 0, 400, 400)):
    T, w, h = mosaic_grid(CFG, bounds)
    xs = (np.arange(w) * PX)[None, :].repeat(h, 0)
    ys = (bounds[3] - np.arange(h) * PX)[:, None].repeat(w, 1)
    return T, xs, ys


def test_carve_keeps_hill_inside_water_polygon():
    T, xs, ys = _grid()
    z = np.full(xs.shape, 60.0, dtype=np.float32)
    field = np.full_like(z, np.nan)
    wet = (xs > 100) & (xs < 300)
    field[wet] = 50.0
    z[wet] = 50.5                                 # 수면 근처 하상 → 깎여야 함
    hill = wet & (ys > 300)
    z[hill] = 75.0                                # 폴리곤에 잘못 들어간 산비탈 → 보호
    out, _ = carve(z, field, PX, CFG["water"])
    assert out[wet & (ys < 250) & (xs > 150) & (xs < 250)].max() <= 49.7
    assert out[hill & (ys > 340)].min() > 74.0    # 수면보다 8m 넘게 높으면 깎지 않음


def test_carve_shore_is_continuous():
    T, xs, ys = _grid()
    z = np.full(xs.shape, 52.0, dtype=np.float32)  # 수면보다 2.7m 높은 둑
    field = np.full_like(z, np.nan)
    wet = xs > 200
    field[wet] = 49.3
    out, _ = carve(z, field, PX, CFG["water"])
    row = out[200, :]
    assert np.abs(np.diff(row)).max() < 1.0       # 한 표본(≈1m) 사이 1m 넘는 절벽 없음


def test_water_classification_and_junction():
    T, xs, ys = _grid()
    z = (80.0 - xs * 0.1).astype(np.float32)      # 동쪽으로 낮아지는 골짜기(40m 낙차)
    river = Polygon([(0, 190), (300, 190), (300, 210), (0, 210)])      # 가늘고 긴 하천
    lake = box(300, 100, 400, 300)                                     # 호수(평평)
    z[(xs >= 300)] = 49.5
    field, bodies = water_level_field(CFG, z, T, [(river, False), (lake, False)])
    kinds = {round(b["area_m2"]): b["kind"] for b in bodies}
    assert kinds[round(river.area)] == "sloped"
    assert kinds[round(lake.area)] == "flat"
    wet = ~np.isnan(field)
    step = np.nanmax(np.abs(np.diff(np.where(wet, field, np.nan), axis=1)))
    assert step < 0.3                              # 합류부 계단 없음


def test_flatten_footprints_removes_dsm_bump():
    T, xs, ys = _grid((0, 0, 200, 200))
    z = np.full(xs.shape, 50.0, dtype=np.float32)
    foot = box(90, 90, 110, 110)
    bump = (np.hypot(xs - 100, ys - 100) < 25)
    z[bump] = 62.0                                 # DSM 에 섞인 건물 높이
    n = flatten_footprints(z, T, [foot], CFG["dem"]["dsm_flatten_buildings"])
    assert n == 1
    inside = (xs > 92) & (xs < 108) & (ys > 92) & (ys < 108)
    assert z[inside].max() < 51.0
    assert z[(np.hypot(xs - 100, ys - 100) > 80)].min() == 50.0   # 먼 곳은 그대로


def test_flatten_buildings_skips_no_build_zone(monkeypatch):
    """금지 구역(남이섬) 안 건물은 내보내지 않으므로 평탄화도 하지 않는다(빈 구덩이 방지)."""
    import geopandas as gpd

    import pipeline.dem as dem
    import pipeline.zones as zones
    T, xs, ys = _grid((0, 0, 300, 200))
    z = np.full(xs.shape, 50.0, dtype=np.float32)
    inside, outside = box(60, 90, 80, 110), box(220, 90, 240, 110)
    for g in (inside, outside):
        z[np.hypot(xs - g.centroid.x, ys - g.centroid.y) < 25] = 62.0     # DSM 에 섞인 건물
    gdf = gpd.GeoDataFrame(geometry=[inside, outside], crs="EPSG:5186")
    monkeypatch.setattr(dem, "load_layer", lambda cfg, area, layer, margin_m=200.0: gdf)
    monkeypatch.setattr(zones, "no_build_polygons", lambda cfg, area: [("섬", box(0, 0, 150, 200))])
    n = dem.flatten_buildings(CFG, "phase1", z, T)
    assert n == 1
    assert z[np.hypot(xs - 70, ys - 100) < 8].min() == 62.0    # 금지 구역 안: 그대로
    assert z[np.hypot(xs - 230, ys - 100) < 8].max() < 51.0    # 밖: 평탄화


def _channel(bounds=(0, 0, 800, 400), slope=0.05, bump=0.0):
    T, xs, ys = _grid(bounds)
    z = (90.0 - slope * xs).astype(np.float32)                 # 동쪽으로 5% 내려가는 하천
    mask = (ys > 190) & (ys < 210)
    if bump:
        z[mask & (np.abs(xs - 400) < 15)] += bump              # DSM 다리(OSM 폭보다 넓게 번짐)
    return T, xs, ys, z, mask


def test_min_filter_only_near_bridge():
    T, xs, ys, z0, mask = _channel()
    _, _, _, zb, _ = _channel(bump=8.0)
    foot = np.abs(xs - 400) < 6                                # 다리 footprint(폭 12 m)
    ref = sloped_level(z0, mask, PX)                           # 다리 없는 같은 하천
    raw = sloped_level(zb, mask, PX)                           # 다리 처리 없음
    got = sloped_level(zb, mask, PX, foot)
    at = mask & (np.abs(xs - 400) < 3)
    assert (raw - ref)[at].min() > 1.0                         # 처리하지 않으면 다리 둔덕이 수면을 끌어올림
    assert np.abs(got - ref)[at].max() < 0.8                   # 다리 자리: 둔덕 제거
    far = mask & (np.abs(xs - 400) > 180) & (xs > 60) & (xs < 740)
    assert np.abs(got - ref)[far].max() < 0.05                 # 다리에서 먼 경사 수면은 낮추지 않음(전역 필터면 약 0.75 m)


def test_monotone_downstream_lowers_only():
    s = np.arange(7) * 2.0
    f = np.array([5.0, 4.0, 4.6, 3.0, 3.4, 3.4, 2.0])
    out = monotone_downstream(f, s, 2.0)
    assert np.allclose(out, [5, 4, 4, 3, 3, 3, 2])
    assert np.all(out <= f) and np.all(np.diff(out) <= 0)
    mono = np.array([5.0, 4.5, 4.0, 3.0])
    assert np.allclose(monotone_downstream(mono, np.arange(4) * 2.0, 2.0), mono)   # 이미 단조면 그대로
    s2, f2 = np.array([0.0, 0.5, 2.0, 2.5]), np.array([5.0, 3.0, 4.0, 4.5])
    assert np.allclose(monotone_downstream(f2, s2, 2.0), [5.0, 3.0, 4.0, 4.5])      # 횡단 높낮이는 두고 올리지 않음
    anchor = np.array([False, True, False, False])                                  # 중심선 표본 기준
    assert np.allclose(monotone_downstream(f2, s2, 2.0, anchor), [3.0, 3.0, 3.0, 3.0])


def test_slope_limit_turns_step_into_ramp():
    f = np.full((5, 40), 60.0)
    f[:, 20:] = 55.0                                          # 5 m 단
    m = np.ones_like(f, dtype=bool)
    out = slope_limit(f, m, 1.0, 0.25)
    assert np.all(out <= f + 1e-9)
    assert np.abs(np.diff(out, axis=1)).max() <= 0.25 + 1e-6
    assert out[:, 25:].min() == 55.0 and out[:, 0].max() == 60.0


def _scene():
    """호수(평수면) + 둔덕이 있는 경사 하천(호수로 흘러듦) + 양 끝이 호수에 닿는 곁수로."""
    from shapely import contains_xy
    from shapely.geometry import LineString
    T, xs, ys = _grid()
    z = np.full(xs.shape, 70.0, dtype=np.float32)
    lake = box(250, 0, 400, 400)
    z[xs >= 250] = 49.0
    stream = box(0, 300, 252, 312)
    on = contains_xy(stream, xs, ys) & (xs < 250)
    z[on] = 60.0 - 0.04 * xs[on]
    z[on & (np.abs(xs - 120) < 10)] += 6.0                    # 둔덕 → 그대로 두면 수면 역경사
    side = box(200, 50, 252, 66).union(box(200, 50, 216, 250)).union(box(200, 234, 252, 250))
    on_s = contains_xy(side, xs, ys) & (xs < 250)
    z[on_s] = 49.5 + 0.03 * ys[on_s]                          # 표고 범위 6 m → 경사 후보
    line = LineString([(0, 306), (260, 306)])                  # 흐름 방향 = 서 → 동
    return T, xs, ys, z, [(lake, False), (stream, False), (side, False)], line


def _profile(field, owner, ys, body, y=306.0):
    row = int(np.argmin(np.abs(ys[:, 0] - y)))
    cols = np.nonzero(owner[row, :] == body)[0]
    return field[row, cols]


def test_water_surface_downstream_monotone_and_side_channel():
    T, xs, ys, z, polys, line = _scene()
    field, owner, bodies = water_surface(CFG, z, T, polys, flow_lines=[(1, line)])
    b = {d["id"]: d for d in bodies}
    assert b[2]["kind"] == "flat" and b[2]["reclass"] == "side_channel_of:0"   # 양 끝이 같은 평수면
    assert b[2]["level_m"] == b[0]["level_m"]
    assert b[1]["kind"] == "sloped" and b[1]["flow"] == "osm_waterway"
    p = _profile(field, owner, ys, 1)
    assert (p - np.minimum.accumulate(p)).max() < 1e-4                         # 하류로 높아지지 않음
    # 물줄기 선이 없으면 더 낮은 수면체(호수)에 닿는 끝을 하구로 본다
    f2, o2, b2 = water_surface(CFG, z, T, polys)
    assert {d["id"]: d for d in b2}[1]["flow"] == "contact"
    p2 = _profile(f2, o2, ys, 1)
    assert (p2 - np.minimum.accumulate(p2)).max() < 1e-4


def test_junction_smoothing_only_at_body_boundary():
    T, xs, ys, z, polys, line = _scene()
    field, owner, _ = water_surface(CFG, z, T, polys, flow_lines=[(1, line)])
    alone, _, _ = water_surface(CFG, z, T, [polys[1]], flow_lines=[(1, line)])   # 이웃 수면체 없음
    inner = (owner == 1) & (xs < 250 - JUNCTION_BAND_M - 5)
    assert np.abs(field[inner] - alone[inner]).max() < 1e-4                      # 수면체 안쪽은 그대로
    edge = (owner == 1) & (xs > 245)
    assert np.abs(field[edge] - alone[edge]).max() > 0.05                        # 맞닿은 띠만 섞임
    wet = ~np.isnan(field)
    step = max(np.nanmax(np.abs(np.diff(np.where(wet, field, np.nan), axis=a))) for a in (0, 1))
    assert step < 0.3


def test_carve_force_removes_bridge_mound():
    T, xs, ys = _grid()
    z = np.full(xs.shape, 60.0, dtype=np.float32)
    wet = (ys > 150) & (ys < 250)
    field = np.where(wet, 50.0, np.nan).astype(np.float32)
    z[wet] = 50.5
    mound = wet & (np.abs(xs - 200) < 10)
    z[mound] = 62.0                                            # 수면보다 12 m 높은 DSM 다리 둔덕
    kept, _ = carve(z, field, PX, CFG["water"])
    assert kept[mound & (np.abs(xs - 200) < 5) & (np.abs(ys - 200) < 30)].min() > 61.0   # 보통은 보호
    force = np.clip(1.0 - (np.abs(xs - 200) - 12) / 15.0, 0.0, 1.0).astype(np.float32)
    out, _ = carve(z, field, PX, CFG["water"], force=force)
    assert out[mound].max() <= 49.7                            # 다리 자리는 보호 없이 수면 아래로
