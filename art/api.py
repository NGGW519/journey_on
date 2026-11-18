"""MonoGPT 라우터 이미지 생성 호출.

- 키는 `.env`의 MONOGPT_API_KEY, 주소는 MONOGPT_BASE_URL 에서만 읽는다. 키는 출력·기록하지 않는다.
- 경로(route):
  - `images`: POST {base}/images/generations (OpenAI 규격, 응답 data[0].b64_json)
  - `gemini`: POST {base}/gemini/v1beta/models/{model}:generateContent (Gemini 고유 규격,
    응답 candidates[].content.parts[].inlineData, 생각 중 이미지(thought)는 뺀다)
  - `chat`:   POST {base}/chat/completions + modalities (라우터가 이미지 모델을 받지 않아 지금은 안 쓴다)
- 401·402·429 는 다시 시도하지 않고 StopGeneration 으로 멈춘다.
- 모든 호출(성공·실패)은 raw 폴더의 `_calls.jsonl`에 한 줄씩 남긴다(키 없음).
"""
from __future__ import annotations

import base64
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
STOP_STATUS = {401, 402, 429}


class StopGeneration(RuntimeError):
    """다시 시도하면 안 되는 오류(인증·결제·속도 제한). 전체 생성을 멈춘다."""


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class Result:
    png: bytes
    meta: dict[str, Any] = field(default_factory=dict)


def _short(obj: Any, n: int = 200) -> Any:
    """응답 구조를 기록할 때 긴 문자열(base64)을 줄인다."""
    if isinstance(obj, dict):
        return {k: _short(v, n) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_short(v, n) for v in obj]
    if isinstance(obj, str) and len(obj) > n:
        return obj[:60] + f"...<{len(obj)} chars>"
    return obj


_DATA_URL = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)")


