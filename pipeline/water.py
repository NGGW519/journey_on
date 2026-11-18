"""수면 단계: 수면 폴리곤(섬 제외)을 넓혀 타일별로 자르고, 수면 높이장에 얹은 메시를 만든다.

평평한 수면(북한강·청평호)은 상수 높이, 경사 수면(가평천 상류)은 dem 단계가 만든 높이장을 따른다.
같은 메시를 dem 단계도 메모리에서 만든다(`water_surface_from_field`): 교량 상판의 '수면 + 0.2 m' 바닥(C3)을
실제 water.glb 면과 맞추기 위해서다. validate 는 water.glb 를 직접 읽는다(`water_surface_from_tiles`).

보이는 수면 자르기(D-007, 2026-11-18 06a): water 단계는 넓힌 메시를 '최종 지형(RAW 이중선형) − 수면 <
water.shore_clip_above_m' 영역으로 자른다(`clip_to_visible`). Unity 는 water.glb 경계마다 물가 벽을 세우므로, 수면이
지형 아래 묻힌 곳에 벽이 서지 않고 보이는 물가선으로 옮겨진다. 원래 삼각형을 평면 그대로 자르므로 남은 면의 높이는
자르기 전과 같다 → dem 단계가 자르기 전 메시(상위 집합)로 정한 교량 수면 바닥은 water.glb 에서도 지켜진다.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import rasterio
import shapely
import trimesh
from scipy.spatial import Delaunay, cKDTree
from shapely import contains_xy
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from . import sources
from .config import area_paths, read_json, write_json
from .dem import water_polygons
from .grid import area_bounds, area_tiles
from .meshio import from_gltf_axes, write_glb
from .surface import TriSurface
from .tilejson import update_tile_json

GRID_STEP_M = 25.0     # 수면 내부 보조점 간격(경사 수면 표현용)
EDGE_STEP_M = 10.0     # 외곽선 촘촘히
CLIP_WELD_M = 1e-5     # 이웃 삼각형을 따로 자른 교점을 같은 점으로 볼 거리
CLIP_MIN_SEP_M = 0.08  # Unity 물가 벽은 정점을 5 cm 격자로 묶어 변을 센다 → 자르며 생긴 정점은 다른 정점과 이만큼(> √2·5 cm) 띄운다
CLIP_CHUNK = 300_000   # 격자 수면 높이 표본 한 번에 잴 점 수(메모리)


def _sample_level(field: np.ndarray, T, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    inv = ~T
    cols, rows = inv @ (xs, ys)
    r = np.clip(np.round(rows).astype(int), 0, field.shape[0] - 1)
    c = np.clip(np.round(cols).astype(int), 0, field.shape[1] - 1)
    return field[r, c]


def _fill_level(field: np.ndarray) -> np.ndarray:
    """수면 밖(NaN)은 가장 가까운 수면 높이로 채워 넓힌 가장자리도 높이를 갖게 한다."""
    from scipy import ndimage
    nan = np.isnan(field)
    if nan.all():
        return field
    idx = ndimage.distance_transform_edt(nan, return_distances=False, return_indices=True)
    return field[tuple(idx)]


def triangulate(poly: Polygon) -> tuple[np.ndarray, np.ndarray]:
    """폴리곤 내부를 보조점 포함 Delaunay 로 삼각분할(2D)."""
    pts = []
    for ring in [poly.exterior, *poly.interiors]:
        seg = ring.segmentize(EDGE_STEP_M)
        pts.extend(seg.coords[:-1])
    minx, miny, maxx, maxy = poly.bounds
    gx, gy = np.meshgrid(np.arange(minx, maxx, GRID_STEP_M), np.arange(miny, maxy, GRID_STEP_M))
    gx, gy = gx.ravel(), gy.ravel()
    inner = poly.buffer(-EDGE_STEP_M / 2)
    if not inner.is_empty and gx.size:
        m = contains_xy(inner, gx, gy)
        pts.extend(zip(gx[m], gy[m]))
    P = np.unique(np.asarray(pts, dtype=np.float64), axis=0)
    if len(P) < 3:
        return P, np.zeros((0, 3), dtype=np.int64)
    tri = Delaunay(P)
    c = P[tri.simplices].mean(axis=1)
    keep = contains_xy(poly, c[:, 0], c[:, 1])
    return P, tri.simplices[keep]


def tile_water_meshes(cfg: dict[str, Any], area: str, polys, field_filled: np.ndarray, T):
    """타일별 수면 메시: {타일 이름: [(k, P 월드 (n, 2), F, 높이 (n,))]}(k = 노드 번호 W_<k>).
    water 단계와 dem 단계가 같은 함수를 쓴다."""
    bounds = area_bounds(cfg, area)
    overlap = cfg["water"]["shore_overlap_m"]
    out: dict[str, list] = {}
    if not polys:
        return out
    wet = unary_union([p.buffer(overlap) for p in polys]).intersection(box(*bounds))
    for t in area_tiles(cfg, area):
        part = wet.intersection(t.polygon)
        geoms = [g for g in getattr(part, "geoms", [part]) if isinstance(g, Polygon) and g.area > 1.0]
        meshes = []
        for k, g in enumerate(geoms):
            P, F = triangulate(g)
            if len(F) == 0:
                continue
            meshes.append((k, P, F, _sample_level(field_filled, T, P[:, 0], P[:, 1])))
        out[t.name] = meshes
    return out


def surface_of(meshes: dict[str, list]) -> TriSurface:
    """tile_water_meshes 결과 → 높이 표본기(삼각형 안 선형 보간 = water.glb 면)."""
    parts = [(np.column_stack([P, z]), F) for ms in meshes.values() for _, P, F, z in ms]
    return TriSurface.concat(parts)


def water_surface_from_field(cfg: dict[str, Any], area: str, field: np.ndarray, T, polys) -> TriSurface:
    """dem 단계용: 메모리의 수면 높이장(수면 밖 NaN)으로 water 단계와 같은 메시를 만들어 표본기로."""
    return surface_of(tile_water_meshes(cfg, area, polys, _fill_level(np.asarray(field, dtype=np.float64)), T))


def water_surface_from_tiles(cfg: dict[str, Any], area: str) -> TriSurface:
    """validate·skirt 용: 타일 water.glb 를 읽어 표본기로(월드 좌표)."""
    ap = area_paths(cfg, area)
    parts = []
    for t in area_tiles(cfg, area):
        tj = read_json(ap.tile_dir(t.name) / "tile.json", {}) or {}
        if not tj.get("water"):
            continue
        sc = trimesh.load(ap.tile_dir(t.name) / tj["water"]["file"], force="scene")
        for g in sc.geometry.values():
            parts.append((from_gltf_axes(np.asarray(g.vertices)) + [t.e0, t.n0, 0.0], np.asarray(g.faces)))
    return TriSurface.concat(parts)


# --------------------------------------------------------------------------- 보이는 수면 자르기(D-007, 06a)
def clip_params(cfg: dict[str, Any]) -> dict[str, float]:
    """config water.shore_clip_*: 자르는 기준(지형 − 수면 < above_m), 등고선 단순화 허용 오차, 작은 조각·구멍 면적."""
    wc = cfg["water"]
    return {"above_m": float(wc.get("shore_clip_above_m", 0.4)),
            "simplify_m": float(wc.get("shore_clip_simplify_m", 0.15)),
            "min_area_m2": float(wc.get("shore_clip_min_area_m2", 50.0))}


def mesh_footprint(meshes: dict[str, list]):
    """tile_water_meshes 결과의 덮는 면(월드). 노드마다 삼각형 커버리지 합집합 → 전체 합집합."""
    parts = []
    for ms in meshes.values():
        for _, P, F, _ in ms:
            tri = shapely.polygons(P[F])
            try:
                u = shapely.coverage_union_all(tri)
                if not u.is_valid:
                    raise ValueError
            except Exception:              # 거의 납작한 삼각형 등으로 커버리지 노딩이 깨지면 일반 합집합
                u = shapely.union_all(tri)
            parts.append(u)
    return shapely.union_all(parts) if parts else Polygon()


def delta_grid(Z: np.ndarray, Wfill: np.ndarray, near: np.ndarray, surf: TriSurface, x0: float, y0: float,
               step: float) -> np.ndarray:
    """표본 격자(첫 행 = 남쪽, (r, c) = (y0 + r·step, x0 + c·step))의 지형 − 수면. near 밖은 NaN.

    Z: 지형(RAW 표본 그대로 → 이 격자 위 이중선형 = Unity 지형). 수면은 메시(surf)가 덮는 곳은 메시 면,
    덮지 않는 곳은 Wfill(가장 가까운 수면 높이)이다. 순수 함수(테스트 대상)."""
    D = np.full(Z.shape, np.nan, dtype=np.float32)
    rr, cc = np.nonzero(near)
    w = Wfill[rr, cc].astype(np.float64)
    xs, ys = x0 + cc * step, y0 + rr * step
    for a in range(0, len(rr), CLIP_CHUNK):
        h = surf.height(xs[a:a + CLIP_CHUNK], ys[a:a + CLIP_CHUNK])
        w[a:a + CLIP_CHUNK] = np.where(np.isfinite(h), h, w[a:a + CLIP_CHUNK])
    D[rr, cc] = (Z[rr, cc] - w).astype(np.float32)
    return D


def below_region(D: np.ndarray, x0: float, y0: float, step: float, level: float, simplify_m: float):
    """격자 D(첫 행 = 남쪽, NaN = 계산 안 함)에서 D < level 인 영역 다각형(등고선, 칸 안 선형 보간 = 이중선형 변 위 정확).
    단순화(Douglas–Peucker, 위상 유지) 뒤 합집합. 순수 함수(테스트 대상)."""
    import contourpy
    H, W = D.shape
    z = np.ma.masked_invalid(D.astype(np.float64))
    if z.mask.all():
        return Polygon()
    gen = contourpy.contour_generator(x0 + np.arange(W) * step, y0 + np.arange(H) * step, z, name="serial",
                                      fill_type=contourpy.FillType.OuterOffset, corner_mask=True)
    lo = float(np.nanmin(D)) - 1.0
    if lo >= level:
        return Polygon()
    pts, offs = gen.filled(lo, level)
    polys = []
    for p, o in zip(pts, offs):
        rings = [p[o[i]:o[i + 1]] for i in range(len(o) - 1)]
        rings = [r for r in rings if len(r) >= 4]
        if not rings:
            continue
        g = Polygon(rings[0], rings[1:])
        if not g.is_valid:
            g = shapely.make_valid(g)
        polys.append(g)
    if not polys:
        return Polygon()
    arr = shapely.simplify(np.asarray(polys, dtype=object), simplify_m, preserve_topology=True)
    arr = shapely.make_valid(arr)
    out = shapely.union_all(arr)
    return _polygonal(out)


def _polygonal(g):
    """(Multi)Polygon 만 남긴다(선·점 조각 제거)."""
    parts = [p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and not p.is_empty]
    if not parts:
        return Polygon()
    return shapely.union_all(parts) if len(parts) > 1 else parts[0]


def clean_region(footprint, region, min_area: float):
    """자를 영역 정리: 메시 안에 갇힌 작은 구멍(< min_area, 섬 구멍 제외)은 메우고, 남는 작은 조각(< min_area)은 목록으로.
    돌려주는 값: (메운 영역, 작은 조각 다각형 목록, 통계). 순수 함수(테스트 대상)."""
    fp_in = footprint.buffer(1e-6)
    V = _polygonal(shapely.intersection(footprint, region))
    fill = []
    for p in shapely.get_parts(V):
        for ring in p.interiors:
            h = Polygon(ring)
            if h.area < min_area and fp_in.contains(h):
                fill.append(h)
    region2 = _polygonal(shapely.union_all([region, *fill])) if fill else region
    V2 = _polygonal(shapely.intersection(footprint, region2)) if fill else V
    small = [p for p in shapely.get_parts(V2) if p.area < min_area]
    st = {"holes_filled": len(fill), "holes_filled_m2": round(float(sum(h.area for h in fill)), 1),
          "pieces_removed": len(small), "pieces_removed_m2": round(float(sum(p.area for p in small)), 1),
          "visible_pieces": int(len(shapely.get_parts(V2)) - len(small))}
    return region2, small, st


def _orient_ccw(T: np.ndarray) -> np.ndarray:
    """(m, 3, 2) 삼각형 → (m, 3) 순서(반시계 = 위를 봄)."""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    order = np.tile(np.arange(3), (len(T), 1))
    order[cross < 0] = [0, 2, 1]
    return order


def _union_find_merge(n: int, pairs: np.ndarray) -> np.ndarray:
    """쌍 (i, j) 를 같은 점으로 묶어 대표 번호(묶음 안 가장 작은 번호)를 돌려준다."""
    parent = np.arange(n)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, j in pairs:
        ri, rj = find(int(i)), find(int(j))
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)
    return np.array([find(i) for i in range(n)])


def clip_triangles(P: np.ndarray, F: np.ndarray, z: np.ndarray, region, drop=None,
                   min_sep: float = CLIP_MIN_SEP_M) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, int]]:
    """삼각형 메시(P (n, 2) 월드, F, 정점 높이 z)를 region 으로 자른다. 순수 함수(테스트 대상).

    - region 안에 통째로 든 삼각형은 그대로, 밖은 버리고, 걸친 삼각형만 잘라 조각을 제약 Delaunay 로 다시 나눈다.
      새 정점 높이는 원래 삼각형 평면(무게중심 좌표) → 남은 면의 높이는 자르기 전과 같다.
    - 이웃 삼각형을 따로 자른 교점은 CLIP_WELD_M 안에서 합친다(T 자 이음 없음).
    - 자르며 생긴 정점이 다른 정점과 min_sep 안이면 그 변을 접는다(Unity 5 cm 격자 묶음에서 겹치지 않게).
    - drop: 무게중심이 이 다각형들 안인 삼각형은 뺀다(작은 조각).
    돌려주는 값: (P2, F2, z2, 통계)."""
    P = np.asarray(P, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    z = np.asarray(z, dtype=np.float64)
    st = {"tris_in": int(len(F)), "kept": 0, "cut": 0, "dropped": 0, "new_tris": 0, "collapsed": 0, "small_dropped": 0}
    if len(F) == 0 or region.is_empty:
        st["dropped"] = int(len(F))
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int64), np.zeros(0), st
    tris = shapely.polygons(P[F])
    shapely.prepare(region)
    inside = shapely.contains(region, tris)
    hit = shapely.intersects(region, tris)
    cut = hit & ~inside
    st["kept"], st["cut"], st["dropped"] = int(inside.sum()), int(cut.sum()), int((~hit).sum())
    keepF = F[inside]
    ci = np.nonzero(cut)[0]
    newV, newz, newF = [], [], []
    if len(ci):
        pieces = shapely.intersection(tris[ci], region)
        parts, pidx = shapely.get_parts(pieces, return_index=True)
        ok = (shapely.get_type_id(parts) == 3) & (shapely.area(parts) > 1e-8)
        parts, pidx = parts[ok], pidx[ok]
        if len(parts):
            tri_gc = shapely.constrained_delaunay_triangles(parts)
            tparts, tidx = shapely.get_parts(tri_gc, return_index=True)
            isp = shapely.get_type_id(tparts) == 3
            tparts, tidx = tparts[isp], tidx[isp]
            if len(tparts):
                C = shapely.get_coordinates(tparts).reshape(-1, 4, 2)[:, :3]
                area2 = np.abs((C[:, 1, 0] - C[:, 0, 0]) * (C[:, 2, 1] - C[:, 0, 1])
                               - (C[:, 1, 1] - C[:, 0, 1]) * (C[:, 2, 0] - C[:, 0, 0]))
                good = area2 > 2e-10
                C, tidx = C[good], tidx[good]
                par = ci[pidx[tidx]]                                  # 원래 삼각형 번호
                A, B, Cc = P[F[par, 0]], P[F[par, 1]], P[F[par, 2]]
                za, zb, zc = z[F[par, 0]], z[F[par, 1]], z[F[par, 2]]
                det = (B[:, 1] - Cc[:, 1]) * (A[:, 0] - Cc[:, 0]) + (Cc[:, 0] - B[:, 0]) * (A[:, 1] - Cc[:, 1])
                det = np.where(np.abs(det) > 1e-12, det, 1e-12)
                order = _orient_ccw(C)
                C = np.take_along_axis(C, order[:, :, None], axis=1)
                Q = C.reshape(-1, 2)
                rep = np.repeat(np.arange(len(C)), 3)
                l1 = ((B[rep, 1] - Cc[rep, 1]) * (Q[:, 0] - Cc[rep, 0]) + (Cc[rep, 0] - B[rep, 0]) * (Q[:, 1] - Cc[rep, 1])) / det[rep]
                l2 = ((Cc[rep, 1] - A[rep, 1]) * (Q[:, 0] - Cc[rep, 0]) + (A[rep, 0] - Cc[rep, 0]) * (Q[:, 1] - Cc[rep, 1])) / det[rep]
                l3 = 1.0 - l1 - l2
                newV.append(Q)
                newz.append(l1 * za[rep] + l2 * zb[rep] + l3 * zc[rep])
                newF.append(np.arange(len(Q)).reshape(-1, 3))
    nP = len(P)
    if newV:
        Qa, za_, Fa = newV[0], newz[0], newF[0] + nP
    else:
        Qa, za_, Fa = np.zeros((0, 2)), np.zeros(0), np.zeros((0, 3), dtype=np.int64)
    st["new_tris"] = int(len(Fa))
    allV = np.concatenate([P, Qa])
    allz = np.concatenate([z, za_])
    allF = np.concatenate([keepF, Fa]).astype(np.int64)
    # 작은 조각 빼기(무게중심)
    if drop and len(allF):
        cen = allV[allF].mean(axis=1)
        tree = shapely.STRtree(list(drop))
        q = tree.query(shapely.points(cen), predicate="within")
        if q.size:
            bad = np.zeros(len(allF), dtype=bool)
            bad[np.unique(q[0])] = True
            st["small_dropped"] = int(bad.sum())
            allF = allF[~bad]
    # 1) 교점 합치기: 새 정점 ↔ 모든 정점(CLIP_WELD_M)
    rep = np.arange(len(allV))
    if len(Qa):
        pairs = cKDTree(allV).query_pairs(CLIP_WELD_M, output_type="ndarray")
        if len(pairs):
            rep = _union_find_merge(len(allV), pairs)
    allF = rep[allF]
    # 2) 짧은 변 접기(새 정점이 낀 변만, 새 정점을 다른 쪽으로 옮긴다)
    for _ in range(8):
        e = np.concatenate([allF[:, [0, 1]], allF[:, [1, 2]], allF[:, [2, 0]]])
        e = e[e[:, 0] != e[:, 1]]
        L = np.hypot(*(allV[e[:, 0]] - allV[e[:, 1]]).T)
        short = (L < min_sep) & ((e[:, 0] >= nP) | (e[:, 1] >= nP))
        if not short.any():
            break
        m = np.arange(len(allV))
        for a, b in e[short]:
            a, b = int(m[a]), int(m[b])
            if a == b:
                continue
            src, dst = (a, b) if (a >= nP and (b < nP or a > b)) else (b, a)
            if src < nP:
                continue
            m[src] = dst
            st["collapsed"] += 1
        # 경로 압축
        for _k in range(4):
            m = m[m]
        allF = m[allF]
    allF = allF[(allF[:, 0] != allF[:, 1]) & (allF[:, 1] != allF[:, 2]) & (allF[:, 2] != allF[:, 0])]
    # 쓰는 정점만
    used, inv = np.unique(allF, return_inverse=True)
    F2 = inv.reshape(-1, 3)
    P2, z2 = allV[used], allz[used]
    # 감김: 위를 보게
    o = _orient_ccw(P2[F2])
    F2 = np.take_along_axis(F2, o, axis=1)
    return P2, F2, z2, st


def clip_to_visible(cfg: dict[str, Any], area: str, meshes: dict[str, list], field_filled: np.ndarray, T):
    """넓힌 수면 메시를 '최종 지형 − 수면 < shore_clip_above_m' 영역으로 자른다(D-007, 06a).

    돌려주는 값: (자른 메시 {타일: [(k, P, F, z)]}, 통계). 지형 RAW 가 없으면 자르지 않는다."""
    from rasterio.features import rasterize
    from scipy import ndimage

    from .heightmap import sample_spacing
    from .terrain import raw_mosaic
    cp = clip_params(cfg)
    Z = raw_mosaic(cfg, area)
    if Z is None:
        return meshes, {"skipped": "타일 RAW 없음(dem 단계)"}
    minx, miny, maxx, maxy = area_bounds(cfg, area)
    step = sample_spacing(cfg)
    Wfill = np.asarray(field_filled, dtype=np.float32)[::-1]          # water_level.tif(첫 행 = 북) → 첫 행 = 남
    if Wfill.shape != Z.shape:
        raise SystemExit(f"water_level.tif 격자 {Wfill.shape} ≠ RAW 모자이크 {Z.shape} → dem 단계를 다시 실행하세요.")
    fp = mesh_footprint(meshes)
    near = rasterize([(fp, 1)], out_shape=Z.shape, transform=T, fill=0, all_touched=True, dtype="uint8").astype(bool)[::-1]
    near = ndimage.binary_dilation(near, iterations=3)
    surf = surface_of(meshes)
    D = delta_grid(Z, Wfill, near, surf, minx, miny, step)
    del Z, Wfill
    region = below_region(D, minx, miny, step, cp["above_m"], cp["simplify_m"])
    region, small, cst = clean_region(fp, region, cp["min_area_m2"])
    out: dict[str, list] = {}
    tot = {"kept": 0, "cut": 0, "dropped": 0, "new_tris": 0, "collapsed": 0, "small_dropped": 0, "tris_in": 0}
    for t in area_tiles(cfg, area):
        ms = meshes.get(t.name, [])
        if not ms:
            out[t.name] = []
            continue
        reg_t = _polygonal(shapely.intersection(region, box(*t.bounds).buffer(1.0, join_style="mitre")))
        sm_t = [p for p in small if p.intersects(box(*t.bounds))]
        res = []
        for k, P, F, zl in ms:
            P2, F2, z2, st = clip_triangles(P, F, zl, reg_t, sm_t)
            for key in tot:
                tot[key] += st[key]
            if len(F2):
                res.append((k, P2, F2, z2))
        out[t.name] = res
    area_before = float(shapely.area(fp))
    area_after = float(sum(_tri_area(P, F).sum() for ms in out.values() for _, P, F, _ in ms))
    stats = {**cp, **cst, **tot, "area_before_m2": round(area_before, 0), "area_after_m2": round(area_after, 0),
             "grid_samples": int(np.isfinite(D).sum())}
    return out, stats


def _tri_area(P: np.ndarray, F: np.ndarray) -> np.ndarray:
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    return 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))


def body_areas(cfg: dict[str, Any], area: str, meshes: dict[str, list], owner: np.ndarray, T, polys) -> dict[int, float]:
    """메시 넓이를 수면체별로(삼각형 무게중심의 수면체 번호 래스터 값, 수면 밖이면 가장 가까운 폴리곤 번호)."""
    out: dict[int, float] = {}
    tree = shapely.STRtree(polys) if polys else None
    inv = ~T
    H, W = owner.shape
    for ms in meshes.values():
        for _, P, F, _ in ms:
            if not len(F):
                continue
            a = _tri_area(P, F)
            cen = P[F].mean(axis=1)
            c, r = inv @ (cen[:, 0], cen[:, 1])
            r, c = np.clip(np.floor(r).astype(int), 0, H - 1), np.clip(np.floor(c).astype(int), 0, W - 1)
            b = owner[r, c].astype(np.int64)
            miss = b < 0
            if miss.any() and tree is not None:
                b[miss] = tree.query_nearest(shapely.points(cen[miss]), return_distance=False, all_matches=False)[1]
            ub, inv_b = np.unique(b, return_inverse=True)
            sums = np.bincount(inv_b.ravel(), weights=a)
            for bid, s in zip(ub.tolist(), sums.tolist()):
                out[int(bid)] = out.get(int(bid), 0.0) + float(s)
    return out


def run(cfg: dict[str, Any], area: str, args=None) -> None:
    ap = area_paths(cfg, area).ensure()
    bounds = area_bounds(cfg, area)
    level_path = ap.interim / "water_level.tif"
    if not level_path.exists():
        raise SystemExit("dem 단계를 먼저 실행하세요(water_level.tif 없음).")
    polys = water_polygons(cfg, area, bounds)
    tiles = area_tiles(cfg, area)
    if not polys:
        print("  수면 원천이 없습니다 → 건너뜀")
        for t in tiles:
            update_tile_json(ap.tile_dir(t.name), t, cfg, "water", None)
        return
    with rasterio.open(level_path) as src:
        field = _fill_level(src.read(1).astype(np.float64))
        T = src.transform
    full = tile_water_meshes(cfg, area, polys, field, T)
    meshes, cst = clip_to_visible(cfg, area, full, field, T)
    bodies = read_json(ap.interim / "water_bodies.json", [])
    owner_p = ap.interim / "water_body_id.tif"
    per_body = {}
    if owner_p.exists():
        with rasterio.open(owner_p) as s:
            owner = s.read(1)
        b0 = body_areas(cfg, area, full, owner, T, polys)
        b1 = body_areas(cfg, area, meshes, owner, T, polys)
        per_body = {str(k): {"before_m2": round(b0.get(k, 0.0), 0), "after_m2": round(b1.get(k, 0.0), 0),
                             "kept": round(b1.get(k, 0.0) / b0[k], 4) if b0.get(k) else None}
                    for k in sorted(set(b0) | set(b1))}
    prov = sources.provenance(sources.select(cfg, "water_areas"))
    total = 0
    nodes_before = sum(len(v) for v in full.values())
    for t in tiles:
        d = ap.tile_dir(t.name)
        ms = meshes.get(t.name, [])
        if not ms:
            (d / "water.glb").unlink(missing_ok=True)
            update_tile_json(d, t, cfg, "water", None)
            continue
        items, levels = [], []
        for k, P, F, zl in ms:
            levels.append(float(np.median(zl)))
            V = np.column_stack([P[:, 0] - t.e0, P[:, 1] - t.n0, zl])
            mesh = trimesh.Trimesh(vertices=V, faces=F, process=False)   # 감김은 clip_triangles 가 위를 보게 맞췄다
            if mesh.face_normals[:, 2].mean() < 0:
                mesh.invert()
            items.append((f"W_{k}", mesh, "water"))
        n = write_glb(d / "water.glb", items)
        update_tile_json(d, t, cfg, "water", {
            "count": n, "file": "water.glb",
            "level_m": round(float(np.median(levels)), 2) if levels else None,
            "interim": bool(prov and prov["interim"]),
        } if n else None)
        total += n
    write_json(ap.reports / "water.json", {"clip": cst, "nodes_before": nodes_before, "nodes_after": total,
                                           "bodies": per_body})
    print(f"  수면 메시 {total}개(자르기 전 {nodes_before}), 수면체 {[(b['kind'], b['level_m']) for b in bodies]}")
    if "skipped" not in cst:
        print(f"  보이는 수면 자르기(지형 − 수면 < {cst['above_m']} m, 단순화 {cst['simplify_m']} m): 넓이 "
              f"{cst['area_before_m2']:,.0f} → {cst['area_after_m2']:,.0f} m², 걸친 삼각형 {cst['cut']:,}개 자름·"
              f"{cst['dropped']:,}개 버림, 메운 구멍 {cst['holes_filled']}곳 {cst['holes_filled_m2']} m², "
              f"뺀 작은 조각 {cst['pieces_removed']}곳 {cst['pieces_removed_m2']} m², 접은 짧은 변 {cst['collapsed']}")
    else:
        print(f"  보이는 수면 자르기 건너뜀: {cst['skipped']}")
