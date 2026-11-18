"""지형 단계: DEM 모자이크(표본점 격자) → 수면 높이장·하상 깎기 → 교량 상판 높이·상판 밑 지형 → 타일별 16-bit RAW.

범위 전체를 높이맵 표본 격자 한 장으로 만든 뒤 타일로 자르므로, 이웃 타일의 공유 가장자리 값이
구조적으로 같다(이음매 0). 수면 아래 지형은 수면보다 낮게 깎아 수면 메시가 지형에 묻히지 않게 한다.
교량 상판 높이(roads.plan_decks)는 여기서 정한다: 상판 밑 지형을 낮춘 뒤(lower_under_decks, D-012) RAW 를
써야 Unity 지형이 상판을 뚫지 않기 때문이다. 결과는 data/interim/<area>/decks.json → roads 단계가 메시를 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

import numpy as np
import rasterio
import rasterio.windows
import shapely
from PIL import Image
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from . import sources
from .config import area_paths, write_json
from .grid import area_bounds, area_tiles
from .heightmap import encode, hillshade, mosaic_grid, sample_spacing, tile_window
from .tilejson import update_tile_json
from .vectors import load_layer

FLAT_RANGE_M = 3.0      # 수면 안 표고 범위가 이보다 작으면 평평한 수면(호수·저수지)
SMOOTH_SIGMA_M = 60.0   # 경사 수면 높이장 평활 반경
BED_SLOPE_IN = 0.15     # 수면 안쪽 하상 경사(m/m)
BED_MAX_DEPTH = 4.0
BRIDGE_FILTER_M = 30.0  # 최소값 필터 폭. 다리 footprint 에서 이 거리 안의 경사 수면 표본에만 건다(DSM 다리 번짐 제거)
JUNCTION_SIGMA_M = 12.0 # 서로 다른 수면체가 맞닿는 경계의 높이 이음 평활 반경
JUNCTION_BAND_M = 36.0  # 이음 평활을 섞는 띠 폭(경계에서 1 → 이 거리에서 0). 한 수면체 안쪽은 건드리지 않는다
CONTACT_M = 3.0         # 수면체끼리 이 거리 안이면 맞닿은 것으로 본다
SIDE_CHANNEL_SEP_M = 50.0  # 같은 평수면과 닿는 접촉부 두 곳이 이만큼 떨어져 있으면 '양 끝이 닿은 곁수로'
SAME_LEVEL_M = 0.5      # 높이 차가 이 안인 평수면은 같은 평수면으로 본다
FLOW_BIN_M = 2.0        # 하류 단조 강제 구간 길이(흐름 좌표)
FLOW_LINE_MIN_M = 20.0  # 수면체 안 물줄기 선이 이보다 짧으면 흐름 방향에 쓰지 않는다
FLOW_CONFLICT_M = 2.0   # OSM 선 방향의 하류 끝이 상류 끝보다 이만큼 높으면 선이 거꾸로 그려진 것으로 본다
SLOPE_MAX = 0.25        # 경사 수면 안 이웃 표본 사이 최대 수면 경사(m/m). 넘는 단은 높은 쪽을 낮춰 비탈로 만든다
BRIDGE_FADE_M = 15.0    # 다리 자리(footprint+여유폭) 밖에서 보호 해제 강도가 1 → 0 으로 줄어드는 폭
REACH_ALLOW_M = 0.5     # 경사 수면 상한: 메시가 덮는 반경(shore_overlap_m) 안 최저 지형 + 이 값 − 0.3 m (가장자리 틈 ≤ 0.2 m)
RIVER_TAGS = {"river", "stream", "canal", "ditch", "rapids", "stream_pool"}


def build_mosaic(cfg: dict[str, Any], bounds) -> tuple[np.ndarray, Any, float]:
    transform, w, h = mosaic_grid(cfg, bounds)
    dst = np.full((h, w), np.nan, dtype=np.float32)
    entries = sources.select(cfg, "dem")
    if not entries:
        raise SystemExit("DEM 원천이 없습니다. fetch-open 또는 register 를 먼저 실행하세요.")
    for e in entries:
        with rasterio.open(sources.abspath(e)) as src:
            src_crs = src.crs or CRS.from_user_input(e["crs"].split()[0])
            tmp = np.full((h, w), np.nan, dtype=np.float32)
            reproject(
                source=rasterio.band(src, 1), destination=tmp,
                src_transform=src.transform, src_crs=src_crs, src_nodata=src.nodata,
                dst_transform=transform, dst_crs=cfg["crs"], dst_nodata=np.nan,
                resampling=Resampling.cubic,
            )
        fill = np.isnan(dst) & ~np.isnan(tmp)
        dst[fill] = tmp[fill]
    nodata_ratio = float(np.isnan(dst).mean())
    if nodata_ratio > 0:
        if nodata_ratio == 1.0:
            raise SystemExit("DEM 원천이 범위를 전혀 덮지 않습니다.")
        idx = ndimage.distance_transform_edt(np.isnan(dst), return_distances=False, return_indices=True)
        dst = dst[tuple(idx)]
    return dst, transform, nodata_ratio


def dem_is_dsm(cfg: dict[str, Any]) -> bool:
    """선택된 DEM 원천이 DSM(수목·건물 포함)인가. 지금은 Copernicus GLO-30 만 해당."""
    return any(e.get("kind") == "copernicus_glo30" for e in sources.select(cfg, "dem"))


def flatten_footprints(z: np.ndarray, transform, geoms, p: dict[str, Any]) -> int:
    """외곽선 inner_buffer_m 안을 고리(inner~ring_outer) 높이의 ring_percentile 로 낮추고 blend_m 폭에서 섞는다.

    z 를 제자리에서 고친다(낮추기만 한다). 낮춘 외곽선 수를 돌려준다. 순수 함수(테스트 대상).
    """
    inner, blend, outer, pct = p["inner_buffer_m"], p["blend_m"], p["ring_outer_m"], p["ring_percentile"]
    H, W = z.shape
    inv = ~transform
    px = transform.a
    n = 0
    for g in geoms:
        minx, miny, maxx, maxy = g.buffer(outer).bounds
        c0, r0 = inv @ (minx, maxy)
        c1, r1 = inv @ (maxx, miny)
        r0, c0 = max(0, int(r0)), max(0, int(c0))
        r1, c1 = min(H, int(r1) + 1), min(W, int(c1) + 1)
        if r1 - r0 < 3 or c1 - c0 < 3:
            continue
        sub = z[r0:r1, c0:c1]
        wt = rasterio.windows.transform(rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0), transform)
        foot = rasterize([(g, 1)], out_shape=sub.shape, transform=wt, fill=0, all_touched=True, dtype="uint8").astype(bool)
        if not foot.any():
            continue
        dist = ndimage.distance_transform_edt(~foot) * px          # 외곽선에서의 거리
        ring = (dist >= inner) & (dist <= outer)
        if ring.sum() < 8:
            continue
        ground = float(np.percentile(sub[ring], pct))
        w = np.clip(1.0 - (dist - inner) / blend, 0.0, 1.0)           # 안쪽 1 → 바깥 0
        target = ground * w + sub * (1.0 - w)
        lowered = np.minimum(sub, target)
        if (sub - lowered).max() > 0.05:
            n += 1
        z[r0:r1, c0:c1] = lowered
    return n


def flatten_buildings(cfg: dict[str, Any], area: str, z: np.ndarray, transform) -> int:
    """(interim DSM 전용, D-009) 실제로 내보낼 건물 자리의 DSM 융기를 낮춘다.

    건물 금지 구역(남이섬) 안의 건물은 내보내지 않으므로 평탄화하지 않는다(빈 구덩이 방지).
    """
    from shapely import contains_xy

    from .zones import no_build_polygons

    bld = load_layer(cfg, area, "buildings", margin_m=0)
    if bld is None or bld.empty:
        return 0
    geoms = [g for g in bld.geometry if g.geom_type in ("Polygon", "MultiPolygon") and g.area >= 4.0]
    zones = [zg for _, zg in no_build_polygons(cfg, area)]
    if zones:
        keep = []
        for g in geoms:
            rp = g.representative_point()
            if not any(contains_xy(zg, rp.x, rp.y) for zg in zones):
                keep.append(g)
        geoms = keep
    return flatten_footprints(z, transform, geoms, cfg["dem"]["dsm_flatten_buildings"])


def _compactness(poly) -> float:
    return 4.0 * np.pi * poly.area / max(poly.length, 1e-6) ** 2


def water_polygons(cfg: dict[str, Any], area: str, bounds, with_meta: bool = False):
    """수면 피처별 폴리곤(섬 제외, 범위로 자름). 피처를 합치지 않는다(가평천이 북한강 수면에 묻히지 않게).

    with_meta=True 면 (폴리곤, 하천 태그 여부) 목록을 돌려준다.
    """
    water = load_layer(cfg, area, "water_areas")
    if water is None or water.empty:
        return []
    islands = load_layer(cfg, area, "islands")
    ig = [] if islands is None else [g for g in islands.geometry if g.geom_type in ("Polygon", "MultiPolygon")]
    clip = box(*bounds)
    cols = set(water.columns)
    out = []
    for _, r in water.iterrows():
        g = r.geometry
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        for isl in ig:
            # 섬은 그 섬을 실제로 둘러싼 수면에서만 뺀다(섬 안의 연못은 남긴다)
            if g.intersects(isl) and g.intersection(isl).area >= 0.5 * isl.area:
                g = g.difference(isl)
        g = g.intersection(clip)
        tag = str(r["water"]).lower() if "water" in cols and isinstance(r["water"], str) else ""
        riverbank = "waterway" in cols and r["waterway"] == "riverbank"
        is_river = tag in RIVER_TAGS or riverbank
        for p in getattr(g, "geoms", [g]):
            if p.geom_type == "Polygon" and p.area >= 300.0:
                out.append((p, is_river))
    return out if with_meta else [p for p, _ in out]


@dataclass
class _Body:
    """수면체 하나(창 단위 마스크). 내부용."""
    id: int
    poly: Any
    is_river: bool
    win: tuple[int, int, int, int]          # r0, r1, c0, c1 (전체 격자 기준)
    mask: np.ndarray                        # 창 크기 bool
    info: dict[str, Any]
    level: float | None = None              # 평수면 높이
    sfield: np.ndarray | None = None        # 경사 수면 높이(창 크기)
    contacts: dict[int, np.ndarray] = dc_field(default_factory=dict)  # 이웃 id → 맞닿은 표본(창 크기 bool)

    @property
    def sloped(self) -> bool:
        return self.info.get("kind") == "sloped"

    def sloped_field(self) -> np.ndarray:
        """경사 수면 높이(창 크기). water_surface 가 kind="sloped" 와 sfield 를 함께 정하고 함께 지우므로
        sloped 인 수면체에서는 항상 있다. 없으면 그 불변식이 깨진 것이므로 오류를 낸다."""
        if self.sfield is None:
            raise ValueError(f"수면체 {self.id}: 경사 수면인데 높이장이 없습니다")
        return self.sfield

    def flat_level(self) -> float:
        """평수면 높이. 평수면(곁수로 재분류 포함)에서는 항상 있다."""
        if self.level is None:
            raise ValueError(f"수면체 {self.id}: 평수면인데 높이가 없습니다")
        return float(self.level)


def _window(bounds, transform, shape, margin_px: int) -> tuple[int, int, int, int]:
    inv = ~transform
    minx, miny, maxx, maxy = bounds
    c0, r0 = inv @ (minx, maxy)
    c1, r1 = inv @ (maxx, miny)
    return (max(0, int(np.floor(r0)) - margin_px), min(shape[0], int(np.ceil(r1)) + 1 + margin_px),
            max(0, int(np.floor(c0)) - margin_px), min(shape[1], int(np.ceil(c1)) + 1 + margin_px))


def _win_transform(transform, win):
    r0, r1, c0, c1 = win
    return rasterio.windows.transform(rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0), transform)


def _mask_in(b: _Body, win) -> np.ndarray:
    """수면체 b 의 마스크를 다른 창(win) 크기로 옮긴다(겹치지 않는 곳은 False)."""
    r0, r1, c0, c1 = win
    out = np.zeros((r1 - r0, c1 - c0), dtype=bool)
    br0, br1, bc0, bc1 = b.win
    ir0, ir1, ic0, ic1 = max(r0, br0), min(r1, br1), max(c0, bc0), min(c1, bc1)
    if ir0 < ir1 and ic0 < ic1:
        out[ir0 - r0:ir1 - r0, ic0 - c0:ic1 - c0] = b.mask[ir0 - br0:ir1 - br0, ic0 - bc0:ic1 - bc0]
    return out


def sloped_level(z: np.ndarray, mask: np.ndarray, px: float, foot: np.ndarray | None = None,
                 reach_m: float = 0.0) -> np.ndarray:
    """경사 수면 높이(창 크기 배열, 마스크 밖 값은 쓰지 않음). 순수 함수(테스트 대상).

    - 다리 footprint(foot) 표본은 평활 가중에서 뺀다(상판·교각 DSM 이 수면 높이를 끌어올리지 않게).
    - 최소값 필터(BRIDGE_FILTER_M)는 footprint 에서 BRIDGE_FILTER_M 안의 표본에만 쓴다(DSM 번짐).
      다리 없는 곳은 원 표고를 그대로 평활한다(경사면에서 '경사 × 창 반폭'만큼 낮아지는 치우침 방지).
    - 평활(SMOOTH_SIGMA_M) 뒤 '그 자리 지형 + 1 m'와 '반경 reach_m 안 최저 지형 + REACH_ALLOW_M'을 넘지 않게
      자르고 0.3 m 내린다. reach_m = 수면 메시가 폴리곤 밖으로 덮는 폭(shore_overlap_m) → 메시 가장자리가
      낮은 물가 위로 0.2 m 넘게 뜨지 않는다.
    """
    sig = SMOOTH_SIGMA_M / px
    src = z
    weight = mask
    if foot is not None and (foot & mask).any():
        near = mask & (ndimage.distance_transform_edt(~foot) * px <= BRIDGE_FILTER_M)
        k = max(3, int(round(BRIDGE_FILTER_M / px)) | 1)
        zmin = ndimage.minimum_filter(np.where(mask, z, np.inf), size=k)
        zmin = np.where(np.isfinite(zmin), zmin, z)
        src = np.where(near, zmin, z)
        keep = mask & ~foot
        if keep.sum() >= max(4, 0.2 * mask.sum()):   # 거의 다리뿐인 작은 수면체는 그대로 둔다
            weight = keep
    num = ndimage.gaussian_filter(np.where(weight, src, 0.0).astype(np.float64), sig)
    den = ndimage.gaussian_filter(weight.astype(np.float64), sig)
    sm = num / np.maximum(den, 1e-6)
    cap = z + 1.0
    if reach_m > 0:
        r = int(np.ceil(reach_m / px))
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        zc = ndimage.minimum_filter(z, footprint=(np.hypot(yy, xx) * px <= reach_m + 1e-6))
        cap = np.minimum(cap, zc + REACH_ALLOW_M)
    return (np.minimum(sm, cap) - 0.3).astype(z.dtype)


def slope_limit(f: np.ndarray, mask: np.ndarray, px: float, smax: float) -> np.ndarray:
    """마스크 안에서 이웃 사이 수면 경사가 smax(m/m)를 넘지 않게 높은 쪽을 낮춘다(낮추기만 함). 순수 함수.

    f(x) = min_y [f(y) + smax·거리(x, y)] 를 8방향 반복으로 푼다. 짧은 거리의 급한 단(계단)이
    경사 smax 의 비탈로 바뀐다. 마스크 밖 값은 바꾸지 않는다.
    """
    out = np.where(mask, f, np.inf).astype(np.float64)
    H, W = out.shape
    steps = [(dr, dc, smax * px * float(np.hypot(dr, dc))) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]
    for _ in range(4 * max(H, W)):
        changed = False
        for dr, dc, add in steps:
            dst = out[max(0, dr):H + min(0, dr), max(0, dc):W + min(0, dc)]
            src = out[max(0, -dr):H + min(0, -dr), max(0, -dc):W + min(0, -dc)]
            cand = src + add
            m = cand < dst - 1e-6
            if m.any():
                dst[m] = cand[m]
                changed = True
        if not changed:
            break
    return np.where(mask, out, f).astype(f.dtype)


def monotone_downstream(f: np.ndarray, s: np.ndarray, bin_m: float = FLOW_BIN_M,
                        anchor: np.ndarray | None = None) -> np.ndarray:
    """하류로 갈수록 높아지지 않게 자른다. 순수 함수(테스트 대상).

    f: 수면 높이(1차원), s: 흐름 좌표(클수록 하류, m). s 를 bin_m 구간으로 나눠 구간 대표값을 구하고,
    상류에서 하류로 누적 최소를 취한 값으로 위를 자른다. 낮추기만 하므로 물가에 새 틈이 생기지 않는다.
    구간 대표값 = 그 구간 기준점(anchor, 물줄기 중심선 표본) 최소값. 기준점이 없는 구간은 구간 최대값.
    → 중심선을 따라서는 정확히 단조 감소, 횡단 방향의 높낮이는 그대로 둔다.
    """
    if f.size == 0:
        return f
    k = np.floor((s - s.min()) / bin_m).astype(np.int64)
    n = int(k.max()) + 1
    top = np.full(n, -np.inf)
    np.maximum.at(top, k, f)
    if anchor is not None and anchor.any():
        lo = np.full(n, np.inf)
        np.minimum.at(lo, k[anchor], f[anchor])
        top = np.where(np.isfinite(lo), lo, top)
    top[~np.isfinite(top)] = np.inf                  # 빈 구간은 제약 없음
    cap = np.minimum.accumulate(top)
    return np.minimum(f, cap[k]).astype(f.dtype)


def geodesic_distance(mask: np.ndarray, sources: np.ndarray, px: float) -> np.ndarray:
    """mask 안에서 sources 로부터의 최단 거리(8방향, m). 닿지 않는 곳은 inf. 마스크 밖도 inf."""
    n = int(mask.sum())
    out = np.full(mask.shape, np.inf)
    src = sources & mask
    if n == 0 or not src.any():
        return out
    idx = np.full(mask.shape, -1, dtype=np.int64)
    idx[mask] = np.arange(n)
    ii, jj, ww = [], [], []
    H, W = mask.shape
    for dr, dc, d in ((0, 1, px), (1, 0, px), (1, 1, px * 2 ** 0.5), (1, -1, px * 2 ** 0.5)):
        a = idx[0:H - dr, max(0, -dc):W - max(0, dc)]
        b = idx[dr:H, max(0, dc):W - max(0, -dc)]
        ok = (a >= 0) & (b >= 0)
        ii.append(a[ok]); jj.append(b[ok]); ww.append(np.full(int(ok.sum()), d))
    g = coo_matrix((np.concatenate(ww), (np.concatenate(ii), np.concatenate(jj))), shape=(n, n)).tocsr()
    dist = dijkstra(g, directed=False, indices=idx[src], min_only=True)
    out[mask] = dist
    return out


def _contacts(a: _Body, others: list[_Body], px: float) -> dict[int, np.ndarray]:
    out = {}
    for b in others:
        if b.id == a.id or a.poly.distance(b.poly) > CONTACT_M:
            continue
        mb = _mask_in(b, a.win)
        if not mb.any():
            continue
        c = a.mask & (ndimage.distance_transform_edt(~mb) * px <= CONTACT_M)
        if c.sum() >= 2:
            out[b.id] = c
    return out


def side_channel_level(contacts: dict[int, np.ndarray], flats: dict[int, tuple[float, float]], px: float):
    """양 끝(서로 SIDE_CHANNEL_SEP_M 넘게 떨어진 접촉부 둘 이상)이 같은 평수면에 닿으면 (높이, 이웃 id 목록).

    flats: 평수면 id → (높이, 면적). 높이 차 SAME_LEVEL_M 안이면 같은 평수면으로 묶고, 가장 큰 수면 높이를 쓴다.
    """
    groups: list[list] = []          # [기준 높이, 접촉 마스크, [(id, 높이, 면적)]]
    for bid, c in sorted(contacts.items()):
        if bid not in flats:
            continue
        lvl, area = flats[bid]
        for g in groups:
            if abs(g[0] - lvl) <= SAME_LEVEL_M:
                g[1] = g[1] | c
                g[2].append((bid, lvl, area))
                break
        else:
            groups.append([lvl, c.copy(), [(bid, lvl, area)]])
    for _, c, members in groups:
        lab, n = ndimage.label(c, structure=np.ones((3, 3)))
        if n < 2:
            continue
        sizes = ndimage.sum(c, lab, range(1, n + 1))
        keep = [i + 1 for i, s in enumerate(sizes) if s >= 4]
        if len(keep) < 2:
            continue
        cen = np.array(ndimage.center_of_mass(c, lab, keep)) * px
        sep = max(float(np.hypot(*(p - q))) for p in cen for q in cen)
        if sep >= SIDE_CHANNEL_SEP_M:
            main = max(members, key=lambda m: m[2])
            return main[1], sorted(m[0] for m in members)
    return None


def _flow_coordinate(b: _Body, own: np.ndarray, f_own: np.ndarray, wt, px: float,
                     flow_lines, neighbor_levels: dict[int, float]
                     ) -> tuple[np.ndarray, np.ndarray | None, dict[str, Any]]:
    """수면체 표본(own)의 흐름 좌표 s(클수록 하류), 중심선 기준점(OSM 선을 쓴 경우, 아니면 None), 방법 기록.

    1) OSM 물줄기 선(수면체 안 길이 FLOW_LINE_MIN_M 이상, 가장 긴 것): 선 방향 = 흐름 방향.
       하류 끝이 상류 끝보다 FLOW_CONFLICT_M 넘게 높으면 선이 거꾸로 그려진 것으로 보고 뒤집는다.
    2) 선이 없으면 가장 낮은 이웃 수면체에 닿은 곳을 하구로 보고, 하구에서의 수면체 안 거리로 s 를 정한다.
    3) 이웃도 없으면 가장 낮은 표본을 하구로 본다.
    """
    rr, cc = np.nonzero(own)
    best = None
    for oid, line in flow_lines or []:
        if not line.intersects(b.poly):
            continue
        L = line.intersection(b.poly.buffer(CONTACT_M)).length
        if L >= FLOW_LINE_MIN_M and (best is None or L > best[0]):
            best = (L, oid, line)
    if best is not None:
        xs, ys = wt @ (cc + 0.5, rr + 0.5)
        pts = shapely.points(np.asarray(xs), np.asarray(ys))
        s = shapely.line_locate_point(best[2], pts)
        anchor = shapely.distance(best[2], pts) <= px          # 중심선에서 한 표본 안
        meta = {"flow": "osm_waterway", "flow_line": int(best[1])}
        lo, hi = np.percentile(s, [15, 85])
        up, down = f_own[s <= lo], f_own[s >= hi]
        if up.size and down.size and np.median(down) > np.median(up) + FLOW_CONFLICT_M:
            s = -s
            meta["flow"] = "osm_waterway_reversed"
        return s, anchor, meta
    near = {k: v for k, v in b.contacts.items() if k in neighbor_levels}
    if near:
        low = min(near, key=lambda k: neighbor_levels[k])
        sources = near[low] & own
        meta = {"flow": "contact", "outlet_body": int(low)}
    else:
        sources = np.zeros_like(own)
        sources[rr[np.argmin(f_own)], cc[np.argmin(f_own)]] = True
        meta = {"flow": "lowest_point"}
    if not sources.any():
        sources = np.zeros_like(own)
        sources[rr[np.argmin(f_own)], cc[np.argmin(f_own)]] = True
        meta = {"flow": "lowest_point"}
    D = geodesic_distance(own, sources, px)
    reach = own & np.isfinite(D)
    if (own & ~reach).any():   # 끊긴 조각은 가장 가까운 닿은 표본의 거리를 쓴다
        idx = ndimage.distance_transform_edt(~reach, return_distances=False, return_indices=True)
        D = D[tuple(idx)]
    return -D[own], None, meta


def _owner_boundary(owner: np.ndarray) -> np.ndarray:
    """서로 다른 수면체가 (8방향으로) 맞닿은 표본."""
    out = np.zeros(owner.shape, dtype=bool)
    H, W = owner.shape
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = owner[0:H - dr, max(0, -dc):W - max(0, dc)]
        b = owner[dr:H, max(0, dc):W - max(0, -dc)]
        m = (a >= 0) & (b >= 0) & (a != b)
        out[0:H - dr, max(0, -dc):W - max(0, dc)] |= m
        out[dr:H, max(0, dc):W - max(0, -dc)] |= m
    return out


def water_surface(cfg, z, transform, polys_meta, bridge_foot: np.ndarray | None = None, flow_lines=None):
    """수면 높이장(수면 밖 NaN), 수면체 번호 래스터(수면 밖 −1), 수면체 목록.

    1. 분류: 표고 범위 > FLAT_RANGE_M 이고 (하천 태그 또는 가늘고 긴 모양) → 경사 수면, 나머지는 평수면
       (상수 높이 = level_percentile 백분위수).
    2. 경사 수면 높이 = sloped_level(다리 footprint 제외 평활, 다리 근처에만 최소값 필터).
    3. 곁수로: 경사 수면인데 양 끝이 같은 평수면에 닿으면 평수면으로 바꾸고 그 높이를 쓴다.
    4. 경사 수면을 먼저 쓰고 평수면이 면적 순으로 덮어쓴다(합류부는 본류 수면을 따른다).
    5. 이음 평활: 서로 다른 수면체가 맞닿는 경계 띠(JUNCTION_BAND_M)에서만 섞는다.
    6. 하류 단조: 경사 수면은 흐름 방향으로 높아지지 않게 자른다(monotone_downstream).
    """
    H, W = z.shape
    px = sample_spacing(cfg)
    wc = cfg["water"]
    pct = wc["level_percentile"]
    cmax = wc["sloped_compactness_max"]
    margin = int(3 * SMOOTH_SIGMA_M / px) + 2
    bodies: list[_Body] = []
    for i, (poly, is_river) in enumerate(polys_meta):
        win = _window(poly.bounds, transform, z.shape, margin)
        r0, r1, c0, c1 = win
        if r1 <= r0 or c1 <= c0:
            continue
        mask = rasterize([(poly, 1)], out_shape=(r1 - r0, c1 - c0), transform=_win_transform(transform, win),
                         fill=0, dtype="uint8").astype(bool)
        if mask.sum() < 4:
            continue
        zw = z[r0:r1, c0:c1]
        vals = zw[mask]
        lo, hi = np.percentile(vals, [5, 95])
        comp = _compactness(poly)
        sloped = (hi - lo) > FLAT_RANGE_M and (is_river or comp < cmax)
        info = {"id": i, "range_m": round(float(hi - lo), 2), "area_m2": round(poly.area, 1),
                "compactness": round(comp, 3), "river_tag": bool(is_river)}
        b = _Body(i, poly, bool(is_river), win, mask, info)
        if sloped:
            foot = None if bridge_foot is None else bridge_foot[r0:r1, c0:c1]
            b.sfield = sloped_level(zw, mask, px, foot, reach_m=wc.get("shore_overlap_m", 0.0))
            info["kind"] = "sloped"
            if foot is not None and (foot & mask).any():
                info["bridge_samples"] = int((foot & mask).sum())
        else:
            b.level = float(np.percentile(vals, pct))
            info["kind"] = "flat"
        bodies.append(b)

    # 3. 곁수로 재분류(양 끝이 같은 평수면)
    flats = {b.id: (b.flat_level(), float(b.poly.area)) for b in bodies if not b.sloped}
    for b in bodies:
        if not b.sloped:
            continue
        b.contacts = _contacts(b, bodies, px)
        sc = side_channel_level(b.contacts, flats, px)
        if sc is not None:
            b.level, ids = sc
            b.info.update(kind="flat", reclass=f"side_channel_of:{','.join(map(str, ids))}")
            b.sfield = None

    # 4. 조립
    field = np.full(z.shape, np.nan, dtype=z.dtype)
    owner = np.full(z.shape, -1, dtype=np.int16)
    for b in (b for b in bodies if b.sloped):
        r0, r1, c0, c1 = b.win
        fv, ov = field[r0:r1, c0:c1], owner[r0:r1, c0:c1]
        fv[b.mask] = b.sloped_field()[b.mask]
        ov[b.mask] = b.id
    for b in sorted((b for b in bodies if not b.sloped), key=lambda b: b.poly.area):  # 큰 수면이 마지막에 덮음
        r0, r1, c0, c1 = b.win
        field[r0:r1, c0:c1][b.mask] = b.flat_level()
        owner[r0:r1, c0:c1][b.mask] = b.id

    # 5. 이음 평활(경계 띠만). 수면 메시는 폴리곤을 shore_overlap_m 씩 넓히므로, 마른 틈이 2×shore_overlap_m
    #    이하인 수면체끼리도 메시에서는 붙는다 → 각 수면체를 그 폭만큼 키워 만나는 곳도 경계로 본다.
    grow = int(round(wc.get("shore_overlap_m", 0.0) / px))
    lab = owner
    if grow > 0:
        lab = owner.copy()
        for _ in range(grow):
            g = ndimage.maximum_filter(lab, size=3)
            empty = lab < 0
            lab[empty] = g[empty]
    edge = _owner_boundary(lab)
    del lab
    if edge.any():
        rows, cols = np.nonzero(edge)
        s_ = JUNCTION_SIGMA_M / px
        m_ = int((JUNCTION_BAND_M + 4 * JUNCTION_SIGMA_M) / px) + 2
        r0, r1 = max(0, rows.min() - m_), min(H, rows.max() + m_ + 1)
        c0, c1 = max(0, cols.min() - m_), min(W, cols.max() + m_ + 1)
        fv = field[r0:r1, c0:c1]
        wet = ~np.isnan(fv)
        num = ndimage.gaussian_filter(np.where(wet, fv, 0.0).astype(np.float64), s_)
        den = ndimage.gaussian_filter(wet.astype(np.float64), s_)
        sm = num / np.maximum(den, 1e-6)
        wj = np.clip(1.0 - ndimage.distance_transform_edt(~edge[r0:r1, c0:c1]) * px / JUNCTION_BAND_M, 0.0, 1.0)
        field[r0:r1, c0:c1] = np.where(wet, wj * sm + (1.0 - wj) * fv, np.nan).astype(z.dtype)

    # 6. 하류 단조(경사 수면)
    levels = {b.id: (b.flat_level() if not b.sloped else float(np.median(b.sloped_field()[b.mask]))) for b in bodies}
    for b in (b for b in bodies if b.sloped):
        r0, r1, c0, c1 = b.win
        own = owner[r0:r1, c0:c1] == b.id
        if own.sum() < 4:
            continue
        fv = field[r0:r1, c0:c1]
        f_own = fv[own]
        s, anchor, meta = _flow_coordinate(b, own, f_own, _win_transform(transform, b.win), px, flow_lines,
                                           {k: v for k, v in levels.items() if k != b.id})
        f2 = monotone_downstream(f_own, s, FLOW_BIN_M, anchor)
        fv[own] = f2
        meta["monotone_lowered_max_m"] = round(float((f_own - f2).max()), 2)
        # 수면 계단(지형 상한이 만든 짧은 단) → 경사 SLOPE_MAX 비탈(낮추기만 하므로 하류 단조는 유지된다)
        rows, cols = np.nonzero(own)
        sl = (slice(rows.min(), rows.max() + 1), slice(cols.min(), cols.max() + 1))
        sub = fv[sl]
        lim = slope_limit(sub, own[sl], px, SLOPE_MAX)
        meta["slope_lowered_max_m"] = round(float(np.nanmax(np.where(own[sl], sub - lim, 0.0))), 2)
        fv[sl] = lim
        b.info.update(meta)

    out = []
    for b in bodies:
        r0, r1, c0, c1 = b.win
        own = owner[r0:r1, c0:c1] == b.id
        lvl = float(np.median(field[r0:r1, c0:c1][own])) if own.any() else (b.level if b.level is not None else float("nan"))
        b.info["level_m"] = round(lvl, 2)
        out.append(b.info)
    return field, owner, sorted(out, key=lambda d: d["id"])


def water_level_field(cfg, z, transform, polys_meta, bridge_foot=None, flow_lines=None):
    """수면 높이장(수면 밖 NaN)과 수면체 목록(water_surface 의 앞 두 값)."""
    field, _, bodies = water_surface(cfg, z, transform, polys_meta, bridge_foot, flow_lines)
    return field, bodies


def bridge_footprint_mask(bridges, shape, transform, extra_m: float = 0.0) -> np.ndarray:
    """다리 상판 자리(선형 ± 폭/2 + extra_m) 래스터."""
    geoms = [(b.footprint(extra_m), 1) for b in bridges]
    if not geoms:
        return np.zeros(shape, dtype=bool)
    return rasterize(geoms, out_shape=shape, transform=transform, fill=0, all_touched=True, dtype="uint8").astype(bool)


def bridge_zones(bridges, polys, shape, transform, px: float, margin_m: float) -> tuple[np.ndarray, np.ndarray, list]:
    """물에 닿는 다리만 골라 (footprint+margin 마스크, 깎기 강도 0~1, 고른 다리).

    margin_m: DSM 이 다리를 번지게 하는 폭(DSM 일 때만 > 0). 강도는 footprint+margin 안 1,
    그 밖 BRIDGE_FADE_M 동안 1 → 0 으로 줄어든다(깎은 자리와 보호된 둔덕 사이 절벽 방지).
    """
    wet = unary_union(polys) if polys else None
    over = [b for b in bridges if wet is not None and b.footprint(margin_m).intersects(wet)]
    exact = bridge_footprint_mask(over, shape, transform)
    if not exact.any():
        z = np.zeros(shape, dtype=bool)
        return z, z.astype(np.float32), over
    d = ndimage.distance_transform_edt(~exact).astype(np.float32) * px
    foot = d <= margin_m
    strength = np.clip((margin_m + BRIDGE_FADE_M - d) / BRIDGE_FADE_M, 0.0, 1.0).astype(np.float32)
    return foot, strength, over


def flow_lines(cfg, area) -> list[tuple[int, Any]]:
    """OSM 물줄기 선(waterways). 선 방향 = 흐름 방향(OSM 규약)."""
    ww = load_layer(cfg, area, "waterways")
    if ww is None or ww.empty:
        return []
    out = []
    for _, r in ww.iterrows():
        g = r.geometry
        for part in getattr(g, "geoms", [g]):
            if part.geom_type == "LineString" and part.length > 0:
                out.append((int(r["osm_id"]) if "osm_id" in ww.columns else -1, part))
    return out


def carve(z, field, px, wc: dict[str, Any] | None = None, force: np.ndarray | None = None):
    """수면 아래 지형을 깎는다. 목표면은 물가를 기준으로 연속이다.

    물가 바깥 shore_ramp_m 구간: 수면 −0.5m에서 shore_slope_out 경사로 올라가는 면(지형이 더 높으면 깎음)
    수면 안쪽: 수면 −0.5m에서 가운데로 갈수록 깊어짐(최대 4m)
    수면보다 carve_max_above_m 넘게 높은 지형은 깎지 않는다(수면 폴리곤에 잘못 들어간 산비탈 보호).
    단, force(다리 자리, 0~1) 표본은 그 비율만큼 보호를 풀고 깎는다. force = 1 인 수면 표본은
    보호 없이 하상까지 깎는다(DSM 에 섞인 다리 상판·둔덕 제거). bool 도 받는다.
    """
    wc = wc or {}
    ramp = wc.get("shore_ramp_m", 20.0)
    slope_out = wc.get("shore_slope_out", 0.5)
    max_above = wc.get("carve_max_above_m", 8.0)
    mask = ~np.isnan(field)
    if not mask.any():
        return z, 0
    idx = ndimage.distance_transform_edt(~mask, return_distances=False, return_indices=True)
    level = field[tuple(idx)]                        # 수면 밖은 가장 가까운 수면 높이
    del idx
    d_in = ndimage.distance_transform_edt(mask) * px
    d_out = ndimage.distance_transform_edt(~mask) * px
    sd = np.where(mask, d_in, -d_out)                # + 수면 안, − 수면 밖
    del d_in, d_out
    target = np.where(
        sd >= 0,
        level - np.clip(0.5 + BED_SLOPE_IN * sd, 0.5, BED_MAX_DEPTH),
        level - 0.5 + slope_out * (-sd),
    )
    above = z - level
    weight = np.clip((max_above - above) / 4.0, 0.0, 1.0)      # 수면보다 아주 높은 지형은 보호
    must = mask & (above < max_above - 4.0)          # 보호 대상이 아닌 수면 안쪽은 반드시 수면보다 낮게
    if force is not None:
        fw = force.astype(np.float32)
        weight = np.maximum(weight, fw)
        must |= mask & (fw >= 1.0)
        del fw
    zone = sd > -ramp
    delta = np.where(zone, np.minimum(0.0, target - z) * weight, 0.0)
    delta = ndimage.gaussian_filter(delta, 1.5)      # 픽셀 경계 다듬기
    out = z + delta
    out = np.where(must, np.minimum(out, level - 0.3), out)
    return out.astype(z.dtype), int((delta < -0.01).sum())


# --------------------------------------------------------------------------- 상판 밑 지형(D-012)
@dataclass
class DeckSpan:
    """상판 하나의 사슬(지형 낮추기 입력). line: 사슬 중심선(사슬 방향), x·top: 1 m 종단, parts: [(way 선, 폭 m)],
    taper: 양 끝이 땅에 닿는가(접속 길·바깥 지형 끝), thickness: 상판 두께."""
    line: Any
    x: np.ndarray
    top: np.ndarray
    parts: list
    taper: tuple[bool, bool]
    thickness: float


def window_min(s: np.ndarray, x: np.ndarray, y: np.ndarray, win: float) -> np.ndarray:
    """조각 선형 함수 (x, y) 의 [s − win, s + win] ∩ [x0, xL] 최솟값(정확). 순수 함수."""
    s = np.asarray(s, dtype=np.float64)
    out = np.minimum(np.interp(s - win, x, y), np.interp(s + win, x, y))
    if win <= 0 or len(x) < 2:
        return np.minimum(out, np.interp(s, x, y))
    lo = np.searchsorted(x, s - win, side="left")
    hi = np.searchsorted(x, s + win, side="right")
    span = int((hi - lo).max()) if len(s) else 0
    for o in range(span):
        j = lo + o
        ok = j < hi
        jj = np.minimum(j, len(x) - 1)
        out = np.where(ok, np.minimum(out, y[jj]), out)
    return out


def deck_allowance(s: np.ndarray, L: float, x: np.ndarray, top: np.ndarray, thickness: float, clear: float,
                   end_m: float, taper: tuple[bool, bool], eps: float = 0.05, win_m: float = 0.0) -> np.ndarray:
    """사슬 위치 s 에서 상판 밑 지형 표본이 넘지 말아야 할 높이. 순수 함수(테스트 대상).

    기본 = 윗면 − thickness − clear(= 밑면 − clear). 땅에 닿는 끝(taper)은 끝에서 win_m 까지 (윗면 − eps),
    그다음 end_m 까지 기본으로 바뀐다(끝 단면은 접속 길 높이 그대로 두고 지형이 상판 위로 뚫고 나오지만 않게).
    윗면은 ±win_m 안 최저 윗면을 쓴다: 표본 하나가 Unity 이중선형으로 영향을 주는 상판 점(칸 대각선 √2·간격 안)
    가운데 가장 낮은 곳에서도 뚫고 나오지 않게. 사슬 밖(끝 너머)은 s 를 0·L 로 잘라 끝값을 쓴다.
    """
    s = np.clip(np.asarray(s, dtype=np.float64), 0.0, L)
    t = window_min(s, np.asarray(x, dtype=np.float64), np.asarray(top, dtype=np.float64), max(win_m, 0.0))
    w = np.ones_like(s)
    if end_m > 0:
        a0 = min(max(win_m, 0.0), 0.5 * end_m)          # 끝 이중선형 표본이 닿는 첫 구간은 평평하게(끝 지형을 덜 건드림)
        if taper[0]:
            w = np.minimum(w, np.clip((s - a0) / (end_m - a0), 0.0, 1.0))
        if taper[1]:
            w = np.minimum(w, np.clip((L - s - a0) / (end_m - a0), 0.0, 1.0))
    return t - eps - (thickness + clear - eps) * w


def bilinear_support(geom, transform, shape) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
    """면 geom 안의 어느 점이든 이중선형 보간에 쓰는 표본(모자이크 픽셀 중심)의 창과 마스크.

    표본 사이 칸(표본 네 개가 꼭짓점)을 all_touched 로 칠하고, 칠한 칸의 꼭짓점 표본을 고른다. 순수 함수.
    """
    from affine import Affine
    H, W = shape
    px = abs(transform.a)
    minx, miny, maxx, maxy = geom.bounds
    inv = ~transform
    c0f, r0f = inv @ (minx, maxy)
    c1f, r1f = inv @ (maxx, miny)
    # 칸 (i, j) = 표본 (r, c)..(r+1, c+1) 사이. 표본 중심 = 픽셀 (c + 0.5, r + 0.5)
    r0, r1 = max(0, int(np.floor(r0f - 0.5)) - 1), min(H - 1, int(np.ceil(r1f - 0.5)) + 1)
    c0, c1 = max(0, int(np.floor(c0f - 0.5)) - 1), min(W - 1, int(np.ceil(c1f - 0.5)) + 1)
    if r1 <= r0 or c1 <= c0:
        return None
    x0, y0 = transform @ (c0 + 0.5, r0 + 0.5)                 # 칸 격자 왼쪽 위 = 표본 (r0, c0)
    T = Affine(px, 0.0, x0, 0.0, -px, y0)
    cells = rasterize([(geom, 1)], out_shape=(r1 - r0, c1 - c0), transform=T, fill=0, all_touched=True,
                      dtype="uint8").astype(bool)
    if not cells.any():
        return None
    m = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=bool)
    m[:-1, :-1] |= cells
    m[1:, :-1] |= cells
    m[:-1, 1:] |= cells
    m[1:, 1:] |= cells
    return (r0, r1 + 1, c0, c1 + 1), m


def deck_footprint(g, width: float):
    """상판 외곽(bridges.footprint_ring 과 같은 마이터 단면) ∪ 끝이 평평한 버퍼."""
    from shapely.geometry import Polygon
    from .bridges import footprint_ring
    ring = footprint_ring(np.asarray(g.coords)[:, :2], width)
    a = Polygon(ring).buffer(0)
    b = g.buffer(width / 2.0, cap_style="flat", join_style="mitre", mitre_limit=2.0)
    return a.union(b)


def lower_under_decks(z: np.ndarray, transform, spans: list[DeckSpan], clear: float = 0.3, end_m: float = 3.0,
                      slope: float = 0.5, eps: float = 0.05, max_ramp_m: float = 60.0) -> tuple[np.ndarray, dict[str, Any]]:
    """상판 밑 지형을 deck_allowance 이하로 낮추고 둘레를 경사 slope 로 섞는다(낮추기만 한다). 순수 함수(테스트 대상).

    - 자리(zone) = 상판 외곽 안 어느 점이든 Unity 이중선형 보간에 쓰는 표본(bilinear_support). 표본마다 허용 높이는
      사슬 위치의 deck_allowance(윗면은 ±√2·간격 안 최저값).
    - 둘레: 자리 밖 표본은 min(지형, 가장 가까운 자리 표본 허용값 + slope × 거리). 단, 다른 상판의 자리 안
      (그 상판 끝·접속 길 높이를 지킨다)과 사슬 끝 너머(터널 입구 산비탈 등)는 섞지 않는다.
    결과는 모든 제약의 최솟값이라 상판 순서와 무관하다. 돌려주는 값: (새 z, 통계).
    """
    import shapely as _sh

    H, W = z.shape
    px = abs(transform.a)
    win_m = float(np.sqrt(2.0)) * px
    out = z.copy()
    zones = []
    for si, sp in enumerate(spans):
        L = float(sp.line.length)
        for g, w in sp.parts:
            sup = bilinear_support(deck_footprint(g, w), transform, z.shape)
            if sup is None:
                continue
            win, m = sup
            r0, r1, c0, c1 = win
            rr, cc = np.nonzero(m)
            xs, ys = transform @ (cc + c0 + 0.5, rr + r0 + 0.5)
            s = _sh.line_locate_point(sp.line, _sh.points(np.asarray(xs), np.asarray(ys)))
            A = deck_allowance(s, L, sp.x, sp.top, sp.thickness, clear, end_m, sp.taper, eps, win_m=win_m)
            zones.append((si, win, m, A, L))
    # 1) 자리 안
    per_span: dict[int, list[float]] = {}
    for si, (r0, r1, c0, c1), m, A, _ in zones:
        sub = out[r0:r1, c0:c1]
        orig = z[r0:r1, c0:c1][m]
        sub[m] = np.minimum(sub[m], A.astype(z.dtype))
        d = orig - np.minimum(orig, A)
        st = per_span.setdefault(si, [0.0, 0.0])
        st[0] = max(st[0], float(d.max()))
        st[1] += float((d > 0.01).sum()) * px * px
    # 2) 둘레(자리 밖, 다른 자리 밖, 사슬 끝 너머 제외)
    n_ramp = 0
    for si, (r0, r1, c0, c1), m, A, L in zones:
        depth = float((z[r0:r1, c0:c1][m] - A).max())
        if depth <= 0.01:
            continue
        R = min(depth / slope, max_ramp_m) + px
        k = int(np.ceil(R / px))
        R0, R1, C0, C1 = max(0, r0 - k), min(H, r1 + k), max(0, c0 - k), min(W, c1 + k)
        zm = np.zeros((R1 - R0, C1 - C0), dtype=bool)
        Aw = np.full(zm.shape, np.inf)
        zm[r0 - R0:r1 - R0, c0 - C0:c1 - C0] = m
        Aw[r0 - R0:r1 - R0, c0 - C0:c1 - C0][m] = A
        prot = np.zeros_like(zm)
        for _, (q0, q1, p0, p1), mq, _, _ in zones:
            a0, a1, b0, b1 = max(q0, R0), min(q1, R1), max(p0, C0), min(p1, C1)
            if a0 < a1 and b0 < b1:
                prot[a0 - R0:a1 - R0, b0 - C0:b1 - C0] |= mq[a0 - q0:a1 - q0, b0 - p0:b1 - p0]
        dist, idx = ndimage.distance_transform_edt(~zm, return_indices=True)
        C = Aw[idx[0], idx[1]] + slope * dist * px
        zw = z[R0:R1, C0:C1]
        cand = ~prot & (dist * px <= R) & (zw > C)
        if not cand.any():
            continue
        rr, cc = np.nonzero(cand)
        xs, ys = transform @ (cc + C0 + 0.5, rr + R0 + 0.5)
        s = _sh.line_locate_point(spans[si].line, _sh.points(np.asarray(xs), np.asarray(ys)))
        mid = (s > 1e-6) & (s < L - 1e-6)
        rr, cc = rr[mid], cc[mid]
        sub = out[R0:R1, C0:C1]
        sub[rr, cc] = np.minimum(sub[rr, cc], C[rr, cc].astype(z.dtype))
        n_ramp += int(mid.sum())
    low = z - out
    stats = {"zones": len(zones), "lowered_samples": int((low > 0.01).sum()), "ramp_samples": n_ramp,
             "lowered_max_m": round(float(low.max()), 2) if low.size else 0.0,
             "lowered_area_m2": round(float((low > 0.01).sum()) * px * px, 0),
             "spans": {int(k): {"lowered_max_m": round(v[0], 2), "zone_lowered_m2": round(v[1], 0)} for k, v in per_span.items()}}
    return out, stats


def spans_from_chains(chains, bridges, thickness: float) -> list[DeckSpan]:
    """roads.plan_decks 의 사슬 → DeckSpan."""
    from .roads import end_tapers
    out = []
    for ch in chains:
        parts = [(bridges[j].geometry, float(bridges[j].width_m)) for j, _ in ch.members]
        out.append(DeckSpan(ch.line, np.asarray(ch.x), np.asarray(ch.top), parts, end_tapers(ch), thickness))
    return out


def _deck_terrain(cfg, area, ap, z, transform, field, wpolys, bridges, px):
    """상판 높이를 정하고(roads.plan_decks) 상판 밑 지형을 낮춘다. decks.json 을 남긴다."""
    from .roads import plan_decks, save_decks
    from .terrain import TerrainSampler
    from .water import water_surface_from_field
    rc = cfg["roads"]
    wsurf = water_surface_from_field(cfg, area, field, transform, wpolys)
    plan = plan_decks(cfg, area, bridges, TerrainSampler.from_mosaic(cfg, area, z), wsurf)
    spans = spans_from_chains(plan.chains, bridges, float(rc.get("deck_thickness_m", 0.8)))
    z2, st = lower_under_decks(z, transform, spans, clear=float(rc.get("under_deck_clear_m", 0.3)),
                               end_m=float(rc.get("under_deck_end_m", 3.0)), slope=float(rc.get("under_deck_ramp_slope", 0.5)))
    # 낮춘 땅이 물가(수면 메시 띠) 밖에서 가까운 수면보다 낮아진 곳 = 수면이 덮지 않는 마른 웅덩이(보고만)
    low = (z - z2) > 0.01
    pits = 0
    if low.any():
        rows, cols = np.nonzero(low)
        m_ = int(40.0 / px) + 2
        sl = (slice(max(0, rows.min() - m_), rows.max() + m_ + 1), slice(max(0, cols.min() - m_), cols.max() + m_ + 1))
        wet = ~np.isnan(field[sl])
        if wet.any():
            dist, idx = ndimage.distance_transform_edt(~wet, return_indices=True)
            near_lv = field[sl][idx[0], idx[1]]
            reach = float(cfg["water"].get("shore_overlap_m", 3.0))
            pit = low[sl] & (dist * px > reach) & (z2[sl] < near_lv - 0.01) & (z[sl] >= near_lv - 0.01)
            pits = int(pit.sum())
    st["dry_below_water_m2"] = round(pits * px * px, 0)
    named = {}
    for k, v in st["spans"].items():
        if v["lowered_max_m"] > 0.01:
            named["+".join(f"w{bridges[j].osm_id}" for j, _ in plan.chains[k].members)] = v
    st["spans"] = named
    save_decks(ap.interim / "decks.json", plan, bridges, {"under_deck": st, "water_triangles": len(wsurf)})
    print(f"  교량 상판 {len(plan.chains)}사슬(나란한 쌍 {len(plan.parallel)}, 공유 반복 {plan.iterations}회, 경고 {len(plan.warnings)}건). "
          f"상판 밑 지형 낮춤 {st['lowered_area_m2']:,.0f} m², 최대 {st['lowered_max_m']} m, 둘레 섞음 {st['ramp_samples']:,}표본, "
          f"물가 밖에서 수면보다 낮아진 땅 {st['dry_below_water_m2']:,.0f} m²")
    return z2, st


def run(cfg: dict[str, Any], area: str, args=None) -> None:
    ap = area_paths(cfg, area).ensure()
    bounds = area_bounds(cfg, area)
    z, transform, nodata_ratio = build_mosaic(cfg, bounds)
    px = sample_spacing(cfg)
    print(f"  모자이크 {z.shape[1]}x{z.shape[0]} (간격 {px:.4f}m), 표고 {np.nanmin(z):.1f}~{np.nanmax(z):.1f}m, nodata {nodata_ratio:.2%}")

    if dem_is_dsm(cfg) and cfg.get("dem", {}).get("dsm_flatten_buildings"):
        nflat = flatten_buildings(cfg, area, z, transform)
        print(f"  DSM 건물 융기 평탄화(D-009): {nflat}동")
    from .bridges import load_bridges
    bridges = load_bridges(cfg, area)
    margin = float(cfg.get("dem", {}).get("dsm_bridge_margin_m", 0.0)) if dem_is_dsm(cfg) else 0.0
    polys = water_polygons(cfg, area, bounds, with_meta=True)
    foot, strength, over = bridge_zones(bridges, [p for p, _ in polys], z.shape, transform, px, margin)
    field, owner, bodies = water_surface(cfg, z, transform, polys, bridge_foot=foot, flow_lines=flow_lines(cfg, area))
    n_forced = int((foot & ~np.isnan(field)).sum())
    z, carved = carve(z, field, px, cfg["water"], force=strength)
    del strength
    kinds = {k: sum(1 for b in bodies if b["kind"] == k) for k in ("flat", "sloped")}
    print(f"  다리 {len(bridges)}개 중 물에 닿는 {len(over)}개(여유폭 {margin:g} m): 수면 위 다리 자리 {n_forced:,}표본은 보호 없이 깎음")
    print(f"  수면체 {len(bodies)}개 {kinds}, 깎은 표본 {carved:,}개")
    for b in bodies:
        if b["kind"] == "sloped" or b.get("reclass"):
            extra = {k: b[k] for k in ("reclass", "flow", "flow_line", "outlet_body", "monotone_lowered_max_m", "slope_lowered_max_m") if k in b}
            print(f"    수면체 {b['id']}: {b['kind']} {b['level_m']} m {extra}")

    # 교량 상판(C3, D-012): 깎은 지형(RAW 와 같은 양자화)과 수면 메시(water 단계와 같은 삼각형)로 상판 높이를 정하고,
    # 상판 밑 지형을 낮춘 뒤 RAW 를 쓴다. 결과는 decks.json → roads 단계가 메시를 만든다.
    z, _ = _deck_terrain(cfg, area, ap, z, transform, field, [p for p, _ in polys], bridges, px)

    profile = dict(driver="GTiff", width=z.shape[1], height=z.shape[0], count=1, dtype="float32",
                   crs=cfg["crs"], transform=transform, compress="deflate", predictor=3,
                   tiled=True, blockxsize=512, blockysize=512, nodata=np.nan)
    with rasterio.open(ap.interim / "dem_samples.tif", "w", **profile) as dst:
        dst.write(z.astype(np.float32), 1)
    with rasterio.open(ap.interim / "water_level.tif", "w", **profile) as dst:
        dst.write(field.astype(np.float32), 1)
    with rasterio.open(ap.interim / "water_body_id.tif", "w", **{**profile, "dtype": "int16", "nodata": -1, "predictor": 2}) as dst:
        dst.write(owner, 1)
    write_json(ap.interim / "water_bodies.json", bodies)
    write_json(ap.interim / "bridges.geojson", {
        "type": "FeatureCollection", "crs_note": cfg["crs"],
        "features": [{"type": "Feature", "geometry": mapping(b.geometry),
                      "properties": {"osm_id": b.osm_id, "kind": b.kind, "class": b.cls, "name": b.name,
                                     "width_m": b.width_m, "width_source": b.width_source, "layer": b.layer}}
                     for b in bridges]})

    hm = cfg["heightmap"]
    lo, hi = hm["base_m"], hm["base_m"] + hm["range_m"]
    if np.nanmin(z) < lo or np.nanmax(z) > hi:
        print(f"  경고: 표고가 RAW 범위({lo}~{hi}m)를 벗어납니다 → 잘림")
    prov = sources.provenance(sources.select(cfg, "dem"))
    for t in area_tiles(cfg, area):
        rs, cs = tile_window(cfg, bounds, t.e0, t.n0)
        tz = z[rs, cs]
        d = ap.tile_dir(t.name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "heightmap.raw").write_bytes(encode(tz, cfg))
        hs = (hillshade(tz, px) * 255).astype(np.uint8)
        Image.fromarray(hs).resize((256, 256), Image.Resampling.BILINEAR).save(d / "heightmap_preview.png")
        update_tile_json(d, t, cfg, "dem", {
            "file": "heightmap.raw", "min_m": round(float(tz.min()), 2), "max_m": round(float(tz.max()), 2),
            "nodata_ratio": round(nodata_ratio, 4), "interim": bool(prov and prov["interim"]),
        })
    print(f"  타일 {len(area_tiles(cfg, area))}개 RAW 저장 → {ap.tiles}")
