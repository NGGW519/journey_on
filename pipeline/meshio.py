"""glTF(GLB) 쓰기·읽기와 축 변환. 축 변환은 to_gltf_axes() 한 곳에서만 한다.

로컬 좌표(E, N, Up) → glTF (−E, Up, N). glTFast 는 가져올 때 X 부호를 뒤집으므로
Unity 에서는 (E, Up, N) = (X, Y, Z) 가 된다. 이 변환은 회전(행렬식 +1)이라 삼각형 감김 방향이
유지된다.

쓰기 방식 두 가지:
- `write_glb`: trimesh(노드 하나에 프리미티브 하나, 위치·법선만). water.glb, axis_test.glb.
- `write_glb_nodes`: pygltflib 직접 쓰기(노드 하나에 프리미티브 여러 개, TEXCOORD_0·COLOR_0 선택).
  buildings.glb(계약 C1: [0] 벽, [1] 지붕), bridges.glb(C3), skirt.glb(C4).
UV 는 Unity 기준 값으로 받고, glb 에는 `to_gltf_uv()`(v → 1 − v) 한 곳에서 바꿔 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pygltflib
import trimesh
from shapely.geometry import Polygon
from trimesh.visual.material import PBRMaterial

GLTF_AXES = np.array([[-1.0, 0.0, 0.0],   # glTF x = −E
                      [0.0, 0.0, 1.0],    # glTF y = Up
                      [0.0, 1.0, 0.0]])   # glTF z = N

CLASS_COLORS = {  # glb 단독 확인용 기본색. Unity 에서는 프로젝트 재질로 바꾼다.
    "residential": (0.86, 0.80, 0.70, 1.0),
    "commercial": (0.70, 0.78, 0.86, 1.0),
    "public": (0.85, 0.72, 0.62, 1.0),
    "industrial": (0.66, 0.66, 0.66, 1.0),
    "other": (0.78, 0.78, 0.74, 1.0),
    "water": (0.20, 0.38, 0.52, 0.85),
    "axis": (0.90, 0.20, 0.20, 1.0),
    "roof": (0.55, 0.55, 0.53, 1.0),
    "bridge_road": (0.64, 0.63, 0.60, 1.0),
    "bridge_foot": (0.72, 0.60, 0.47, 1.0),
    "bridge_rail": (0.36, 0.34, 0.33, 1.0),
    "skirt": (1.0, 1.0, 1.0, 1.0),
}


def to_gltf_axes(v_local: np.ndarray) -> np.ndarray:
    return np.asarray(v_local, dtype=np.float64) @ GLTF_AXES.T


def from_gltf_axes(v_gltf: np.ndarray) -> np.ndarray:
    return np.asarray(v_gltf, dtype=np.float64) @ GLTF_AXES  # 직교행렬의 역 = 전치


def to_gltf_uv(uv_unity: np.ndarray) -> np.ndarray:
    """Unity 기준 UV(u, v) → glb 에 쓸 값(u, 1 − v). glTFast 가 가져올 때 V 를 뒤집는다(계약 C1)."""
    uv = np.asarray(uv_unity, dtype=np.float64).copy()
    uv[:, 1] = 1.0 - uv[:, 1]
    return uv


def from_gltf_uv(uv_gltf: np.ndarray) -> np.ndarray:
    return to_gltf_uv(uv_gltf)   # 같은 변환(자기 역)


def extrude(poly: Polygon, base: float, height: float) -> trimesh.Trimesh:
    """로컬 좌표 외곽선을 base~base+height 로 압출(로컬 E,N,Up)."""
    m = trimesh.creation.extrude_polygon(poly, height, engine="earcut")
    m.apply_translation([0.0, 0.0, base])
    return m


def _material(cls: str) -> PBRMaterial:
    c = CLASS_COLORS.get(cls, CLASS_COLORS["other"])
    return PBRMaterial(name=f"M_{cls}", baseColorFactor=[int(255 * x) for x in c],
                       metallicFactor=0.0, roughnessFactor=0.9,
                       alphaMode="BLEND" if c[3] < 1 else "OPAQUE")


def write_glb(path: Path, items: Iterable[tuple[str, trimesh.Trimesh, str]]) -> int:
    """items: (노드 이름, 로컬 좌표 메시, 재질 클래스). 평면 음영을 위해 정점을 나눈다."""
    scene = trimesh.Scene()
    n = 0
    for name, mesh, cls in items:
        g = mesh.copy()
        g.vertices = to_gltf_axes(g.vertices)
        g.unmerge_vertices()
        g.visual = trimesh.visual.TextureVisuals(material=_material(cls))
        scene.add_geometry(g, node_name=name, geom_name=name)
        n += 1
    if n == 0:
        return 0
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(scene.export(file_type="glb", include_normals=True))
    return n


# --------------------------------------------------------------------------- pygltflib 직접 쓰기
@dataclass
class Primitive:
    """프리미티브 하나(로컬 E, N, Up 좌표). uv 는 Unity 기준(v 를 뒤집기 전), color 는 선형 RGB 0~1."""
    position: np.ndarray            # (n, 3)
    normal: np.ndarray              # (n, 3)
    indices: np.ndarray             # (m, 3) 바깥에서 볼 때 반시계(로컬 좌표 기준)
    material: str                   # 재질 이름(예: M_residential_wall)
    uv: np.ndarray | None = None    # (n, 2)
    color: np.ndarray | None = None # (n, 3)

    @property
    def attribute_names(self) -> tuple[str, ...]:
        names = ["POSITION", "NORMAL"]
        if self.uv is not None:
            names.append("TEXCOORD_0")
        if self.color is not None:
            names.append("COLOR_0")
        return tuple(names)


def _material_color(name: str) -> list[float]:
    key = name[2:] if name.startswith("M_") else name
    if key.endswith("_wall"):
        key = key[:-5]
    c = CLASS_COLORS.get(key) or CLASS_COLORS.get(key.split("_")[0]) or CLASS_COLORS["other"]
    return [float(x) for x in c]


class _GlbBuilder:
    def __init__(self):
        self.g = pygltflib.GLTF2(asset=pygltflib.Asset(generator="journey_on pipeline (pygltflib)", version="2.0"))
        self.blob = bytearray()
        self.materials: dict[str, int] = {}

    def _view(self, data: bytes, target: int) -> int:
        while len(self.blob) % 4:
            self.blob.append(0)
        off = len(self.blob)
        self.blob.extend(data)
        self.g.bufferViews.append(pygltflib.BufferView(buffer=0, byteOffset=off, byteLength=len(data), target=target))
        return len(self.g.bufferViews) - 1

    def accessor(self, arr: np.ndarray, kind: str, minmax: bool = False) -> int:
        a = np.ascontiguousarray(arr, dtype=np.float32)
        view = self._view(a.tobytes(), pygltflib.ARRAY_BUFFER)
        acc = pygltflib.Accessor(bufferView=view, byteOffset=0, componentType=pygltflib.FLOAT,
                                 count=int(a.shape[0]), type=kind)
        if minmax:
            acc.min = [float(x) for x in a.min(axis=0)]
            acc.max = [float(x) for x in a.max(axis=0)]
        self.g.accessors.append(acc)
        return len(self.g.accessors) - 1

    def indices(self, idx: np.ndarray, n_vertices: int) -> int:
        flat = np.asarray(idx, dtype=np.int64).ravel()
        if n_vertices <= 65535:
            data, ctype = flat.astype("<u2").tobytes(), pygltflib.UNSIGNED_SHORT
        else:
            data, ctype = flat.astype("<u4").tobytes(), pygltflib.UNSIGNED_INT
        view = self._view(data, pygltflib.ELEMENT_ARRAY_BUFFER)
        self.g.accessors.append(pygltflib.Accessor(bufferView=view, byteOffset=0, componentType=ctype,
                                                   count=int(flat.size), type=pygltflib.SCALAR))
        return len(self.g.accessors) - 1

    def material(self, name: str) -> int:
        if name not in self.materials:
            c = _material_color(name)
            self.g.materials.append(pygltflib.Material(
                name=name, doubleSided=False, alphaMode="BLEND" if c[3] < 1 else "OPAQUE",
                pbrMetallicRoughness=pygltflib.PbrMetallicRoughness(baseColorFactor=c, metallicFactor=0.0,
                                                                    roughnessFactor=0.9)))
            self.materials[name] = len(self.g.materials) - 1
        return self.materials[name]

    def primitive(self, p: Primitive) -> pygltflib.Primitive:
        pos = to_gltf_axes(p.position)
        nrm = to_gltf_axes(p.normal)          # 회전이므로 법선도 같은 행렬
        attrs = pygltflib.Attributes(POSITION=self.accessor(pos, pygltflib.VEC3, minmax=True),
                                     NORMAL=self.accessor(nrm, pygltflib.VEC3))
        if p.uv is not None:
            attrs.TEXCOORD_0 = self.accessor(to_gltf_uv(p.uv), pygltflib.VEC2)
        if p.color is not None:
            attrs.COLOR_0 = self.accessor(np.clip(p.color, 0.0, 1.0), pygltflib.VEC3)
        return pygltflib.Primitive(attributes=attrs, indices=self.indices(p.indices, len(pos)),
                                   material=self.material(p.material), mode=pygltflib.TRIANGLES)

    def node(self, name: str, prims: list[Primitive]) -> None:
        mesh = pygltflib.Mesh(name=name, primitives=[self.primitive(p) for p in prims])
        self.g.meshes.append(mesh)
        self.g.nodes.append(pygltflib.Node(name=name, mesh=len(self.g.meshes) - 1))

    def save(self, path: Path) -> None:
        while len(self.blob) % 4:
            self.blob.append(0)
        self.g.buffers = [pygltflib.Buffer(byteLength=len(self.blob))]
        self.g.scenes = [pygltflib.Scene(nodes=list(range(len(self.g.nodes))))]
        self.g.scene = 0
        self.g.set_binary_blob(bytes(self.blob))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = b"".join(self.g.save_to_bytes())
        tmp = Path(str(path) + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)


def write_glb_nodes(path: Path, nodes: Iterable[tuple[str, list[Primitive]]]) -> int:
    """노드 하나에 프리미티브 여러 개를 쓴다(pygltflib). 같은 노드의 프리미티브는 속성 구성이 같아야 한다
    (다르면 glTFast 가 submesh 로 묶지 않고 자식 오브젝트로 나눈다, 계약 C1). 쓴 노드 수를 돌려준다."""
    b = _GlbBuilder()
    n = 0
    for name, prims in nodes:
        prims = [p for p in prims if len(p.indices)]
        if not prims:
            continue
        sets = {p.attribute_names for p in prims}
        if len(sets) != 1:
            raise ValueError(f"{name}: 프리미티브 속성 구성이 다릅니다 {sorted(sets)}")
        b.node(name, prims)
        n += 1
    if n == 0:
        return 0
    b.save(path)
    return n


# --------------------------------------------------------------------------- 읽기(검사·테스트용)
_CTYPE = {pygltflib.FLOAT: "<f4", pygltflib.UNSIGNED_INT: "<u4", pygltflib.UNSIGNED_SHORT: "<u2",
          pygltflib.UNSIGNED_BYTE: "u1", pygltflib.SHORT: "<i2", pygltflib.BYTE: "i1"}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _read_accessor(g: pygltflib.GLTF2, blob: bytes, idx: int) -> np.ndarray:
    acc = g.accessors[idx]
    view = g.bufferViews[acc.bufferView]
    dt = np.dtype(_CTYPE[acc.componentType])
    nc = _NCOMP[acc.type]
    start = (view.byteOffset or 0) + (acc.byteOffset or 0)
    stride = view.byteStride or dt.itemsize * nc
    if stride == dt.itemsize * nc:
        arr = np.frombuffer(blob, dtype=dt, count=acc.count * nc, offset=start)
    else:
        rows = [np.frombuffer(blob, dtype=dt, count=nc, offset=start + i * stride) for i in range(acc.count)]
        arr = np.concatenate(rows) if rows else np.zeros(0, dtype=dt)
    arr = arr.reshape(acc.count, nc) if nc > 1 else arr
    if acc.normalized and dt.kind in "ui":
        arr = arr.astype(np.float64) / float(np.iinfo(dt).max)
    return arr.astype(np.float64) if dt.kind == "f" else arr


def read_glb_nodes(path: Path) -> list[dict]:
    """glb → [{name, primitives: [{attributes: {이름: 배열(glTF 좌표 그대로)}, indices: (m,3), material: 이름}]}]."""
    g = pygltflib.GLTF2().load(str(path))
    if g is None:
        raise ValueError(f"glb 를 읽지 못했습니다: {path}")
    blob = g.binary_blob() or b""
    out = []
    for node in g.nodes:
        if node.mesh is None:
            continue
        prims = []
        for p in g.meshes[node.mesh].primitives:
            attrs = {k: _read_accessor(g, blob, v) for k, v in vars(p.attributes).items()
                     if isinstance(v, int)}
            idx = _read_accessor(g, blob, p.indices).astype(np.int64).reshape(-1, 3) if p.indices is not None \
                else np.arange(len(attrs["POSITION"])).reshape(-1, 3)
            mat = g.materials[p.material].name if p.material is not None else None
            prims.append({"attributes": attrs, "indices": idx, "material": mat})
        out.append({"name": node.name, "primitives": prims})
    return out


def axis_test_meshes() -> list[tuple[str, trimesh.Trimesh, str]]:
    """동쪽(+E)으로 100m 긴 팔, 북쪽(+N)으로 40m 짧은 팔, 원점에 30m 기둥."""
    long_arm = Polygon([(0, 0), (100, 0), (100, 8), (0, 8)])
    short_arm = Polygon([(0, 0), (8, 0), (8, 40), (0, 40)])
    pole = Polygon([(-2, -2), (2, -2), (2, 2), (-2, 2)])
    return [
        ("AXIS_East100", extrude(long_arm, 0, 6), "axis"),
        ("AXIS_North40", extrude(short_arm, 0, 6), "commercial"),
        ("AXIS_Pole30", extrude(pole, 0, 30), "residential"),
    ]
