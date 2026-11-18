"""생성: 캐시 확인 → MonoGPT 호출 → 원본 PNG·메타 저장. 모델 비교도 여기서 한다."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from PIL import Image

from .api import ApiError, ImageClient, StopGeneration, now_iso
from .theme import Request, Theme


def _state_path(theme: Theme) -> Path:
    return theme.raw_dir / "_api_state.json"


def load_state(theme: Theme) -> dict[str, Any]:
    p = _state_path(theme)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"quality_supported": {}}


def save_state(theme: Theme, state: dict[str, Any]) -> None:
    p = _state_path(theme)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _backup(path: Path) -> None:
    """--force 로 덮어쓰기 전에 이전 원본을 .prevN 으로 남긴다."""
    if not path.exists():
        return
    i = 1
    while path.with_name(f"{path.stem}.prev{i}{path.suffix}").exists():
        i += 1
    shutil.move(str(path), str(path.with_name(f"{path.stem}.prev{i}{path.suffix}")))


def _save(theme: Theme, req: Request, png: bytes, meta: dict[str, Any], note: str | None) -> Path:
    img = Image.open(io.BytesIO(png))
    img.load()
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")  # 응답이 JPEG/WEBP여도 PNG로 통일
    data = buf.getvalue() if img.format != "PNG" else png
    out = req.raw_png(theme.raw_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    rec = {**req.key_dict(), "name": req.name, "sha8": req.sha8, "created_at": now_iso(),
           "raw_file": out.name, "raw_sha256": hashlib.sha256(data).hexdigest(),
           "raw_size": list(img.size), "response_format": img.format, "response": meta}
    if note:
        rec["note"] = note
    req.raw_meta(theme.raw_dir).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def generate_one(theme: Theme, client: ImageClient, state: dict[str, Any], asset: str,
                 model: str | None = None, force: bool = False) -> tuple[str, str]:
    """반환 (상태, 설명). 상태: cached | ok | error | stop"""
    m = theme.model_for(asset, model)
    qs = state["quality_supported"].get(m)  # None = 아직 모름 → 보내 본다
    req = theme.request(asset, model, quality_supported=qs)
    png_path = req.raw_png(theme.raw_dir)
    if png_path.exists() and not force:
        return "cached", png_path.name
    if force:
        _backup(png_path)
        _backup(req.raw_meta(theme.raw_dir))
    note = None
    try:
        res = client.generate(name=asset, model=req.model, route=req.route, prompt=req.prompt,
                              size=req.size, quality=req.quality)
        if req.quality and qs is None:
            state["quality_supported"][m] = True
            save_state(theme, state)
    except ApiError as e:
        if req.quality and e.status == 400 and ("quality" in e.message.lower() or "imagesize" in e.message.lower()):
            state["quality_supported"][m] = False
            save_state(theme, state)
            note = f"quality/imageSize={req.quality} 거부됨(400) → 빼고 다시 호출"
            req = theme.request(asset, model, quality_supported=False)
            try:
                res = client.generate(name=asset, model=req.model, route=req.route, prompt=req.prompt,
                                      size=req.size, quality=None)
            except ApiError as e2:
                return "error", str(e2)[:400]
        else:
            return "error", str(e)[:400]
    except StopGeneration as e:
        return "stop", str(e)
    out = _save(theme, req, res.png, res.meta, note)
    return "ok", out.name


def run_generate(theme: Theme, only: list[str] | None, force: bool, model: str | None, jobs: int) -> int:
    names = only or list(theme.assets)
    bad = [n for n in names if n not in theme.assets]
    if bad:
        raise SystemExit(f"테마에 없는 에셋: {bad}")
    client = ImageClient(theme.calls_log)
    state = load_state(theme)
    todo = []
    for n in names:
        if not force and _cached(theme, state, n, model):
            print(f"[cache] {n}")
        else:
            todo.append(n)
    # 품질 지원 여부를 모르면 첫 장을 먼저 혼자 보내 확인한다
    first_unknown = [n for n in todo if state["quality_supported"].get(theme.model_for(n, model)) is None
                     and theme.route_for(theme.model_for(n, model)) == "images"]
    results: dict[str, tuple[str, str]] = {}
    if first_unknown:
        n0 = first_unknown[0]
        results[n0] = generate_one(theme, client, state, n0, model, force)
        print(f"[{results[n0][0]}] {n0}: {results[n0][1]}")
        todo.remove(n0)
        if results[n0][0] == "stop":
            print("중단:", results[n0][1])
            return 2
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {ex.submit(generate_one, theme, client, state, n, model, force): n for n in todo}
        for f in as_completed(futs):
            n = futs[f]
            try:
                results[n] = f.result()
            except Exception as e:  # noqa: BLE001
                results[n] = ("error", f"{type(e).__name__}: {e}"[:400])
            print(f"[{results[n][0]}] {n}: {results[n][1]}", flush=True)
    stops = [r for r in results.values() if r[0] == "stop"]
    errs = [n for n, r in results.items() if r[0] == "error"]
    if stops:
        print("중단(401/402/429):", stops[0][1])
        return 2
    if errs:
        print("실패:", errs)
        return 1
    return 0


def _cached(theme: Theme, state: dict[str, Any], asset: str, model: str | None) -> bool:
    m = theme.model_for(asset, model)
    req = theme.request(asset, model, quality_supported=state["quality_supported"].get(m))
    return req.raw_png(theme.raw_dir).exists()


def run_compare(theme: Theme, models: list[str] | None, asset: str | None, jobs: int) -> int:
    """같은 에셋 프롬프트를 여러 모델로 만들어 _workspace/art/model_compare/ 에 모은다."""
    from .process import process_array  # 순환 import 피하기
    from .seamless import tile_preview, to_image
    from .sheet import label_grid

    cfg = theme.data.get("compare", {})
    asset = asset or cfg["asset"]
    models = models or cfg["models"]
    client = ImageClient(theme.calls_log)
    state = load_state(theme)
    out_dir = theme.sheet_dir / "model_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {ex.submit(generate_one, theme, client, state, asset, m, False): m for m in models}
        for f in as_completed(futs):
            m = futs[f]
            try:
                results[m] = f.result()
            except Exception as e:  # noqa: BLE001
                results[m] = ("error", f"{type(e).__name__}: {e}"[:400])
            print(f"[{results[m][0]}] {m}: {results[m][1]}", flush=True)
    raws, previews, labels, summary = [], [], [], {}
    for m in models:
        st, msg = results[m]
        summary[m] = {"status": st, "detail": msg}
        if st not in ("ok", "cached"):
            continue
        png = theme.raw_dir / msg
        img = Image.open(png).convert("RGB")
        img.save(out_dir / f"{asset}__{m}.png")
        arr, info = process_array(theme, asset, img)
        prev = tile_preview(to_image(arr), 3, 256)
        prev.save(out_dir / f"{asset}__{m}_3x3.jpg", quality=90)
        summary[m].update(raw_file=msg, raw_size=list(img.size), process=info)
        raws.append(img.resize((768, int(768 * img.height / img.width))))
        previews.append(prev)
        labels.append(m)
    if raws:
        label_grid([raws, previews], labels, out_dir / f"compare_{asset}.jpg")
    (out_dir / f"compare_{asset}.json").write_text(json.dumps({"asset": asset, "prompt": theme.prompt(asset),
                                                       "results": summary}, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print("비교 이미지:", out_dir / f"compare_{asset}.jpg")
    return 0 if all(v["status"] in ("ok", "cached") for v in summary.values()) else 1
