"""격자·높이맵 규약 고정: 타일 이름, 표본 공유, RAW 행 순서."""
import numpy as np

from pipeline.config import load_config
from pipeline.grid import Tile, area_bounds, area_tiles, tile_from_name, tiles_for_bounds
from pipeline.heightmap import decode, encode, mosaic_grid, sample_spacing, tile_window

CFG = load_config()


def test_tile_name_roundtrip():
    t = Tile(245000, 579000)
    assert t.name == "Tile_245_579"
    assert tile_from_name("Tile_245_579") == t
    assert t.unity_position(CFG["origin"]) == [-1000.0, 0.0, -1000.0]


def test_tiles_snap_outward():
    ts = tiles_for_bounds([244100, 575900, 245100, 576100])
    assert {t.name for t in ts} == {"Tile_244_575", "Tile_245_575", "Tile_244_576", "Tile_245_576"}


def test_phase1_grid():
    ts = area_tiles(CFG, "phase1")
    assert len(ts) == 28
    assert area_bounds(CFG, "phase1") == (244000, 575000, 248000, 582000)


def test_mosaic_grid_sample_registration():
    T, w, h = mosaic_grid(CFG, (0, 0, 2000, 1000))
    px = sample_spacing(CFG)
    assert (w, h) == (2049, 1025)
    # 첫 픽셀 중심 = 북서 표본점(0, 1000)
    x, y = T @ (0.5, 0.5)
    assert abs(x - 0) < 1e-9 and abs(y - 1000) < 1e-9
    assert abs(px - 1000 / 1024) < 1e-12


def test_neighbor_tiles_share_edge_samples():
    bounds = (0, 0, 2000, 2000)
    _, w, h = mosaic_grid(CFG, bounds)
    z = np.random.default_rng(0).uniform(10, 500, (h, w))
    sw = z[tile_window(CFG, bounds, 0, 0)]
    se = z[tile_window(CFG, bounds, 1000, 0)]
    nw = z[tile_window(CFG, bounds, 0, 1000)]
    assert sw.shape == (1025, 1025)
    assert np.array_equal(sw[:, -1], se[:, 0])     # 동쪽 이웃
    assert np.array_equal(sw[0, :], nw[-1, :])     # 북쪽 이웃(북쪽이 첫 행인 배열)


def test_raw_row_order_south_first():
    z = np.zeros((1025, 1025))
    z[0, :] = 300.0      # 북쪽 가장자리(북쪽이 첫 행)
    z[-1, :] = 100.0     # 남쪽 가장자리
    back = decode(encode(z, CFG), CFG)   # 첫 행 = 남쪽
    assert abs(back[0, 0] - 100.0) < 0.05
    assert abs(back[-1, 0] - 300.0) < 0.05


def test_raw_quantization_error_small():
    z = np.linspace(CFG["heightmap"]["base_m"], 900, 1025 * 1025).reshape(1025, 1025)
    err = np.abs(decode(encode(z, CFG), CFG)[::-1] - z).max()
    assert err < CFG["heightmap"]["range_m"] / 65535
