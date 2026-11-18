"""플레이어 시작점(D-010): 넓힌 수면에서 min_shore_distance_m 이상, 경사 19° 미만인 가장 가까운 육지."""
import copy

import numpy as np
import rasterio
from affine import Affine
from shapely.geometry import Point, box

import pipeline.dem as dem
from pipeline.config import load_config
from pipeline.export import find_spawn


def test_find_spawn_on_flat_land_away_from_shore(monkeypatch, tmp_path):
    cfg = copy.deepcopy(load_config())
    cfg["paths"] = {**cfg["paths"], "interim": str(tmp_path)}
    cfg["spawn"] = {"near": [246290.0, 578300.0], "look_at": [246290.0, 577000.0],
                    "min_shore_distance_m": 12.0, "search_radius_m": 300.0}
    e0, n0, size = 245900.0, 577900.0, 800
    T = Affine(1.0, 0.0, e0, 0.0, -1.0, n0 + size)
    xs = e0 + 0.5 + np.arange(size)[None, :].repeat(size, 0)
    z = np.full((size, size), 52.0, dtype=np.float32)
    ramp = (xs >= 246305) & (xs < 246330)
    z[ramp] += (xs[ramp] - 246305) * 0.5                      # 27° 비탈(물가 12 m 선을 가로지름) → 시작점 불가
    z[xs >= 246330] += 12.5
    (tmp_path / "phase1").mkdir()
    with rasterio.open(tmp_path / "phase1" / "dem_samples.tif", "w", driver="GTiff", width=size, height=size,
                       count=1, dtype="float32", crs="EPSG:5186", transform=T) as dst:
        dst.write(z, 1)
    water = box(245900, 577900, 246300, 578700)               # 기준점(E246290)은 물 안
    monkeypatch.setattr(dem, "water_polygons", lambda cfg, area, bounds, with_meta=False: [water])
    sp = find_spawn(cfg, "phase1")
    assert sp is not None
    p = Point(sp["e"], sp["n"])
    # 넓힌 물가에서 12 m 이상(래스터 거리라 반 표본 0.5 m 오차 허용)
    assert not water.buffer(cfg["water"]["shore_overlap_m"] + 12.0 - 0.5).contains(p)
    assert sp["shore_distance_m"] >= 12.0
    assert 246330 < sp["e"] < 246335 and abs(sp["n"] - 578300) < 2                        # 비탈이 아닌 가장 가까운 평지
    assert sp["ground_m"] == 64.5
    assert abs(abs(sp["yaw_deg"]) - 180.0) < 5.0                                          # 남쪽(look_at)을 본다
