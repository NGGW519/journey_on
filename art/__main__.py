"""CLI: python -m art {generate,process,sheet,compare} --theme cozy"""
from __future__ import annotations

import argparse
import sys

from .theme import Theme


def _list(s: str | None) -> list[str] | None:
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="art", description="생성형 아트 텍스처 도구(docs/ART.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--theme", default="cozy")
        return p

    p = add("generate", "원본 생성(캐시가 있으면 건너뜀)")
    p.add_argument("--only", help="쉼표로 구분한 에셋 이름")
    p.add_argument("--force", action="store_true", help="캐시가 있어도 다시 생성(이전 원본은 .prevN 으로 보관)")
    p.add_argument("--model", help="테마에 정한 모델 대신 이 모델로(실험용)")
    p.add_argument("--jobs", type=int, default=4, help="동시 호출 수")

    p = add("process", "후처리 → Unity Assets/Art/<Theme>/*.jpg + art_manifest.json")
    p.add_argument("--only", help="쉼표로 구분한 에셋 이름")

    add("sheet", "검토용 3×3 반복 미리보기와 모음 → _workspace/art/")

    p = add("compare", "같은 프롬프트를 여러 모델로 만들어 비교 → _workspace/art/model_compare/")
    p.add_argument("--models", help="쉼표로 구분한 모델(기본: 테마 compare.models)")
    p.add_argument("--asset", help="비교에 쓸 에셋(기본: 테마 compare.asset)")
    p.add_argument("--jobs", type=int, default=4)

    args = ap.parse_args(argv)
    theme = Theme.load(args.theme)
    if args.cmd == "generate":
        from .generate import run_generate
        return run_generate(theme, _list(args.only), args.force, args.model, args.jobs)
    if args.cmd == "process":
        from .process import run_process
        return run_process(theme, _list(args.only))
    if args.cmd == "sheet":
        from .sheet import run_sheet
        return run_sheet(theme)
    if args.cmd == "compare":
        from .generate import run_compare
        return run_compare(theme, _list(args.models), args.asset, args.jobs)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
