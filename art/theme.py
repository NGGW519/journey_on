"""테마 YAML 읽기, 프롬프트 조립, 캐시 키."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
THEMES = Path(__file__).resolve().parent / "themes"
MUST_PHRASE = ("flat orthographic, fills the whole frame edge to edge, seamless tileable, no text, "
               "no watermark, no people, no perspective, no cast shadows from outside objects")


def _clean(s: str) -> str:
    return " ".join(str(s).split())


@dataclass(frozen=True)
class Request:
    """한 번의 생성 요청. 같은 Request 는 같은 캐시 파일을 쓴다."""
    name: str
    model: str
    route: str
    prompt: str
    size: str
    quality: str | None

    def key_dict(self) -> dict[str, Any]:
        return {"model": self.model, "route": self.route, "prompt": self.prompt,
                "size": self.size, "quality": self.quality}

    @property
    def sha8(self) -> str:
        blob = json.dumps(self.key_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:8]

    def raw_png(self, raw_dir: Path) -> Path:
        return raw_dir / f"{self.name}__{self.sha8}.png"

    def raw_meta(self, raw_dir: Path) -> Path:
        return raw_dir / f"{self.name}__{self.sha8}.json"


class Theme:
    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self.data = data
        self.path = path
        self.name: str = data["theme"]
        self.raw_dir = ROOT / data["raw_dir"]
        self.out_dir = ROOT / data["out_dir"]
        self.sheet_dir = ROOT / data["sheet_dir"]
        self.assets: dict[str, dict[str, Any]] = data["assets"]

    @classmethod
    def load(cls, name: str) -> "Theme":
        path = THEMES / f"{name}.yaml"
        if not path.exists():
            raise SystemExit(f"테마 파일이 없습니다: {path}")
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f), path)

    @property
    def calls_log(self) -> Path:
        return self.raw_dir / "_calls.jsonl"

    def kind(self, asset: str) -> dict[str, Any]:
        return self.data["kinds"][self.assets[asset]["kind"]]

    def prompt(self, asset: str) -> str:
        a = self.assets[asset]
        parts = [_clean(a["subject"]), _clean(self.kind(asset)["frame"]),
                 _clean(self.data["style"]), "Requirements: " + _clean(self.data["must"])]
        return "\n\n".join(parts)

    def model_for(self, asset: str, override: str | None = None) -> str:
        return override or self.assets[asset].get("model") or self.data["defaults"]["model"]

    def route_for(self, model: str) -> str:
        try:
            return self.data["models"][model]["route"]
        except KeyError as e:
            raise SystemExit(f"cozy.yaml models 에 없는 모델: {model}") from e

    def request(self, asset: str, model: str | None = None,
                quality_supported: bool | None = True) -> Request:
        a = self.assets[asset]
        d = self.data["defaults"]
        m = self.model_for(asset, model)
        route = self.route_for(m)
        if route == "images":
            quality = a.get("quality", d.get("quality"))
        elif route == "gemini":  # Gemini 는 품질 대신 출력 해상도(imageSize)
            quality = a.get("gemini_image_size", d.get("gemini_image_size"))
        else:
            quality = None
        if quality_supported is False:
            quality = None
        return Request(name=asset, model=m, route=route, prompt=self.prompt(asset),
                       size=a.get("size", d["size"]), quality=quality)

    def adjust(self, asset: str) -> dict[str, Any]:
        out = dict(self.data.get("adjust_defaults", {}))
        out.update(self.assets[asset].get("adjust") or {})
        return out

    def out_size(self, asset: str) -> tuple[int, int]:
        w, h = self.assets[asset]["out_size"]
        return int(w), int(h)


def find_raw(theme: Theme, asset: str, model: str | None = None) -> tuple[Path, dict[str, Any]] | None:
    """현재 프롬프트·모델에 맞는 캐시 원본. 품질 파라미터를 못 보낸 경우(quality=None)도 찾는다."""
    for qs in (True, False):
        req = theme.request(asset, model, quality_supported=qs)
        png = req.raw_png(theme.raw_dir)
        if png.exists():
            meta_p = req.raw_meta(theme.raw_dir)
            meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
            return png, meta
    return None
