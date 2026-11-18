"""원천 파일 요약: 좌표계, 범위, 개수, 열 이름(공식 데이터 필드 매핑 확인용)."""
from __future__ import annotations

from pathlib import Path

from .config import ROOT


def inspect(path: str) -> None:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    suf = p.suffix.lower()
    if suf in (".tif", ".tiff", ".img", ".asc", ".dem", ".jp2"):
        import rasterio
        with rasterio.open(p) as src:
            print(f"래스터 {p.name}: {src.width}x{src.height}, 밴드 {src.count}, {src.dtypes[0]}")
            print(f"  CRS: {src.crs}")
            print(f"  범위: {src.bounds}")
            print(f"  해상도: {src.res}, nodata: {src.nodata}")
    else:
        import pyogrio
        info = pyogrio.read_info(p)
        print(f"벡터 {p.name}: {info['features']}개, 형상 {info['geometry_type']}")
        print(f"  CRS: {info['crs']}")
        print(f"  범위: {info.get('total_bounds')}")
        print(f"  열: {list(info['fields'])}")
        gdf = pyogrio.read_dataframe(p, max_features=3)
        print(gdf.drop(columns="geometry").head(3).to_string())
