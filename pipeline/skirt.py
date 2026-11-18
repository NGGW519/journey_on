"""원경 스커트 단계(계약 C4, D-013): 범위 경계 바깥 margin_m 고리 지형 + 범위 밖 수면.

메시 구조: 범위 경계를 d 만큼 넓힌 직사각형 '고리 줄'들을 안쪽부터 바깥쪽으로 이어 붙인다(지퍼 삼각분할).
- d = −inner_overlap_m(60 m): 범위 안 겹침 줄. 높이 = 둘레(±64 m) 범위 지형 최저값 − overlap_drop_m(2 m).
- d = 0: 경계 줄. 정점 간격 = 높이맵 표본 × boundary_samples(약 3.9 m), 높이 = Terrain 경계 높이(RAW 이중선형).
- d = 60, 120, …, near_m(3 km): 간격 60 m. 그 밖 margin_m(12 km)까지 120 m.
높이(d > 0) = Copernicus 원천(EGM2008 정표고, 간격에 맞춘 가우스 평활 후 이중선형)
            + 경계 보정(Terrain − Copernicus, 경계에서 boundary_blend_m 동안 1 → 0).
좌표는 Unity 월드 좌표(E − 원점E, N − 원점N, 표고)이고 glTF 축 변환은 meshio 한 곳에서 한다.
덩어리 SK_<i>(정점 ≤ max_vertices, 이웃 덩어리와 경계 줄을 함께 가져 틈이 없다), 범위 밖 수면 SKW_<i>.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import rasterio
import rasterio.windows
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.warp import Resampling, reproject
from affine import Affine
from scipy import ndimage
from scipy.spatial import Delaunay, cKDTree
from shapely import contains_xy
from shapely.geometry import Polygon, box

from . import sources
from .config import area_paths, rel, write_json
from .grid import area_bounds
from .heightmap import sample_spacing
from .meshio import Primitive, write_glb_nodes
from .surface import TriSurface
from .terrain import TerrainSampler
from .vectors import load_layer

FLAT_RANGE_M = 3.0       # 범위 밖 수면 폴리곤 표고 범위(p95 − p5)가 이보다 크면 경사 수면(하천)
SLOPED_MIN_M = 150.0     # 경사 수면 높이: 이 창(m)의 최저값을 …
SLOPED_SIGMA_M = 120.0   # … 이 반경으로 평활(폴리곤 안 표본끼리)


# --------------------------------------------------------------------------- 고리 줄(순수 함수)
EDGE_INSET_M = 1.0       # 경계 바로 안쪽 줄(d = −1 m). 경계 줄(지형 높이)과 겹침 줄 사이의 가파른 띠를 1 m 폭에 가둔다


def ring_offsets(margin: float, near: float, s1: float, s2: float, overlap: float) -> list[tuple[float, float]]:
    """(d, 줄 위 정점 간격) 목록. d = −overlap(겹침 줄), −EDGE_INSET_M, 0(경계 줄, 간격은 따로),
    s1 간격으로 near 까지, s2 간격으로 margin 까지."""
    out = [(-float(overlap), float(s1))]
    if overlap > EDGE_INSET_M + 1e-6:
        out.append((-EDGE_INSET_M, 0.0))       # 간격 0 = 경계 줄과 같은 촘촘한 간격(삼각형이 옆으로 길어지지 않게)
    out.append((0.0, 0.0))
    d = 0.0
    while d < near - 1e-6:
        d = min(near, d + s1)
        out.append((d, float(s1)))
    while d < margin - 1e-6:
        d = min(margin, d + s2)
        out.append((d, float(s2)))
    return out


def loop_points(bounds, d: float, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """경계를 d 만큼 넓힌 직사각형 위 정점(남서 모서리에서 반시계)과 매개변수 t(변 번호 + 변 안 비율, 모서리 = 정수)."""
    minx, miny, maxx, maxy = bounds
    c = np.array([(minx - d, miny - d), (maxx + d, miny - d), (maxx + d, maxy + d), (minx - d, maxy + d)])
    pts, ts = [], []
    for k in range(4):
        a, b = c[k], c[(k + 1) % 4]
        m = max(1, int(np.ceil(float(np.hypot(*(b - a))) / spacing - 1e-9)))
        f = np.arange(m) / m
        pts.append(a + (b - a) * f[:, None])
        ts.append(k + f)
    return np.concatenate(pts), np.concatenate(ts)


def zip_loops(tA: np.ndarray, tB: np.ndarray, offA: int, offB: int) -> np.ndarray:
    """닫힌 줄 A·B(같은 t 매개변수, 모서리 정렬)를 삼각형 띠로 잇는다. 감김은 나중에 위쪽으로 맞춘다."""
    nA, nB = len(tA), len(tB)
    tA2 = np.append(tA, 4.0)
    tB2 = np.append(tB, 4.0)
    i = j = 0
    tris = []
    while i < nA or j < nB:
        if j >= nB or (i < nA and tA2[i + 1] <= tB2[j + 1]):
            tris.append((offA + i % nA, offA + (i + 1) % nA, offB + j % nB))
            i += 1
        else:
            tris.append((offA + i % nA, offB + (j + 1) % nB, offB + j % nB))
            j += 1
    return np.asarray(tris, dtype=np.int64)


def face_up(P: np.ndarray, F: np.ndarray) -> np.ndarray:
    """위에서 볼 때 반시계(법선 +Up)가 되게 감김을 맞춘다."""
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    cr = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    F = F.copy()
    F[cr < 0] = F[cr < 0][:, ::-1]
    return F


def vertex_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    out = np.zeros_like(V)
    for k in range(3):
        np.add.at(out, F[:, k], n)
    out /= np.maximum(np.linalg.norm(out, axis=1), 1e-12)[:, None]
    return out


def chunk_loops(counts: list[int], max_v: int) -> list[list[int]]:
    """줄 번호 묶음. 이웃 덩어리는 경계 줄 하나를 함께 갖는다(같은 위치 정점 → 틈 없음). 한 덩어리 정점 ≤ max_v."""
    for k in range(1, len(counts)):
        if counts[k - 1] + counts[k] > max_v:
            raise ValueError(f"이웃한 줄 {k - 1}·{k} 정점 {counts[k - 1] + counts[k]} 이 덩어리 상한 {max_v} 을 넘습니다")
    chunks: list[list[int]] = []
    cur: list[int] = [0]
    tot = counts[0]
    for k in range(1, len(counts)):
        if tot + counts[k] > max_v and len(cur) >= 2:
            chunks.append(cur)
            cur, tot = [k - 1], counts[k - 1]
        cur.append(k)
        tot += counts[k]
    chunks.append(cur)
    return chunks


def skirt_colors(h: np.ndarray, nz: np.ndarray, wet: np.ndarray) -> np.ndarray:
    """정점색(선형 RGB): 저지대 들 → 산림 → 고지, 급경사는 암석색, 물 아래는 어두운 색."""
    def lin(c):
        return np.asarray(c, dtype=np.float64) ** 2.2

    def ss(x, a, b):
        t = np.clip((x - a) / (b - a), 0.0, 1.0)
        return t * t * (3 - 2 * t)
    low, forest, high, rock, under = (lin(c) for c in ((0.47, 0.52, 0.34), (0.25, 0.34, 0.20), (0.37, 0.39, 0.31),
                                                        (0.45, 0.43, 0.38), (0.22, 0.26, 0.24)))
    a = ss(h, 70.0, 170.0)[:, None]
    b = ss(h, 600.0, 900.0)[:, None]
    col = low * (1 - a) + forest * a
    col = col * (1 - b) + high * b
    slope = np.degrees(np.arccos(np.clip(nz, -1, 1)))
    r = ss(slope, 32.0, 45.0)[:, None] * 0.6
    col = col * (1 - r) + rock * r
    return np.where(wet[:, None], under, col)


def band_heights(P: np.ndarray, F: np.ndarray, sel: np.ndarray, dem_path, drop: float) -> np.ndarray:
    """sel 정점마다: 붙은 삼각형 꼭짓점들의 사각 범위(+2 m) 안 최저 지형 − drop − 0.05(RAW 양자화 여유)."""
    idx = np.nonzero(sel)[0]
    lo = np.full((len(P), 2), np.inf)
    hi = np.full((len(P), 2), -np.inf)
    for k in range(3):
        v = F[:, k]
        tri_lo = np.minimum(np.minimum(P[F[:, 0]], P[F[:, 1]]), P[F[:, 2]])
        tri_hi = np.maximum(np.maximum(P[F[:, 0]], P[F[:, 1]]), P[F[:, 2]])
        np.minimum.at(lo, v, tri_lo)
        np.maximum.at(hi, v, tri_hi)
    with rasterio.open(dem_path) as s:
        Z = s.read(1)
        TZ = s.transform
    out = np.empty(len(idx))
    for n, i in enumerate(idx):
        c0, r0 = ~TZ @ (lo[i, 0] - 2.0, hi[i, 1] + 2.0)
        c1, r1 = ~TZ @ (hi[i, 0] + 2.0, lo[i, 1] - 2.0)
        r0, c0 = max(0, int(np.floor(r0))), max(0, int(np.floor(c0)))
        r1, c1 = min(Z.shape[0], int(np.ceil(r1)) + 1), min(Z.shape[1], int(np.ceil(c1)) + 1)
        out[n] = float(np.nanmin(Z[r0:r1, c0:c1]))
    return out - drop - 0.05


# --------------------------------------------------------------------------- 원천 표본
class CopernicusSampler:
    """Copernicus 원천(EPSG:4326) 이중선형 표본. sigma_m 별로 가우스 평활한 사본을 쓴다."""

    def __init__(self, path, crs: str, bbox_5186):
        self.to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        minx, miny, maxx, maxy = bbox_5186
        xs = np.array([minx, maxx, maxx, minx])
        ys = np.array([miny, miny, maxy, maxy])
        lon, lat = self.to_ll.transform(xs, ys)
        with rasterio.open(path) as s:
            win = rasterio.windows.from_bounds(min(lon) - 0.01, min(lat) - 0.01, max(lon) + 0.01, max(lat) + 0.01,
                                               s.transform).round_offsets().round_lengths()
            self.z = s.read(1, window=win, boundless=True, fill_value=np.nan).astype(np.float64)
            self.T = rasterio.windows.transform(win, s.transform)
            nod = s.nodata
        if nod is not None:
            self.z[self.z == nod] = np.nan
        if np.isnan(self.z).any():
            idx = ndimage.distance_transform_edt(np.isnan(self.z), return_distances=False, return_indices=True)
            self.z = self.z[tuple(idx)]
        self.px_m = abs(self.T.e) * 111320.0           # 위도 방향 픽셀 크기(m)
        self._cache: dict[float, np.ndarray] = {}

    def field(self, sigma_m: float) -> np.ndarray:
        if sigma_m not in self._cache:
            s = sigma_m / self.px_m
            self._cache[sigma_m] = ndimage.gaussian_filter(self.z, s) if s > 0.05 else self.z
        return self._cache[sigma_m]

    def sample(self, E, N, sigma_m: float = 0.0) -> np.ndarray:
        lon, lat = self.to_ll.transform(np.asarray(E, dtype=np.float64), np.asarray(N, dtype=np.float64))
        f = self.field(sigma_m)
        col = (np.asarray(lon) - self.T.c) / self.T.a - 0.5
        row = (np.asarray(lat) - self.T.f) / self.T.e - 0.5
        H, W = f.shape
        c0 = np.clip(np.floor(col).astype(int), 0, W - 2)
        r0 = np.clip(np.floor(row).astype(int), 0, H - 2)
        tx = np.clip(col - c0, 0, 1)
        ty = np.clip(row - r0, 0, 1)
        return ((f[r0, c0] * (1 - tx) + f[r0, c0 + 1] * tx) * (1 - ty)
                + (f[r0 + 1, c0] * (1 - tx) + f[r0 + 1, c0 + 1] * tx) * ty)


def _dem_entry(cfg) -> dict[str, Any] | None:
    """원경 원천: 선택된 DEM 중 Copernicus(공식 DEM 은 범위 밖을 덮지 않을 수 있으므로 원경은 Copernicus 를 쓴다)."""
    m = sources.load(cfg)
    cop = [e for e in m["layers"].get("dem", []) if e.get("kind") == "copernicus_glo30"]
    return cop[0] if cop else None


# --------------------------------------------------------------------------- 범위 밖 수면
class OuterWater:
    """범위 밖 수면 폴리곤(고리 안으로 자름)과 높이. 평수면 = Copernicus 백분위, 경사 수면 = 최저값 창 + 평활.
    경계에 닿은 수면은 경계에서 boundary_blend_m 동안 범위 안 수면 높이로 이어지게 섞는다."""

    def __init__(self, cfg, area, ring, outer_bounds, cop: CopernicusSampler, inner_level, blend_m: float):
        sk = cfg["skirt"]
        self.items: list[dict[str, Any]] = []
        self.area_box = box(*area_bounds(cfg, area))
        df = load_layer(cfg, area, "skirt_water_areas", margin_m=float(sk["margin_m"]))
        self.ok = df is not None and not df.empty
        if not self.ok:
            return
        assert df is not None
        px = 30.0
        minx, miny, maxx, maxy = outer_bounds
        self.T = Affine(px, 0.0, minx, 0.0, -px, maxy)
        Wd, Hd = int(np.ceil((maxx - minx) / px)), int(np.ceil((maxy - miny) / px))
        gx, gy = np.meshgrid(minx + (np.arange(Wd) + 0.5) * px, maxy - (np.arange(Hd) + 0.5) * px)
        zc = cop.sample(gx.ravel(), gy.ravel()).reshape(Hd, Wd)
        pct = float(sk.get("water_level_percentile", 5))
        amin = float(sk.get("water_min_area_m2", 20000))
        geoms = []
        for _, r in df.iterrows():
            g = r.geometry
            if g is None or g.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            g = g.buffer(0).intersection(ring)
            for p in getattr(g, "geoms", [g]):
                if p.geom_type == "Polygon" and p.area >= amin:
                    name = r["name"] if "name" in df.columns and isinstance(r["name"], str) else ""
                    geoms.append((p, int(r["osm_id"]), name))
        geoms.sort(key=lambda x: (-x[0].area, x[1]))
        for p, oid, name in geoms:
            m = rasterize([(p, 1)], out_shape=zc.shape, transform=self.T, fill=0, dtype="uint8", all_touched=False).astype(bool)
            if m.sum() < 4:
                m = rasterize([(p, 1)], out_shape=zc.shape, transform=self.T, fill=0, dtype="uint8", all_touched=True).astype(bool)
            v = zc[m]
            if v.size == 0:
                continue
            lo, hi = np.percentile(v, [5, 95])
            it = {"osm_id": oid, "name": name, "poly": p, "area_m2": round(p.area, 0),
                  "range_m": round(float(hi - lo), 2), "touches_area": bool(p.distance(self.area_box) < 1.0)}
            if hi - lo > FLAT_RANGE_M:
                k = max(3, int(round(SLOPED_MIN_M / px)) | 1)
                zm = ndimage.minimum_filter(np.where(m, zc, np.inf), size=k)
                zm = np.where(np.isfinite(zm), zm, zc)
                s = SLOPED_SIGMA_M / px
                num = ndimage.gaussian_filter(np.where(m, zm, 0.0), s)
                den = ndimage.gaussian_filter(m.astype(np.float64), s)
                fld = np.where(den > 1e-3, num / np.maximum(den, 1e-6), np.nan)
                it.update(kind="sloped", field=fld, level_m=round(float(np.nanpercentile(fld[m], 50)), 2))
            else:
                it.update(kind="flat", level_m=round(float(np.percentile(v, pct)), 2))
            self.items.append(it)
        self.inner_level = inner_level
        self.blend_m = blend_m

    def level(self, k: int, E: np.ndarray, N: np.ndarray, d_out: np.ndarray, inner_at: np.ndarray) -> np.ndarray:
        """수면 k 의 높이(점마다). d_out: 범위 경계까지 거리, inner_at: 가장 가까운 경계 점의 범위 안 수면 높이(NaN 가능)."""
        it = self.items[k]
        if it["kind"] == "flat":
            lv = np.full(len(E), it["level_m"], dtype=np.float64)
        else:
            c, r = ~self.T @ (np.asarray(E), np.asarray(N))
            f = it["field"]
            r = np.clip(np.floor(r).astype(int), 0, f.shape[0] - 1)
            c = np.clip(np.floor(c).astype(int), 0, f.shape[1] - 1)
            lv = f[r, c]
            lv = np.where(np.isfinite(lv), lv, np.nanmedian(f))
        if not it["touches_area"]:          # 범위 안 수면과 이어진 폴리곤만 경계에서 섞는다
            return lv
        w = np.clip(1.0 - d_out / self.blend_m, 0.0, 1.0)
        has = np.isfinite(inner_at)
        return np.where(has, lv + (np.where(has, inner_at, 0.0) - lv) * w, lv)


def drop_under_water(P: np.ndarray, H: np.ndarray, sel: np.ndarray, surf: TriSurface, drop: float) -> tuple[np.ndarray, np.ndarray]:
    """sel 정점 가운데 수면 삼각형(surf)이 덮는 것은 그 자리 수면 − drop 이하로 내린다. (새 H, 젖은 정점). 순수 함수.

    수면과 같은 높이의 지면은 반투명 수면 아래로 비치고 움직이는 화면에서 깜빡인다(Unity 2차 임포트 보고).
    경계 줄(sel 밖)은 Terrain 경계 높이와 맞아야 하므로 건드리지 않는다.
    """
    H = np.asarray(H, dtype=np.float64).copy()
    ws = np.full(len(P), np.nan)
    if np.any(sel) and len(surf):
        ws[sel] = surf.height(P[sel, 0], P[sel, 1])
    wet = np.isfinite(ws)
    H[wet] = np.minimum(H[wet], ws[wet] - drop)
    return H, wet


def _dist_to_bounds(E, N, b) -> np.ndarray:
    minx, miny, maxx, maxy = b
    dx = np.maximum(np.maximum(minx - E, E - maxx), 0.0)
    dy = np.maximum(np.maximum(miny - N, N - maxy), 0.0)
    return np.hypot(dx, dy)


def _clamp_to_bounds(E, N, b):
    minx, miny, maxx, maxy = b
    return np.clip(E, minx, maxx), np.clip(N, miny, maxy)


def water_mesh(poly: Polygon, edge_step: float, grid_step: float) -> tuple[np.ndarray, np.ndarray]:
    """수면 폴리곤 2D 삼각분할(경계 촘촘히 + 내부 격자, Delaunay 후 폴리곤 밖 삼각형 제거)."""
    pts = []
    for ring_ in [poly.exterior, *poly.interiors]:
        pts.extend(ring_.segmentize(edge_step).coords[:-1])
    minx, miny, maxx, maxy = poly.bounds
    gx, gy = np.meshgrid(np.arange(minx + grid_step / 2, maxx, grid_step), np.arange(miny + grid_step / 2, maxy, grid_step))
    gx, gy = gx.ravel(), gy.ravel()
    inner = poly.buffer(-edge_step / 2)
    if not inner.is_empty and gx.size:
        m = contains_xy(inner, gx, gy)
        pts.extend(zip(gx[m], gy[m]))
    P = np.unique(np.round(np.asarray(pts, dtype=np.float64), 3), axis=0)
    if len(P) < 3:
        return P, np.zeros((0, 3), dtype=np.int64)
    tri = Delaunay(P)
    c = P[tri.simplices].mean(axis=1)
    keep = contains_xy(poly, c[:, 0], c[:, 1])
    return P, face_up(np.column_stack([P, np.zeros(len(P))]), tri.simplices[keep].astype(np.int64))


# --------------------------------------------------------------------------- 실행
def build(cfg: dict[str, Any], area: str) -> dict[str, Any]:
    sk = cfg["skirt"]
    ap = area_paths(cfg, area)
    b = area_bounds(cfg, area)
    M, near = float(sk["margin_m"]), float(sk["near_m"])
    s1, s2 = (float(v) for v in sk["spacing_m"])
    ov, drop = float(sk["inner_overlap_m"]), float(sk["overlap_drop_m"])
    bstep = sample_spacing(cfg) * int(sk.get("boundary_samples", 4))
    blend = float(sk.get("boundary_blend_m", 300.0))
    outer = (b[0] - M, b[1] - M, b[2] + M, b[3] + M)
    ent = _dem_entry(cfg)
    if ent is None:
        raise SystemExit("원경 원천(Copernicus DEM)이 manifest 에 없습니다(fetch-open --only dem).")
    cop = CopernicusSampler(sources.abspath(ent), cfg["crs"], (outer[0] - 500, outer[1] - 500, outer[2] + 500, outer[3] + 500))
    terrain = TerrainSampler(cfg, area)

    # 1) 고리 줄
    offs = ring_offsets(M, near, s1, s2, ov)
    loops = []
    for d, sp in offs:
        P, t = loop_points(b, d, bstep if sp == 0 else sp)
        loops.append((d, sp, P, t))
    counts = [len(L[2]) for L in loops]
    off = np.concatenate([[0], np.cumsum(counts)])
    P = np.concatenate([L[2] for L in loops])
    D = np.concatenate([np.full(len(L[2]), L[0]) for L in loops])
    F = np.concatenate([zip_loops(loops[k][3], loops[k + 1][3], off[k], off[k + 1]) for k in range(len(loops) - 1)])

    # 2) 높이
    H = np.zeros(len(P))
    sig = np.where(D <= near, s1 / 2.5, s2 / 2.5)
    for sv in np.unique(sig):
        m = (sig == sv) & (D > 0)
        H[m] = cop.sample(P[m, 0], P[m, 1], float(sv))
    bmask = D == 0
    Hb_terrain = terrain.height(P[bmask, 0], P[bmask, 1])
    if np.isnan(Hb_terrain).any():
        raise SystemExit("경계 줄 지형 표본에 NaN 이 있습니다(dem 단계 RAW 확인).")
    Hb_cop = cop.sample(P[bmask, 0], P[bmask, 1], s1 / 2.5)
    corr = Hb_terrain - Hb_cop
    k_sm = max(1, int(round(s1 / bstep)))         # 보정값은 경계를 따라 ±s1/2 평활(닫힌 줄)
    c_pad = np.concatenate([corr[-k_sm:], corr, corr[:k_sm]])
    corr_sm = np.convolve(c_pad, np.ones(2 * k_sm + 1) / (2 * k_sm + 1), mode="same")[k_sm:-k_sm]
    H[bmask] = Hb_terrain
    tree = cKDTree(P[bmask])
    outm = D > 0
    _, nb = tree.query(P[outm])
    w = np.clip(1.0 - D[outm] / blend, 0.0, 1.0)
    H[outm] = H[outm] + corr_sm[nb] * w
    # 겹침 줄(d < 0): 그 정점에 붙은 삼각형들이 덮는 사각 범위의 지형 최저값 − drop.
    # 이렇게 하면 경계 줄에 닿지 않은 띠 삼각형은 어디서나 지형보다 drop 이상 낮다(삼각형 안 지형 ≥ 각 정점 범위 최저값).
    # 경계 줄(지형 높이)과 d = −1 m 줄 사이 1 m 띠만 지형에 가깝다.
    imask = D < 0
    H[imask] = band_heights(P, F, imask, ap.interim / "dem_samples.tif", drop)

    # 3) 범위 밖 수면
    ring = box(*outer).difference(box(*b))
    wl_inner = None
    lvp = ap.interim / "water_level.tif"
    if lvp.exists():
        with rasterio.open(lvp) as s:
            wl_inner = (s.read(1), s.transform)

    def inner_level_at(E, N):
        if wl_inner is None:
            return np.full(len(E), np.nan)
        Fw, Tw = wl_inner
        ce, cn = _clamp_to_bounds(np.asarray(E), np.asarray(N), b)
        c, r = ~Tw @ (ce, cn)
        r = np.clip(np.round(r).astype(int), 0, Fw.shape[0] - 1)
        c = np.clip(np.round(c).astype(int), 0, Fw.shape[1] - 1)
        # 경계 바로 안쪽 3 m 안 최대(수면 메시는 폴리곤을 3 m 넓힌다)
        best = Fw[r, c].astype(np.float64)
        for dr in (-3, 0, 3):
            for dc in (-3, 0, 3):
                v = Fw[np.clip(r + dr, 0, Fw.shape[0] - 1), np.clip(c + dc, 0, Fw.shape[1] - 1)]
                best = np.fmax(best, v)
        return best

    water = OuterWater(cfg, area, ring, outer, cop, inner_level_at, blend) if "skirt_water_areas" in sources.load(cfg)["layers"] else None
    wet = np.zeros(len(P), dtype=bool)
    water_nodes = []
    water_meta = []
    wdrop = float(sk.get("water_drop_m", 2.0))
    if water is not None and water.ok:
        for k, it in enumerate(water.items):
            cx, cy = it["poly"].centroid.coords[0][:2]
            far = float(_dist_to_bounds(np.array([cx]), np.array([cy]), b)[0]) > near
            Q, FQ = water_mesh(it["poly"], s1, s2 if far else s1)
            if len(FQ) == 0:
                continue
            d_out = _dist_to_bounds(Q[:, 0], Q[:, 1], b)
            lvq = water.level(k, Q[:, 0], Q[:, 1], d_out, inner_level_at(Q[:, 0], Q[:, 1]))
            water_meta.append({"osm_id": it["osm_id"], "name": it["name"], "kind": it["kind"],
                               "level_m": it["level_m"], "range_m": it["range_m"], "area_m2": it["area_m2"],
                               "vertices": int(len(Q)), "level_min_m": round(float(lvq.min()), 2),
                               "level_max_m": round(float(lvq.max()), 2)})
            water_nodes.append((Q, FQ, lvq))
        # SKW 삼각형이 덮는 원경 정점(경계 줄·겹침 줄 제외)은 그 자리 SKW 면보다 water_drop_m 이상 낮춘다.
        # 폴리곤 포함 대신 실제 삼각형으로 판정한다(Delaunay 삼각형이 오목한 곳에서 폴리곤 밖으로 조금 나간다).
        maxv_w = int(sk.get("max_vertices", 65000))       # 아래 SKW 덩어리에 실제로 들어가는 수면만
        surf = TriSurface.concat([(np.column_stack([Q, lv]), FQ) for Q, FQ, lv in water_nodes if len(Q) <= maxv_w], cell_m=s2)
        H, wet = drop_under_water(P, H, outm, surf, wdrop)

    # 4) 법선·색, 덩어리
    O = np.array([cfg["origin"]["easting"], cfg["origin"]["northing"]], dtype=np.float64)
    V = np.column_stack([P - O, H])
    F = face_up(V, F)
    Nn = vertex_normals(V, F)
    col = skirt_colors(H, Nn[:, 2], wet) if sk.get("vertex_color", True) else None
    loop_id = np.concatenate([np.full(c, k) for k, c in enumerate(counts)])
    face_loop = np.minimum(loop_id[F].min(axis=1), len(loops) - 2)   # 띠 번호 = 안쪽 줄 번호
    chunks = chunk_loops(counts, int(sk.get("max_vertices", 65000)))
    nodes = []
    stats_chunks = []
    for i, ls in enumerate(chunks):
        vsel = np.isin(loop_id, ls)
        fsel = np.isin(face_loop, ls[:-1]) if len(ls) > 1 else np.zeros(len(F), dtype=bool)
        gid = np.nonzero(vsel)[0]
        remap = np.full(len(V), -1, dtype=np.int64)
        remap[gid] = np.arange(len(gid))
        Fi = remap[F[fsel]]
        assert (Fi >= 0).all()
        nodes.append((f"SK_{i}", [Primitive(position=V[gid], normal=Nn[gid], indices=Fi, material="M_skirt",
                                            color=None if col is None else col[gid])]))
        stats_chunks.append({"node": f"SK_{i}", "vertices": int(len(gid)), "triangles": int(len(Fi)),
                             "loops": [float(loops[ls[0]][0]), float(loops[ls[-1]][0])]})
    # 수면 덩어리(정점 ≤ max_vertices 로 묶음)
    maxv = int(sk.get("max_vertices", 65000))
    wi, buf = 0, []

    def flush():
        nonlocal wi, buf
        if not buf:
            return
        Vs, Fs, o = [], [], 0
        for Q, FQ, lv in buf:
            Vs.append(np.column_stack([Q - O, lv]))
            Fs.append(FQ + o)
            o += len(Q)
        Vw = np.concatenate(Vs)
        nodes.append((f"SKW_{wi}", [Primitive(position=Vw, normal=np.tile([0.0, 0.0, 1.0], (len(Vw), 1)),
                                              indices=np.concatenate(Fs), material="M_water")]))
        wi += 1
        buf = []
    tot = 0
    for Q, FQ, lv in water_nodes:
        if len(Q) > maxv:
            continue
        if tot + len(Q) > maxv:
            flush()
            tot = 0
        buf.append((Q, FQ, lv))
        tot += len(Q)
    flush()
    out_path = ap.out / "skirt" / "skirt.glb"
    write_glb_nodes(out_path, nodes)
    st = {
        "file": "skirt/skirt.glb", "bounds": [float(v) for v in b], "outer_bounds": [float(v) for v in outer],
        "margin_m": M, "spacing_m": [s1, s2], "near_m": near, "inner_overlap_m": ov, "overlap_drop_m": drop,
        "boundary_step_m": round(bstep, 4), "boundary_blend_m": blend,
        "loops": len(loops), "vertices": int(len(V)), "triangles": int(len(F)), "chunks": stats_chunks,
        "water_nodes": wi, "water": water_meta,
        "water_drop_m": wdrop, "under_water_vertices": int(wet.sum()),
        "boundary_cop_minus_terrain_m": {"p50": round(float(np.median(-corr)), 2),
                                         "p95_abs": round(float(np.percentile(np.abs(corr), 95)), 2),
                                         "max_abs": round(float(np.abs(corr).max()), 2)},
        "source": rel(sources.abspath(ent)),
    }
    write_json(ap.reports / "skirt.json", st)
    return st


def run(cfg: dict[str, Any], area: str, args=None) -> None:
    st = build(cfg, area)
    print(f"  원경 스커트: 줄 {st['loops']}개, 정점 {st['vertices']:,}, 삼각형 {st['triangles']:,}, "
          f"덩어리 {[c['vertices'] for c in st['chunks']]}, 범위 밖 수면 노드 {st['water_nodes']}개({len(st['water'])}폴리곤)")
    print(f"  경계 Copernicus − Terrain: {st['boundary_cop_minus_terrain_m']} → 경계 줄은 Terrain 에 맞추고 "
          f"{st['boundary_blend_m']:g} m 동안 원천으로 되돌림")
    for w in st["water"]:
        print(f"    수면 {w['osm_id']} {w['name']} {w['kind']} {w['level_m']} m ({w['level_min_m']}~{w['level_max_m']}), "
              f"{w['area_m2'] / 1e6:.2f} km²")
