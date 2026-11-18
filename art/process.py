"""후처리: 원본 → (가운데 자르기) → 이음매 없애기 → (외벽: 창 가운데로) → 색 보정 → 계약 크기 JPG + art_manifest.json."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .api import count_calls, now_iso
from .seamless import adjust_color, center_crop, make_seamless, resize_wrap, seam_metrics, to_float, to_image
from .theme import ROOT, Theme, find_raw


def process_array(theme: Theme, asset: str, img: Image.Image) -> tuple[np.ndarray, dict[str, Any]]:
    kind = theme.kind(asset)
    adj = theme.adjust(asset)
    w, h = theme.out_size(asset)
    arr = to_float(img)
    info: dict[str, Any] = {"kind": theme.assets[asset]["kind"], "raw_size": [img.width, img.height]}
    crop = float(adj.get("crop", 1.0))
    aspect = w / h
    if crop < 1.0 or abs(img.width / img.height - aspect) > 0.01:
        arr = center_crop(arr, crop, aspect)
        info["center_crop"] = {"frac": crop, "aspect": round(aspect, 4), "size": [arr.shape[1], arr.shape[0]]}
    arr, info["seamless"] = make_seamless(arr, kind["band"], kind["search"], center=bool(kind.get("recenter")))
    arr, info["color"] = adjust_color(arr, **adj)
    arr = resize_wrap(arr, (w, h))
    info["out_size"] = [w, h]
    info["seam_check"] = seam_metrics(arr)
    return arr, info


def run_process(theme: Theme, only: list[str] | None = None) -> int:
    theme.out_dir.mkdir(parents=True, exist_ok=True)
    jpg_q = int(theme.data["defaults"].get("jpg_quality", 92))
    man_path = theme.out_dir / "art_manifest.json"
    old = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
    files: dict[str, Any] = dict(old.get("files", {}))
    missing = []
    for name in only or list(theme.assets):
        hit = find_raw(theme, name)
        if not hit:
            missing.append(name)
            print(f"[없음] {name}: 원본 캐시가 없습니다(generate 먼저)")
            continue
        raw_path, meta = hit
        img = Image.open(raw_path).convert("RGB")
        arr, info = process_array(theme, name, img)
        out = theme.out_dir / f"{name}.jpg"
        to_image(arr).save(out, "JPEG", quality=jpg_q, subsampling=0, optimize=True)
        files[out.name] = {
            "asset": name,
            "model": meta.get("model"),
            "route": meta.get("route"),
            "size": meta.get("size"),
            "quality": meta.get("quality"),
            "prompt": meta.get("prompt"),
            "generated_at": meta.get("created_at"),
            "raw_file": f"{theme.data['raw_dir']}/{raw_path.name}",
            "raw_sha256": meta.get("raw_sha256"),
            "postprocess": {**info, "jpg_quality": jpg_q},
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            **({"note": meta["note"]} if meta.get("note") else {}),
        }
        sc = info["seam_check"]
        print(f"[ok] {out.name} seam x {sc['seam_x_ratio']} y {sc['seam_y_ratio']}"
              f" period {info['seamless']['x']['period']}x{info['seamless']['y']['period']}")
    calls = count_calls(theme.calls_log)
    manifest = {
        "theme": theme.name,
        "generator": "art (python -m art), MonoGPT router (OpenAI-compatible) image generation",
        "endpoint": "https://monogpt.kr/api/monorouter/v1 (/images/generations, /chat/completions)",
        "updated_at": now_iso(),
        "license_note": "생성형 일반 소재 텍스처. 특정 장소·건물 재현 아님. 지도·로드뷰·실사진 참조 없음. 이용조건은 docs/DATA_SOURCES.md '아트 에셋(생성형)'.",
        "calls": {**calls, "note": "비교·재생성·실패 호출 포함 전체 API 호출 수(art/raw/<theme>/_calls.jsonl 집계)"},
        "files": {k: files[k] for k in sorted(files)},
    }
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("manifest:", man_path.relative_to(ROOT), f"(호출 {calls['total']}회)")
    return 1 if missing else 0
