"""다리(교량) 선형: OSM roads·railways 의 bridge 태그 way → EPSG:5186 LineString + 폭.

dem 단계(다리 자리 하상 깎기, 다리 근처 최소값 필터)와 교량 상판 메시 단계가 함께 쓰는 공개 모듈이다.

폭 규칙(우선순위, D-012):
1. `width` 태그(m, "3", "3.5 m", "3,5" 모두 허용. 피트 표기는 m 로 바꾼다)
2. `lanes` × `roads.lane_m` + `roads.shoulder_m` (지금 3.0 m × 차로 + 1.0 m)
3. 등급별 기본값 `roads.width_m[등급]` (`*_link` 는 같은 이름 키 → `link` 키 순서), 없으면 `roads.width_m.default`.
   철도(rail)는 OSM 이 선로마다 way 를 그리므로 way당 단선 폭(5 m)이다.
1·2 로 낸 폭이 등급 기본값 × `roads.width_outlier_ratio`(3) 를 넘으면 그 태그는 이상값으로 거르고 다음 규칙을 쓴다(06a).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from shapely.geometry import LineString

from .vectors import load_layer

FOOT_CLASSES = {"footway", "path", "steps", "pedestrian", "cycleway", "bridleway", "corridor"}
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Bridge:
    osm_id: int
    geometry: LineString      # EPSG:5186
    kind: str                 # road / foot / rail
    cls: str                  # highway 또는 railway 값(예: primary, footway, rail)
    name: str                 # bridge:name → name:ko → name 순서, 없으면 ""
    width_m: float
    width_source: str         # width / lanes / default
    layer: int                # OSM layer(없으면 1)

    def footprint(self, extra_m: float = 0.0):
        """상판 자리 폴리곤(선형을 폭/2 + extra_m 만큼 넓힘, 끝은 평평하게)."""
        return self.geometry.buffer(self.width_m / 2.0 + extra_m, cap_style="flat", join_style="mitre")


def _text(v: Any) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def is_bridge(value: Any) -> bool:
    """bridge 태그가 있고 "no" 가 아니면 다리(yes, viaduct, boardwalk, ... 모두 포함)."""
    s = _text(value).lower()
    return s not in ("", "no", "false", "0")


def parse_width(value: Any) -> float | None:
    """width 태그 → m. 해석할 수 없거나 0 이하·100 m 초과면 None."""
    s = _text(value).lower()
    m = _NUM.search(s)
    if not m:
        return None
    w = float(m.group(0).replace(",", "."))
    if "'" in s or "ft" in s:
        w *= 0.3048
    return w if 0.0 < w <= 100.0 else None


def parse_lanes(value: Any) -> int | None:
    m = re.search(r"\d+", _text(value))
    if not m:
        return None
    n = int(m.group(0))
    return n if 0 < n <= 20 else None


def class_width(cls: str, roads_cfg: dict[str, Any]) -> float:
    """등급별 기본 폭 `roads.width_m[등급]`(`*_link` → `link`, 없으면 `default`)."""
    table = roads_cfg.get("width_m", {})
    if cls in table:
        return float(table[cls])
    if cls.endswith("_link"):
        return float(table.get("link", table.get("default", 6.0)))
    return float(table.get("default", 6.0))


def width_check(tags: dict[str, Any], cls: str, roads_cfg: dict[str, Any]) -> tuple[float, str, list[str]]:
    """(폭 m, 출처, 거른 태그 목록). 순수 함수(테스트 대상).

    이상값 거르기(06a): width 태그나 lanes 로 낸 폭이 등급 기본값 × `roads.width_outlier_ratio`(3) 를 넘으면
    그 태그를 없는 것으로 보고 다음 규칙(width → lanes → 등급 기본값)으로 넘어간다. 거른 태그는 목록으로 돌려준다
    (roads 단계가 reports/width_outliers.csv 에 남긴다). 예: 북한강 자전거길 w588836894 width=43 → cycleway 2.5 m.
    """
    base = class_width(cls, roads_cfg)
    ratio = float(roads_cfg.get("width_outlier_ratio", 3.0))
    lim = ratio * base if ratio > 0 else float("inf")
    rejected: list[str] = []
    w = parse_width(tags.get("width"))
    if w is not None:
        if w <= lim + 1e-9:
            return w, "width", rejected
        rejected.append(f"width={_text(tags.get('width'))}")
    lanes = parse_lanes(tags.get("lanes"))
    if lanes is not None:
        wl = lanes * float(roads_cfg.get("lane_m", 3.5)) + float(roads_cfg.get("shoulder_m", 2.0))
        if wl <= lim + 1e-9:
            return wl, "lanes", rejected
        rejected.append(f"lanes={_text(tags.get('lanes'))}")
    return base, "default", rejected


def bridge_width(tags: dict[str, Any], cls: str, roads_cfg: dict[str, Any]) -> tuple[float, str]:
    """(폭 m, 출처). 순수 함수(테스트 대상). 이상값 거르기 포함(width_check)."""
    w, src, _ = width_check(tags, cls, roads_cfg)
    return w, src


def bridge_kind(layer: str, cls: str) -> str:
    if layer == "railways":
        return "rail"
    return "foot" if cls in FOOT_CLASSES else "road"


def _layer_int(v: Any) -> int:
    m = re.search(r"-?\d+", _text(v))
    return int(m.group(0)) if m else 1


def load_bridges(cfg: dict[str, Any], area: str, margin_m: float = 200.0) -> list[Bridge]:
    """범위(+margin_m) 안 OSM roads·railways 에서 bridge≠no 인 way 를 다리 레코드로 돌려준다.

    MultiLineString 은 조각마다 레코드 하나(같은 osm_id). osm_id 순서로 정렬해 재실행 결과가 같다.
    """
    roads_cfg = cfg.get("roads", {}) or {}
    out: list[Bridge] = []
    for layer, cls_col in (("roads", "highway"), ("railways", "railway")):
        df = load_layer(cfg, area, layer, margin_m=margin_m)
        if df is None or df.empty or "bridge" not in df.columns:
            continue
        cols = set(df.columns)
        for _, r in df.iterrows():
            if not is_bridge(r["bridge"]):
                continue
            g = r.geometry
            if g is None or g.is_empty or g.geom_type not in ("LineString", "MultiLineString"):
                continue
            cls = _text(r[cls_col]) if cls_col in cols else ""
            tags = {k: r[k] for k in ("width", "lanes") if k in cols}
            width, src = bridge_width(tags, cls, roads_cfg)
            name = next((_text(r[k]) for k in ("bridge:name", "name:ko", "name") if k in cols and _text(r[k])), "")
            for part in getattr(g, "geoms", [g]):
                if part.length <= 0:
                    continue
                out.append(Bridge(osm_id=int(r["osm_id"]) if "osm_id" in cols else -1, geometry=part,
                                  kind=bridge_kind(layer, cls), cls=cls, name=name, width_m=round(width, 2),
                                  width_source=src, layer=_layer_int(r["layer"]) if "layer" in cols else 1))
    out.sort(key=lambda b: (b.osm_id, b.geometry.coords[0]))
    return out


# --------------------------------------------------------------------------- 교량 상판(계약 C3, D-012)
def is_tunnel(value: Any) -> bool:
    """tunnel 태그가 있고 "no" 가 아니면 터널(yes, culvert, building_passage …)."""
    return is_bridge(value)


def resample_stations(L: float, step: float) -> np.ndarray:
    """0..L 을 step 이하 간격으로 고르게 나눈 위치."""
    n = max(1, int(np.ceil(L / step - 1e-9)))
    return np.linspace(0.0, L, n + 1)


def _sym_average(y: np.ndarray, k: int) -> np.ndarray:
    """고른 간격 표본의 대칭 이동평균(반폭 k 표본). 양 끝으로 갈수록 창을 줄여 끝값은 그대로 둔다."""
    n = len(y)
    if k <= 0 or n < 3:
        return y.copy()
    c = np.concatenate([[0.0], np.cumsum(y)])
    i = np.arange(n)
    r = np.minimum(np.minimum(k, i), n - 1 - i)
    return (c[i + r + 1] - c[i - r]) / (2 * r + 1)


def deck_profile(x: np.ndarray, h0: float, h1: float, req: np.ndarray, gmax: float,
                 smooth_m: float = 20.0, floor: np.ndarray | None = None,
                 share: np.ndarray | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """상판 윗면 종단(순수 함수, 테스트 대상).

    x: 0..L 고른 간격 위치, h0·h1: 양 끝 높이, req: 그 자리 목표 최소 윗면(수면 + 여유, 없으면 NaN), gmax: 경사 상한.
    floor: 어떤 경우에도 지킬 최소 윗면(수면 메시 + 0.2 m, 없으면 NaN). 호출 쪽이 끝 높이를 floor 이상으로 올려 준다.
    share: 나란한 다리의 같은 단면 상판 높이(없으면 NaN). 끝에서 경사 g 로 오를 수 있는 만큼만 따른다.
    1. 목표 = max(양 끝 선형 보간, req, floor). 2. 목표 이상인 가장 낮은 '경사 ≤ g' 함수(앞뒤 두 번 훑기).
    3. 양 끝에서 경사 g 로 오를 수 있는 높이 U 이하로 자른다(끝값 고정). 4. 창 smooth_m 대칭 이동평균으로 꺾임을 다듬는다
       (다듬어 낮아지는 몫 g·W/8 은 req·floor 에 미리 더한다). 5. max(·, min(req, U)), max(·, min(share, U)).
    6. max(·, floor) — 바닥은 경사 상한보다 앞선다. 양 끝 높이 차가 g·L 보다 크면 g 를 그만큼 올리고 기록한다.
    share 에는 다듬기 몫을 더하지 않는다(서로 공유하는 두 상판이 번갈아 올라가지 않게 = 고정점이 둘 중 큰 값).
    돌려주는 info: grade_used, grade_forced(올렸을 때), clearance_short_m(req 를 못 맞춘 최대 양), short_at_m,
    floor_over_grade_m(바닥 때문에 경사 상한을 넘긴 최대 양), share_short_m(share 를 못 따른 최대 양).
    """
    x = np.asarray(x, dtype=np.float64)
    req = np.asarray(req, dtype=np.float64)
    n = len(x)
    flo = np.full(n, np.nan) if floor is None else np.asarray(floor, dtype=np.float64)
    shr = np.full(n, np.nan) if share is None else np.asarray(share, dtype=np.float64)
    L = float(x[-1] - x[0])
    info: dict[str, Any] = {}
    if L <= 0:
        return np.full(n, h0, dtype=np.float64), info
    g = float(gmax)
    need = abs(h1 - h0) / L
    if need > g + 1e-9:
        g = need
        info["grade_forced"] = round(need, 4)
    info["grade_used"] = round(g, 4)
    xs = x - x[0]
    base = h0 + (h1 - h0) * xs / L
    has, hasf, hass = np.isfinite(req), np.isfinite(flo), np.isfinite(shr)
    dx = float(np.median(np.diff(xs))) if len(xs) > 1 else L
    W = min(float(smooth_m), L / 3.0) if smooth_m > 0 else 0.0
    delta = g * W / 8.0
    target = np.maximum(base, np.where(has, req + delta, -np.inf))
    target = np.maximum(target, np.where(hasf, flo + delta, -np.inf))
    P = target.copy()
    step = np.diff(xs) * g
    for i in range(1, len(P)):
        P[i] = max(P[i], P[i - 1] - step[i - 1])
    for i in range(len(P) - 2, -1, -1):
        P[i] = max(P[i], P[i + 1] - step[i])
    U = np.minimum(h0 + g * xs, h1 + g * (L - xs))
    prof = np.minimum(P, U)
    if W > 0:
        prof = _sym_average(prof, int(round(W / 2.0 / dx)))
    prof = np.maximum(prof, np.minimum(np.where(has, req, -np.inf), U))
    prof = np.maximum(prof, np.minimum(np.where(hasf, flo, -np.inf), U))
    if hass.any():
        prof = np.maximum(prof, np.minimum(np.where(hass, shr, -np.inf), U))
        ss = np.where(hass, shr - prof, -np.inf)
        if ss.max() > 0.01:
            info["share_short_m"] = round(float(ss.max()), 2)
    if hasf.any():
        over = np.where(hasf, flo - prof, -np.inf)
        if over.max() > 1e-9:
            info["floor_over_grade_m"] = round(float(over.max()), 2)
        prof = np.maximum(prof, np.where(hasf, flo, -np.inf))
    prof[0], prof[-1] = h0, h1
    if has.any():
        short = np.where(has, req - prof, -np.inf)
        k = int(np.argmax(short))
        if short[k] > 0.01:
            info["clearance_short_m"] = round(float(short[k]), 2)
            info["short_at_m"] = round(float(xs[k]), 1)
    return prof, info


def profile_breakpoints(x: np.ndarray, y: np.ndarray, tol: float = 0.005) -> np.ndarray:
    """종단(x, y)을 선형 조각으로 줄일 때 남길 위치(Douglas–Peucker, 세로 오차 ≤ tol). 양 끝 포함. 순수 함수.

    상판 메시는 표본 사이를 직선으로 잇는다. 1 m 종단을 4 m 간격으로만 잘라 쓰면 좁은 물줄기 위 바닥(수면 + 0.2 m)
    봉우리가 깎일 수 있어, 꺾이는 곳을 함께 남긴다.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = len(x)
    if n <= 2:
        return x.copy()
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = y[a] + (y[b] - y[a]) * (x[a + 1:b] - x[a]) / (x[b] - x[a])
        err = np.abs(y[a + 1:b] - seg)
        k = int(np.argmax(err))
        if err[k] > tol:
            m = a + 1 + k
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return x[keep]


