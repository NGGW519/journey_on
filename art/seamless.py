"""이음매 없는 반복 텍스처 만들기(순수 numpy/scipy/PIL, API 없음).

방법(축마다, 가로 다음 세로):
1. 겹침 찾기: 앞쪽 띠 I[0:b]와 가장 비슷한 뒤쪽 띠 I[P:P+b]를 P ∈ [N-b-s, N-b]에서 찾는다
   (규칙 무늬면 위상이 맞는 곳을 고르게 된다).
2. 교차 페이드: 결과 T = I[0:P], 앞 띠만 T[x] = α(x)·I[x] + (1-α(x))·I[x+P] (α: 0→1 smoothstep).
   T[0] = I[P] 이므로 T 를 반복하면 T[P-1] → T[0] 이 원본의 이웃 화소 I[P-1] → I[P] 가 되어 이음매가 없다.
3. 섞인 띠는 고주파가 약해지므로(두 무늬 평균) 상관도를 보고 세부 대비를 되살린다.
반복 텍스처는 주기적이라 np.roll 로 밀어도 이음매가 생기지 않는다. 외벽은 창 무게중심(원형 평균)을 가운데로 민다.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def to_float(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.rint(arr * 255.0), 0, 255).astype(np.uint8), "RGB")


def luma(arr: np.ndarray) -> np.ndarray:
    return arr @ LUMA


def _smoothstep(n: int) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)
    return t * t * (3.0 - 2.0 * t)


def best_overlap(arr: np.ndarray, axis: int, band: int, search: int) -> tuple[int, float]:
    """앞 띠와 가장 잘 맞는 뒤 띠의 시작 P 와 그때의 평균 제곱 오차."""
    a = np.moveaxis(arr, axis, 0)
    n = a.shape[0]
    g = luma(a) if a.ndim == 3 else a
    # 속도·잡음 대비: 띠에 수직인 방향으로 2배 줄인다
    g = g[:, : (g.shape[1] // 2) * 2].reshape(n, -1, 2).mean(axis=2)
    head = g[:band]
    lo, hi = max(band, n - band - search), n - band
    best_p, best_e = hi, math.inf
    for p in range(lo, hi + 1):
        e = float(np.mean((head - g[p:p + band]) ** 2))
        if e < best_e:
            best_p, best_e = p, e
    return best_p, best_e


def seamless_axis(arr: np.ndarray, axis: int, band_frac: float, search_frac: float) -> tuple[np.ndarray, dict[str, Any]]:
    a = np.moveaxis(arr, axis, 0)
    n = a.shape[0]
    band = max(4, int(round(band_frac * n)))
    search = max(0, int(round(search_frac * n)))
    p, err = best_overlap(arr, axis, band, search)
    head, tail = a[:band], a[p:p + band]
    alpha = _smoothstep(band).reshape((band,) + (1,) * (a.ndim - 1))
    mixed = alpha * head + (1.0 - alpha) * tail
    # 세부 되살리기: 섞인 띠의 고주파를 분산 보존 계수로 키운다(두 띠 상관도 ρ 반영)
    hp_a = head - ndimage.gaussian_filter(head, sigma=(3, 3, 0)[: a.ndim])
    hp_b = tail - ndimage.gaussian_filter(tail, sigma=(3, 3, 0)[: a.ndim])
    denom = float(np.sqrt(np.mean(hp_a ** 2) * np.mean(hp_b ** 2))) or 1.0
    rho = float(np.clip(np.mean(hp_a * hp_b) / denom, 0.0, 1.0))
    var = alpha ** 2 + (1 - alpha) ** 2 + 2 * alpha * (1 - alpha) * rho
    gain = 1.0 / np.sqrt(np.maximum(var, 1e-6))
    low = ndimage.gaussian_filter(mixed, sigma=(3, 3, 0)[: a.ndim])
    mixed = low + (mixed - low) * gain
    out = a[:p].copy()
    out[:band] = mixed
    info = {"axis": "x" if axis == 1 else "y", "size_in": n, "period": p, "band_px": band,
            "search_px": search, "band_mse": round(err, 6), "detail_rho": round(rho, 3)}
    return np.moveaxis(out, 0, axis), info


def circular_centroid(weights: np.ndarray) -> float:
    """주기 신호의 무게중심(0..n). 가중치가 거의 없으면 n/2."""
    n = len(weights)
    if weights.sum() <= 1e-9:
        return n / 2
    th = 2 * np.pi * np.arange(n) / n
    ang = math.atan2(float((weights * np.sin(th)).sum()), float((weights * np.cos(th)).sum()))
    return (ang % (2 * np.pi)) * n / (2 * np.pi)


def feature_mask(arr: np.ndarray, edge_frac: float = 0.06) -> np.ndarray:
    """벽(가장자리 띠의 대표색)과 많이 다른 화소 = 창·차양 같은 요소."""
    h, w, _ = arr.shape
    e = max(2, int(edge_frac * min(h, w)))
    edge = np.concatenate([arr[:e].reshape(-1, 3), arr[-e:].reshape(-1, 3),
                           arr[:, :e].reshape(-1, 3), arr[:, -e:].reshape(-1, 3)])
    wall = np.median(edge, axis=0)
    d = np.sqrt(((arr - wall) ** 2).sum(axis=2))
    thr = max(0.12, float(np.percentile(d, 60)))
    return (d > thr).astype(np.float32)


def recenter(arr: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """창 무게중심을 가운데로 민다(주기 텍스처라 이음매가 생기지 않는다)."""
    m = feature_mask(arr)
    h, w = m.shape
    cx = circular_centroid(m.sum(axis=0))
    cy = circular_centroid(m.sum(axis=1))
    dx = int(round(w / 2 - cx))
    dy = int(round(h / 2 - cy))
    out = np.roll(arr, (dy, dx), axis=(0, 1))
    return out, {"roll_px": [dx, dy], "feature_ratio": round(float(m.mean()), 3)}


def make_seamless(arr: np.ndarray, band: float, search: float, center: bool = False) -> tuple[np.ndarray, dict[str, Any]]:
    out, ix = seamless_axis(arr, 1, band, search)
    out, iy = seamless_axis(out, 0, band, search)
    info: dict[str, Any] = {"x": ix, "y": iy}
    if center:
        out, info["recenter"] = recenter(out)
    return out, info


def center_crop(arr: np.ndarray, frac: float = 1.0, aspect: float | None = None) -> np.ndarray:
    """가운데 frac 만큼 자르고, aspect(가로/세로)가 주어지면 그 비율로 맞춘다."""
    h, w, _ = arr.shape
    ch, cw = h * frac, w * frac
    if aspect:
        if cw / ch > aspect:
            cw = ch * aspect
        else:
            ch = cw / aspect
    ch, cw = int(round(ch)), int(round(cw))
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return arr[y0:y0 + ch, x0:x0 + cw]


def resize_wrap(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """반복 텍스처 크기 바꾸기. 3×3으로 이어 붙여 줄인 뒤 가운데만 쓰므로 가장자리에서도 이웃이 맞다."""
    w, h = size
    big = np.tile(arr, (3, 3, 1))
    img = to_image(big).resize((3 * w, 3 * h), Image.Resampling.LANCZOS)
    return to_float(img)[h:2 * h, w:2 * w]


def adjust_color(arr: np.ndarray, *, min_luma: float = 0.0, max_sat: float = 1.0,
                 contrast: float = 1.0, flatten: float = 0.0, **_: Any) -> tuple[np.ndarray, dict[str, Any]]:
    """반복 텍스처용 색 보정. flatten 은 큰 얼룩을 줄이고(wrap 블러), 나머지는 필요할 때만 살짝."""
    out = arr.astype(np.float32).copy()
    info: dict[str, Any] = {}
    if flatten > 0:
        sigma = max(out.shape[:2]) / 12
        low = ndimage.gaussian_filter(out, sigma=(sigma, sigma, 0), mode="wrap")
        out = out - flatten * (low - low.mean(axis=(0, 1), keepdims=True))
        info["flatten"] = flatten
    if contrast != 1.0:
        mean = out.mean(axis=(0, 1), keepdims=True)
        out = mean + (out - mean) * contrast
        info["contrast"] = contrast
    out = np.clip(out, 0, 1)
    y = float(luma(out).mean())
    info["mean_luma_in"] = round(y, 3)
    if 0 < y < min_luma:
        g = math.log(min_luma) / math.log(y)
        out = out ** g
        info["gamma"] = round(g, 3)
    mx, mn = out.max(axis=2), out.min(axis=2)
    s = float(np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0).mean())
    info["mean_sat_in"] = round(s, 3)
    if s > max_sat:
        k = max_sat / s
        gray = luma(out)[..., None]
        out = gray + (out - gray) * k
        info["saturation_scale"] = round(k, 3)
    out = np.clip(out, 0, 1)
    info["mean_luma_out"] = round(float(luma(out).mean()), 3)
    return out, info


def seam_metrics(arr: np.ndarray) -> dict[str, float]:
    """반복 이음매 점검: 끝 열↔첫 열(반복 시 이웃) 차이 ÷ 안쪽 이웃 열 차이의 평균. 1 근처면 이음매가 안 보인다."""
    def ratio(a: np.ndarray) -> tuple[float, float]:
        seam = float(np.abs(a[:, -1] - a[:, 0]).mean())
        inner = float(np.abs(np.diff(a, axis=1)).mean()) or 1e-6
        return seam, seam / inner
    sx, rx = ratio(arr)
    sy, ry = ratio(np.swapaxes(arr, 0, 1))
    return {"seam_x": round(sx, 4), "seam_x_ratio": round(rx, 2),
            "seam_y": round(sy, 4), "seam_y_ratio": round(ry, 2)}


def tile_preview(img: Image.Image, n: int = 3, cell_w: int = 256) -> Image.Image:
    w, h = img.size
    cw, chh = cell_w, int(round(cell_w * h / w))
    small = img.resize((cw, chh), Image.Resampling.LANCZOS)
    out = Image.new("RGB", (cw * n, chh * n))
    for j in range(n):
        for i in range(n):
            out.paste(small, (i * cw, j * chh))
    return out
