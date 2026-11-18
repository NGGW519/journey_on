"""CLI 진입점. 단계 목록은 STAGES 하나에서 관리한다."""
from __future__ import annotations

import argparse
import sys

from .config import load_config


MODULES = {"grid": "grid_stage"}  # 단계 이름 → 모듈 이름(다르면)


def _stage(name: str):
    def run(cfg, area, args):
        mod = __import__(f"pipeline.{MODULES.get(name, name)}", fromlist=["run"])
        return mod.run(cfg, area, args)
    return run


# grid→export 순서(=all 이 실행하는 순서). roads = 도로 마스크 + 교량 상판(C2·C3), skirt = 원경(C4)
STAGES = ["grid", "dem", "ortho", "buildings", "water", "roads", "skirt", "export"]


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--area", default="phase1")
        return p

    p = add("fetch-open", "공개 대체 데이터(Copernicus DEM, OSM) 받기")
    p.add_argument("--only", choices=["dem", "osm"], default=None)
    p.add_argument("--layers", nargs="*", default=None,
                   help="OSM 레이어만(예: roads). skirt_water_areas 는 원경 범위 수면(기본 목록에 없음, 따로 지정)")
    p = add("register", "공식 원천 파일을 manifest 에 등록")
    p.add_argument("--layer", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--license", required=True)
    p.add_argument("--attribution", default="")
    p.add_argument("--date", default="")
    p.add_argument("--crs", default="")
    p.add_argument("--kind", default="official")
    p.add_argument("--url", default="")
    p.add_argument("--notes", default="")
    p = add("inspect", "원천 파일 요약(좌표계·범위·개수)")
    p.add_argument("--path", required=True)
    for s in STAGES:
        add(s, f"{s} 단계 실행")
    add("all", "grid→export 전부")
    p = add("validate", "자동 검사")
    p.add_argument("--stage", default=None)
    add("overview", "범위 개요도(PNG) 그리기")
    args = ap.parse_args(argv)
    cfg = load_config()

    if args.cmd == "fetch-open":
        from . import fetch_open
        if args.only in (None, "dem"):
            fetch_open.fetch_copernicus(cfg, args.area)
        if args.only in (None, "osm"):
            print(fetch_open.fetch_osm(cfg, args.area, args.layers))
        return 0
    if args.cmd == "register":
        from . import sources
        e = sources.register(cfg, args.layer, args.path, source=args.source, license=args.license,
                             attribution=args.attribution, date=args.date, crs=args.crs,
                             interim=False, kind=args.kind, url=args.url, notes=args.notes)
        print(f"등록: {args.layer} ← {e['path']}")
        return 0
    if args.cmd == "inspect":
        from .inspect_src import inspect
        inspect(args.path)
        return 0
    if args.cmd == "validate":
        from . import validate
        return validate.run(cfg, args.area, args)
    if args.cmd == "overview":
        from . import overview
        overview.run(cfg, args.area, args)
        return 0
    stages = STAGES if args.cmd == "all" else [args.cmd]
    for s in stages:
        print(f"== {s} ({args.area})")
        _stage(s)(cfg, args.area, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