def find_image_payload(obj: Any) -> tuple[str, str] | None:
    """응답 JSON 어디에 있든 이미지(base64 또는 URL)를 찾는다. ('b64'|'url', 값)."""
    if isinstance(obj, dict):
        for key in ("b64_json", "b64"):
            v = obj.get(key)
            if isinstance(v, str) and len(v) > 100:
                return "b64", v
        inline = obj.get("inline_data") or obj.get("inlineData")
        if isinstance(inline, dict) and isinstance(inline.get("data"), str):
            return "b64", inline["data"]
        for v in obj.values():
            hit = find_image_payload(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find_image_payload(v)
            if hit:
                return hit
    elif isinstance(obj, str):
        m = _DATA_URL.search(obj)
        if m:
            return "b64", m.group(1)
        if re.match(r"https?://\S+\.(png|jpg|jpeg|webp)(\?\S*)?$", obj.strip(), re.I):
            return "url", obj.strip()
    return None


def aspect_ratio(size: str) -> str:
    """'1536x1024' → '3:2'"""
    w, h = (int(v) for v in size.lower().split("x"))
    g = math.gcd(w, h)
    return f"{w // g}:{h // g}"


def gemini_final_image(data: Any) -> tuple[str, str] | None:
    """Gemini 응답에서 마지막 최종 이미지(생각 중 이미지 thought=true 는 뺀다)."""
    last = None
    for c in (data.get("candidates") or []) if isinstance(data, dict) else []:
        for p in (c.get("content") or {}).get("parts", []):
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data") and not p.get("thought"):
                last = ("b64", inline["data"])
    return last or find_image_payload(data)


class ImageClient:
    def __init__(self, calls_log: Path, timeout: float = 300.0):
        env = load_env()
        self._key = env.get("MONOGPT_API_KEY", "")
        self.base = env.get("MONOGPT_BASE_URL", "").rstrip("/")
        if not self._key or not self.base:
            raise SystemExit(".env 에 MONOGPT_API_KEY 와 MONOGPT_BASE_URL 이 필요합니다.")
        self.calls_log = calls_log
        self.timeout = timeout
        self._lock = threading.Lock()
        self.stopped: str | None = None

    # ---- 기록 -------------------------------------------------------------
    def _log(self, rec: dict[str, Any]) -> None:
        self.calls_log.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.calls_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    # ---- 호출 -------------------------------------------------------------
    def generate(self, *, name: str, model: str, route: str, prompt: str,
                 size: str, quality: str | None, extra: dict[str, Any] | None = None) -> Result:
        if self.stopped:
            raise StopGeneration(self.stopped)
        if route == "images":
            url = self.base + "/images/generations"
            body: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1, "size": size}
            if quality:
                body["quality"] = quality
        elif route == "chat":
            url = self.base + "/chat/completions"
            body = {"model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "modalities": ["image", "text"]}
        elif route == "gemini":
            # Gemini 고유 규격(라우터가 그대로 넘긴다). quality 자리에 imageSize(1K/2K/4K)를 쓴다.
            url = self.base + f"/gemini/v1beta/models/{model}:generateContent"
            img_cfg: dict[str, Any] = {"aspectRatio": aspect_ratio(size)}
            if quality:
                img_cfg["imageSize"] = quality
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": img_cfg}}
        else:
            raise ValueError(f"알 수 없는 route: {route}")
        if extra:
            body.update(extra)

        t0 = time.time()
        rec: dict[str, Any] = {"time": now_iso(), "name": name, "model": model, "route": route,
                               "size": size, "quality": quality,
                               "extra": extra or {}, "endpoint": url.replace(self.base, "{base}")}
        try:
            r = requests.post(url, headers=self._headers(), json=body, timeout=self.timeout)
        except requests.RequestException as e:
            rec.update(status=None, error=f"{type(e).__name__}: {e}"[:300], seconds=round(time.time() - t0, 1))
            self._log(rec)
            raise ApiError(0, str(e)) from e
        rec["status"] = r.status_code
        rec["seconds"] = round(time.time() - t0, 1)
        if r.status_code != 200:
            msg = r.text[:600]
            rec["error"] = msg
            self._log(rec)
            if r.status_code in STOP_STATUS:
                self.stopped = f"HTTP {r.status_code} ({name}, {model}): {msg[:200]}"
                raise StopGeneration(self.stopped)
            raise ApiError(r.status_code, msg)
        try:
            data = r.json()
        except ValueError as e:
            rec["error"] = "JSON 아님: " + r.text[:300]
            self._log(rec)
            raise ApiError(200, rec["error"]) from e
        hit = gemini_final_image(data) if route == "gemini" else find_image_payload(data)
        if not hit:
            rec["error"] = "응답에 이미지 없음"
            rec["response"] = _short(data)
            self._log(rec)
            raise ApiError(200, "응답에 이미지 없음: " + json.dumps(_short(data), ensure_ascii=False)[:500])
        kind, val = hit
        if kind == "b64":
            png = base64.b64decode(re.sub(r"\s+", "", val))
        else:
            png = requests.get(val, timeout=self.timeout).content
        meta = {k: v for k, v in (data.items() if isinstance(data, dict) else [])
                if k in ("usage", "created", "usageMetadata", "modelVersion")}
        if route == "gemini":
            texts = [p.get("text") for c in data.get("candidates", [])
                     for p in (c.get("content") or {}).get("parts", []) if p.get("text") and not p.get("thought")]
            if texts:
                meta["text"] = " ".join(texts)[:500]
            meta["usage"] = data.get("usageMetadata")
        try:
            first = (data.get("data") or [{}])[0] if route == "images" else {}
            if isinstance(first, dict) and first.get("revised_prompt"):
                meta["revised_prompt"] = first["revised_prompt"]
        except Exception:
            pass
        if route == "chat":
            try:
                txt = data["choices"][0]["message"].get("content")
                if isinstance(txt, str) and txt.strip():
                    meta["text"] = _DATA_URL.sub("<image>", txt)[:500]
            except Exception:
                pass
        rec["bytes"] = len(png)
        rec["usage"] = meta.get("usage")
        self._log(rec)
        return Result(png=png, meta=meta)


def count_calls(calls_log: Path) -> dict[str, Any]:
    """`_calls.jsonl` 집계: 전체 호출 수, 상태별, 모델별."""
    total, by_status, by_model = 0, {}, {}
    if calls_log.exists():
        for line in calls_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            total += 1
            s = str(rec.get("status"))
            by_status[s] = by_status.get(s, 0) + 1
            m = rec.get("model", "?")
            by_model[m] = by_model.get(m, 0) + 1
    return {"total": total, "by_status": by_status, "by_model": by_model}