def offset_lines(xy: np.ndarray, half: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """폴리라인 양옆 평행선(꺾인 곳은 마이터, 최대 2배). (왼쪽, 오른쪽, 단위 접선)."""
    xy = np.asarray(xy, dtype=np.float64)
    d = np.diff(xy, axis=0)
    u = d / np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-12)[:, None]
    t = np.empty_like(xy)
    t[0], t[-1] = u[0], u[-1]
    if len(xy) > 2:
        m = u[:-1] + u[1:]
        bad = np.hypot(m[:, 0], m[:, 1]) < 1e-9
        m[bad] = u[:-1][bad]
        t[1:-1] = m
    t /= np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-12)[:, None]
    nl = np.column_stack([-t[:, 1], t[:, 0]])
    scale = np.ones(len(xy))
    if len(xy) > 2:
        seg_n = np.column_stack([-u[:, 1], u[:, 0]])
        cos = np.clip((nl[1:-1] * seg_n[:-1]).sum(1), 0.5, 1.0)
        scale[1:-1] = 1.0 / cos
    off = nl * (half * scale)[:, None]
    return xy + off, xy - off, t


def _quads(P: np.ndarray, want: np.ndarray):
    """P: (k, 4, 3) 사각형, want: (k, 3) 바깥 방향. 감김을 want 쪽(반시계)으로 맞춰 (정점, 법선, 면)을 돌려준다.

    급히 꺾인 곳(짧은 변의 지그재그 계단 등)에서 마이터 선이 접혀 사각형이 나비꼴이 되면 두 삼각형 방향이
    서로 다를 수 있다. 그래서 삼각형마다 다시 want 쪽으로 맞춘다(밑면 삼각형이 위를 보거나 윗면에 구멍이 나지 않게).
    """
    n = np.cross(P[:, 2] - P[:, 0], P[:, 3] - P[:, 1])
    flip = (n * want).sum(1) < 0
    P = P.copy()
    P[flip] = P[flip][:, ::-1]
    n[flip] = -n[flip]
    n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
    k = len(P)
    V = P.reshape(-1, 3)
    N = np.repeat(n, 4, axis=0)
    b = (np.arange(k) * 4)[:, None]
    F = np.concatenate([b + [0, 1, 2], b + [0, 2, 3]], axis=0)
    w2 = np.concatenate([want, want], axis=0)
    tn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    bad = (tn * w2).sum(1) < 0
    F[bad] = F[bad][:, [0, 2, 1]]
    return V, N, F


