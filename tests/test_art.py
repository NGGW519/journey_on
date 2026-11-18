"""art 패키지 단위 테스트(API 호출 없음): 이음매 처리, 크기 변경, 색 보정, 테마·캐시 키."""
from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage

from art.api import find_image_payload
from art.seamless import (adjust_color, center_crop, circular_centroid, make_seamless, recenter,
                          resize_wrap, seam_metrics)
from art.theme import MUST_PHRASE, Theme


def _texture(h: int = 256, w: int = 256, seed: int = 0) -> np.ndarray:
    """주기가 아닌 매끈한 잡음 + 기울기(왼쪽·오른쪽 끝이 확실히 다르다)."""
    rng = np.random.default_rng(seed)
    noise = ndimage.gaussian_filter(rng.random((h, w, 3)).astype(np.float32), sigma=(4, 4, 0))
    noise = (noise - noise.min()) / (noise.max() - noise.min())
    ramp = np.linspace(0, 1, w, dtype=np.float32)[None, :, None] * np.linspace(0.3, 1, h, dtype=np.float32)[:, None, None]
    return np.clip(0.5 * noise + 0.5 * ramp, 0, 1)


def _edge_gaps(a: np.ndarray) -> tuple[float, float, float, float]:
    lr = float(np.abs(a[:, 0] - a[:, -1]).mean())
    tb = float(np.abs(a[0] - a[-1]).mean())
    inner_x = float(np.abs(np.diff(a, axis=1)).mean())
    inner_y = float(np.abs(np.diff(a, axis=0)).mean())
    return lr, tb, inner_x, inner_y


def test_raw_texture_has_seams():
    lr, tb, ix, iy = _edge_gaps(_texture())
    assert lr > 5 * ix and tb > 5 * iy


@pytest.mark.parametrize("band,search", [(0.10, 0.10), (0.06, 0.06)])
def test_make_seamless_edges_match(band, search):
    out, info = make_seamless(_texture(), band, search)
    lr, tb, ix, iy = _edge_gaps(out)
    # 반복할 때 끝 열→첫 열 차이가 안쪽 이웃 열 차이와 같은 수준이어야 한다
    assert lr < 2.0 * ix, (lr, ix)
    assert tb < 2.0 * iy, (tb, iy)
    assert info["x"]["period"] == out.shape[1] and info["y"]["period"] == out.shape[0]


def test_roll_keeps_seamless_and_recenter_moves_feature():
    base, _ = make_seamless(_texture(seed=1) * 0.2 + 0.7, 0.1, 0.1)
    h, w, _ = base.shape
    feat = base.copy()
    feat[20:60, 10:50] = [0.1, 0.5, 0.4]  # 한쪽에 치우친 '창'
    out, info = recenter(feat)
    ys, xs = np.nonzero(np.abs(out - [0.1, 0.5, 0.4]).sum(axis=2) < 1e-6)
    assert abs(xs.mean() - w / 2) < 3 and abs(ys.mean() - h / 2) < 3
    lr, tb, ix, iy = _edge_gaps(out)
    assert lr < 2.0 * ix and tb < 2.0 * iy


def test_circular_centroid_wraps():
    wts = np.zeros(100)
    wts[[98, 99, 0, 1]] = 1  # 경계를 넘는 덩어리 → 0 근처
    c = circular_centroid(wts)
    assert min(c, 100 - c) < 1.0


def test_resize_wrap_keeps_seam():
    out, _ = make_seamless(_texture(300, 300, seed=2), 0.1, 0.1)
    small = resize_wrap(out, (128, 96))
    assert small.shape == (96, 128, 3)
    m = seam_metrics(small)
    assert m["seam_x_ratio"] < 2.0 and m["seam_y_ratio"] < 2.0


def test_adjust_color_lifts_dark_and_tames_saturation():
    dark = _texture(seed=3) * 0.25
    out, info = adjust_color(dark, min_luma=0.42)
    assert "gamma" in info and out.mean() > dark.mean()
    vivid = np.zeros((64, 64, 3), np.float32)
    vivid[..., 0] = 1.0
    vivid[..., 1] = 0.1
    out, info = adjust_color(vivid, max_sat=0.5)
    assert info["saturation_scale"] < 1.0


def test_flatten_reduces_low_frequency_and_stays_tileable():
    out, _ = make_seamless(_texture(seed=4), 0.1, 0.1)
    flat, _ = adjust_color(out, flatten=0.8)
    lo = lambda a: ndimage.gaussian_filter(a, sigma=(20, 20, 0), mode="wrap").std()  # noqa: E731
    assert lo(flat) < lo(out)
    m = seam_metrics(flat)
    assert m["seam_x_ratio"] < 2.0 and m["seam_y_ratio"] < 2.0


def test_center_crop_aspect():
    a = np.zeros((100, 100, 3), np.float32)
    assert center_crop(a, 1.0, 1.5).shape == (67, 100, 3)
    assert center_crop(a, 0.8).shape == (80, 80, 3)


def test_theme_contract():
    t = Theme.load("cozy")
    names = set(t.assets)
    expected = {f"facade_{c}" for c in ("residential", "commercial", "public", "industrial", "other")}
    expected |= {f"roof_{c}" for c in ("residential", "commercial", "public", "industrial", "other")}
    expected |= {"ground_grass", "ground_paving", "road_asphalt", "rail_ballast",
                 "bridge_road", "bridge_foot", "bridge_rail"}
    assert names == expected
    for n in names:
        p = " ".join(t.prompt(n).split())
        assert MUST_PHRASE in p, n
        assert t.route_for(t.model_for(n)) in ("images", "chat")
    assert t.out_size("facade_industrial") == (1536, 1024)


def test_cache_key_stable_and_sensitive():
    t = Theme.load("cozy")
    a = t.request("ground_grass")
    b = t.request("ground_grass")
    assert a.sha8 == b.sha8 and len(a.sha8) == 8
    assert a.raw_png(t.raw_dir).name == f"ground_grass__{a.sha8}.png"
    c = t.request("ground_grass", quality_supported=False)
    assert c.quality is None
    if a.quality is not None:  # 품질을 빼면 다른 요청 → 다른 캐시 파일
        assert c.sha8 != a.sha8
    other = t.request("ground_grass", model="gpt-image-2.6-flare")
    assert other.sha8 != a.sha8


def test_gemini_route_and_final_image():
    from art.api import aspect_ratio, gemini_final_image
    assert aspect_ratio("1536x1024") == "3:2" and aspect_ratio("1024x1024") == "1:1"
    b1, b2 = "B" * 200, "C" * 200
    data = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": b1}, "thought": True},  # 생각 중 이미지
        {"text": "here"},
        {"inlineData": {"mimeType": "image/png", "data": b2}},
    ]}}]}
    assert gemini_final_image(data) == ("b64", b2)
    t = Theme.load("cozy")
    r = t.request("facade_residential", model="gemini-3-pro-image")
    assert r.route == "gemini" and r.quality == t.data["defaults"]["gemini_image_size"]


def test_find_image_payload_shapes():
    b64 = "A" * 200
    assert find_image_payload({"data": [{"b64_json": b64}]}) == ("b64", b64)
    msg = {"choices": [{"message": {"images": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}}]}
    assert find_image_payload(msg) == ("b64", b64)
    assert find_image_payload({"choices": [{"message": {"content": "no image"}}]}) is None
