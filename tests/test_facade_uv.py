"""계약 C1(외벽 UV, D-011): glb 를 다시 읽어 프리미티브 구성·UV 범위·감김을 고정한다."""
import numpy as np
import pytest
from shapely.geometry import Polygon

from pipeline.buildings import building_primitives, facade_spec, wall_bays
from pipeline.config import load_config
from pipeline.meshio import Primitive, from_gltf_axes, from_gltf_uv, read_glb_nodes, write_glb_nodes

CFG = load_config()


def _roundtrip(tmp_path, poly, base, height, cls="residential", bay=3.0, fh=2.8, roof=4.0):
    prims = building_primitives(poly, base, height, cls, bay, fh, roof)
    p = tmp_path / "b.glb"
    assert write_glb_nodes(p, [("B_000001", prims)]) == 1
    nodes = read_glb_nodes(p)
    assert [n["name"] for n in nodes] == ["B_000001"]
    return nodes[0]["primitives"]


def test_wall_bays_rule():
    assert wall_bays(10.0, 3.0) == 3            # round(3.33)
    assert wall_bays(4.4, 3.0) == 1
    assert wall_bays(4.6, 3.0) == 2             # round(1.53)
    assert wall_bays(0.2, 3.0) == 1             # 짧은 변도 한 칸


def test_two_primitives_same_attributes_and_materials(tmp_path):
    poly = Polygon([(0, 0), (10, 0), (10, 7), (0, 7)])
    wall, roof = _roundtrip(tmp_path, poly, 50.0, 8.4)
    assert set(wall["attributes"]) == set(roof["attributes"]) == {"POSITION", "NORMAL", "TEXCOORD_0"}
    assert (wall["material"], roof["material"]) == ("M_residential_wall", "M_roof")
    # 바닥면 없음: 지붕은 모두 지붕 높이, 벽은 바닥~지붕
    assert np.allclose(from_gltf_axes(roof["attributes"]["POSITION"])[:, 2], 58.4, atol=1e-4)
    z = from_gltf_axes(wall["attributes"]["POSITION"])[:, 2]
    assert np.isclose(z.min(), 50.0) and np.isclose(z.max(), 58.4, atol=1e-4)


def test_wall_uv_range_and_integer_bays(tmp_path):
    poly = Polygon([(0, 0), (10, 0), (10, 7), (0, 7)])         # 변 10, 7, 10, 7 m → 칸 3, 2, 3, 2
    wall, _ = _roundtrip(tmp_path, poly, 50.0, 3 * 2.8)
    raw = wall["attributes"]["TEXCOORD_0"]
    uv = from_gltf_uv(raw)
    # glb 에는 1 − v 를 쓴다 → glb v 범위 [1 − 3, 1]
    assert np.isclose(raw[:, 1].max(), 1.0) and np.isclose(raw[:, 1].min(), 1.0 - 3.0, atol=1e-5)
    # Unity 기준 v ∈ [0, height ÷ 층고] = [0, 3]
    assert np.isclose(uv[:, 1].min(), 0.0) and np.isclose(uv[:, 1].max(), 3.0, atol=1e-5)
    q = uv[:, 0].reshape(-1, 4)                                 # 벽 사각형마다 (U0, U0+n, U0+n, U0)
    assert np.allclose(q, np.round(q), atol=1e-5)
    assert list(np.round(q[:, 1] - q[:, 0]).astype(int)) == [3, 2, 3, 2]
    assert np.isclose(q[:, 1].max(), 10)                        # 링 누적 칸 수 3+2+3+2
    # v 는 높이에 비례: 바닥 정점 0, 지붕 정점 3
    V = from_gltf_axes(wall["attributes"]["POSITION"])
    assert np.allclose(uv[:, 1], (V[:, 2] - 50.0) / 2.8, atol=1e-4)


def test_hole_ring_and_outward_normals(tmp_path):
    poly = Polygon([(0, 0), (12, 0), (12, 12), (0, 12)], [[(4, 4), (8, 4), (8, 8), (4, 8)]])
    wall, roof = _roundtrip(tmp_path, poly, 10.0, 3.5, cls="commercial", bay=4.0, fh=3.5)
    V = from_gltf_axes(wall["attributes"]["POSITION"])
    N = from_gltf_axes(wall["attributes"]["NORMAL"])
    F = wall["indices"]
    assert len(F) == 2 * (4 + 4)                                # 바깥 4변 + 구멍 4변
    c = V[F].mean(axis=1)
    n = N[F[:, 0]]
    radial = c[:, :2] - [6.0, 6.0]
    outer = np.max(np.abs(radial), axis=1) > 5.9
    # 바깥 벽은 중심에서 멀어지는 쪽, 구멍(안뜰) 벽은 중심 쪽을 본다(건물 몸체 바깥)
    assert np.all((n[outer, :2] * radial[outer]).sum(1) > 0)
    assert np.all((n[~outer, :2] * radial[~outer]).sum(1) < 0)
    # 감김(반시계)과 법선이 같은 쪽
    cr = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    assert np.all((cr * n).sum(1) > 0)
    # 지붕: 위를 보고 구멍은 비어 있다, UV = (E − 최소 E, N − 최소 N) ÷ 4 m
    RV = from_gltf_axes(roof["attributes"]["POSITION"])
    RF = roof["indices"]
    rc = np.cross(RV[RF[:, 1]] - RV[RF[:, 0]], RV[RF[:, 2]] - RV[RF[:, 0]])
    assert np.all(rc[:, 2] > 0)
    assert np.isclose(np.abs(rc[:, 2]).sum() / 2, 12 * 12 - 4 * 4)
    ruv = from_gltf_uv(roof["attributes"]["TEXCOORD_0"])
    assert np.allclose(ruv, RV[:, :2] / 4.0, atol=1e-5)
    uv = from_gltf_uv(wall["attributes"]["TEXCOORD_0"])
    assert np.isclose(uv[:, 1].max(), 1.0, atol=1e-5)           # 1층 × 3.5 m ÷ 3.5 m


def test_writer_rejects_mixed_attribute_sets(tmp_path):
    a = Primitive(position=np.zeros((3, 3)), normal=np.tile([0, 0, 1.0], (3, 1)), indices=np.array([[0, 1, 2]]),
                  material="M_x", uv=np.zeros((3, 2)))
    b = Primitive(position=np.zeros((3, 3)), normal=np.tile([0, 0, 1.0], (3, 1)), indices=np.array([[0, 1, 2]]),
                  material="M_y")
    with pytest.raises(ValueError):
        write_glb_nodes(tmp_path / "x.glb", [("B_1", [a, b])])


def test_facade_spec_from_config():
    f = facade_spec(CFG)
    assert f["bay_m"] == {"residential": 3.0, "commercial": 4.0, "public": 4.0, "industrial": 6.0, "other": 3.5}
    assert f["roof_tile_m"] == 4.0
    assert f["floor_height_m"]["residential"] == 2.8 and f["floor_height_m"]["other"] == 3.5