def deck_mesh(xy: np.ndarray, top: np.ndarray, width: float, thickness: float,
              parapet_h: float, parapet_t: float):
    """상판 상자 + 양옆 난간(로컬 E, N, Up). (정점, 법선, 면, 바깥 외곽 왼쪽 선, 오른쪽 선). 순수 함수.

    단면: 바깥 폭 = width, 난간은 바깥 가장자리 안쪽으로 parapet_t 두께, 상판 윗면 위 parapet_h.
    면: 윗면(난간 사이), 밑면, 바깥 옆면(밑면 ~ 난간 꼭대기), 난간 윗면·안쪽 면, 양 끝 마구리.
    """
    xy = np.asarray(xy, dtype=np.float64)
    top = np.asarray(top, dtype=np.float64)
    half = width / 2.0
    pt = min(parapet_t, half * 0.4)
    Lo, Ro, t = offset_lines(xy, half)
    Li, Ri, _ = offset_lines(xy, half - pt)
    bot, rail = top - thickness, top + parapet_h

    def P3(a, z):
        return np.column_stack([a, z])

    up = np.array([0.0, 0.0, 1.0])
    nl = np.column_stack([-t[:, 1], t[:, 0], np.zeros(len(t))])
    a, b = slice(0, -1), slice(1, None)
    k = len(xy) - 1
    groups = []

    def strip(A, za, B, zb, want):
        """정점 i→i+1 사이 사각형 띠: (A_i, za_i), (B_i, zb_i), (B_i+1, zb_i+1), (A_i+1, za_i+1)."""
        Q = np.stack([P3(A[a], za[a]), P3(B[a], zb[a]), P3(B[b], zb[b]), P3(A[b], za[b])], axis=1)
        groups.append(_quads(Q, want))

    mid_n = (nl[a] + nl[b]) / 2.0
    strip(Li, top, Ri, top, np.tile(up, (k, 1)))            # 윗면
    strip(Lo, bot, Ro, bot, np.tile(-up, (k, 1)))           # 밑면
    strip(Lo, bot, Lo, rail, mid_n)                         # 왼쪽 바깥 옆면
    strip(Ro, bot, Ro, rail, -mid_n)                        # 오른쪽 바깥 옆면
    strip(Lo, rail, Li, rail, np.tile(up, (k, 1)))          # 왼쪽 난간 윗면
    strip(Ri, rail, Ro, rail, np.tile(up, (k, 1)))          # 오른쪽 난간 윗면
    strip(Li, top, Li, rail, -mid_n)                        # 왼쪽 난간 안쪽 면
    strip(Ri, top, Ri, rail, mid_n)                         # 오른쪽 난간 안쪽 면
    for i, sgn in ((0, -1.0), (len(xy) - 1, 1.0)):          # 양 끝 마구리
        w = np.array([[t[i, 0] * sgn, t[i, 1] * sgn, 0.0]])
        Q = np.array([
            [[*Lo[i], bot[i]], [*Ro[i], bot[i]], [*Ro[i], top[i]], [*Lo[i], top[i]]],
            [[*Lo[i], top[i]], [*Li[i], top[i]], [*Li[i], rail[i]], [*Lo[i], rail[i]]],
            [[*Ri[i], top[i]], [*Ro[i], top[i]], [*Ro[i], rail[i]], [*Ri[i], rail[i]]],
        ])
        groups.append(_quads(Q, np.repeat(w, 3, axis=0)))
    V, N, F, off = [], [], [], 0
    for v, n, f in groups:
        V.append(v)
        N.append(n)
        F.append(f + off)
        off += len(v)
    return np.concatenate(V), np.concatenate(N), np.concatenate(F), Lo, Ro


def footprint_ring(xy: np.ndarray, width: float) -> np.ndarray:
    """상판 바깥 외곽(닫지 않은 링): 왼쪽 선 앞으로 + 오른쪽 선 뒤로."""
    Lo, Ro, _ = offset_lines(xy, width / 2.0)
    return np.concatenate([Lo, Ro[::-1]])
