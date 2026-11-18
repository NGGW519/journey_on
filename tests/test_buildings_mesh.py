"""건물 규칙(기획서)과 glTF 축 규약 고정."""
import numpy as np
from shapely.geometry import Polygon

from pipeline.buildings import building_height, classify_osm, classify_usage_code, resolve_floors
from pipeline.config import load_config
from pipeline.meshio import GLTF_AXES, axis_test_meshes, extrude, from_gltf_axes, to_gltf_axes

CFG = load_config()


def test_floor_height_rules():
    assert building_height(3, "residential", CFG) == 3 * 2.8
    assert building_height(2, "commercial", CFG) == 7.0


def test_missing_floors_default_one():
    assert resolve_floors(None, CFG) == (1, "default")
    assert resolve_floors("", CFG) == (1, "default")
    assert resolve_floors("0", CFG) == (1, "default")
    assert resolve_floors("5", CFG) == (5, "source")
    assert resolve_floors(250, CFG) == (1, "invalid")


def test_usage_code_classes():
    assert classify_usage_code("02000") == "residential"   # 공동주택
    assert classify_usage_code("04000") == "commercial"    # 2종 근린
    assert classify_usage_code("14000") == "commercial"    # 업무
    assert classify_usage_code("10000") == "public"        # 교육연구
    assert classify_usage_code("17000") == "industrial"    # 공장
    assert classify_usage_code(None) == "other"


def test_osm_classes():
    assert classify_osm({"building": "apartments"})[0] == "residential"
    assert classify_osm({"building": "yes", "shop": "convenience"})[0] == "commercial"
    assert classify_osm({"building": "yes", "amenity": "townhall"})[0] == "public"
    assert classify_osm({"building": "yes"})[0] == "other"


def test_gltf_axes_is_rotation():
    # 행렬식 +1 이면 감김 방향(바깥 법선)이 유지된다
    assert np.isclose(np.linalg.det(GLTF_AXES), 1.0)
    v = np.array([[10.0, 20.0, 5.0]])
    g = to_gltf_axes(v)
    assert np.allclose(g, [[-10.0, 5.0, 20.0]])
    assert np.allclose(from_gltf_axes(g), v)
    # glTFast 는 x 를 뒤집는다 → Unity (X=E, Y=Up, Z=N)
    unity = g * np.array([-1, 1, 1])
    assert np.allclose(unity, [[10.0, 5.0, 20.0]])


def test_extrude_base_and_top():
    m = extrude(Polygon([(0, 0), (10, 0), (10, 5), (0, 5)]), base=42.0, height=8.4)
    assert np.isclose(m.vertices[:, 2].min(), 42.0)
    assert np.isclose(m.vertices[:, 2].max(), 50.4)
    assert m.is_watertight and m.volume > 0


def test_axis_test_long_arm_east():
    east = dict((n, m) for n, m, _ in axis_test_meshes())["AXIS_East100"]
    assert east.vertices[:, 0].max() == 100 and east.vertices[:, 1].max() == 8
