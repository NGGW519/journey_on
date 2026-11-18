"""PreToolUse 훅: 약관상 모델링 원천으로 쓸 수 없는 민간 지도·로드뷰·구글 3D 타일에
자동으로 접근하는 호출을 막는다.

기획서 원칙: 카카오·네이버·구글 로드뷰와 구글 3D 타일은 캡처·가공·3D 파생이 금지다.
사람이 브라우저에서 눈으로 비교하는 검수에만 쓴다. 에이전트는 접근할 이유가 없다.

종료 코드 2 = 호출 차단(stderr 내용이 Claude에게 전달됨), 0 = 허용.
"""
from __future__ import annotations

import json
import re
import sys

FORBIDDEN = [
    # Google: Map Tiles API(Photorealistic 3D Tiles 포함), Street View, 지도 타일
    r"tile\.googleapis\.com",
    r"maps\.googleapis\.com/maps/api/streetview",
    r"streetviewpixels",
    r"(?<![\w.])maps\.google\.",
    r"google\.[a-z.]+/maps",
    r"\bmt[0-3]\.google\.com",
    r"\bkhms[0-3]?\.google",
    # Kakao / Daum: 지도·로드뷰·지도 API
    r"map\.kakao\.com",
    r"dapi\.kakao\.com",
    r"rv\.kakao",
    r"map\.daum\.net",
    r"map\d?\.daumcdn\.net",
    r"\bt\d\.daumcdn\.net",
    # Naver: 지도·파노라마·지도 API
    r"map\.naver\.com",
    r"naveropenapi\.apigw",
    r"map\.pstatic\.net",
    r"panorama\.pstatic\.net",
    r"pano\.map\.naver",
]
PATTERN = re.compile("|".join(FORBIDDEN), re.IGNORECASE)


def collect_text(tool_input: dict) -> str:
    parts = []
    for key in ("command", "url", "prompt", "query", "script"):
        val = tool_input.get(key)
        if isinstance(val, str):
            parts.append(val)
    return "\n".join(parts)


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    text = collect_text(event.get("tool_input") or {})
    m = PATTERN.search(text)
    if not m:
        return 0
    msg = (
        f"[license_guard] 차단: '{m.group(0)}' — 민간 지도·로드뷰·구글 3D 타일은 약관상 "
        "캡처·가공·3D 파생이 금지라 자동 접근하지 않습니다(기획서 '원칙'). "
        "공식 원천(국토정보플랫폼·브이월드)이나 OSM/Copernicus 임시 데이터를 쓰고, "
        "로드뷰 비교가 필요하면 docs/qa/roadview_checklist.md 에 사람이 볼 지점을 적으세요."
    )
    sys.stderr.buffer.write(msg.encode("utf-8"))
    return 2


if __name__ == "__main__":
    sys.exit(main())
