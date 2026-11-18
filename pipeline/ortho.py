"""정사영상 단계: 타일 경계로 잘라 ortho_px 정사각 JPEG(위쪽 = 북쪽)로 만든다.

원천이 없으면(기획서 1일차 대비책) 건너뛰고 tile.json 의 ortho 를 null 로 둔다 → Unity 는 단색 재질.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.warp import reproject

from . import sources
from .config import area_cfg, area_paths
from .grid import area_tiles
from .tilejson import update_tile_json


def run(cfg: dict[str, Any], area: str, args=None) -> None:
    a = area_cfg(cfg, area)
    ap = area_paths(cfg, area).ensure()
    tiles = area_tiles(cfg, area)
    entries = sources.select(cfg, "ortho")
    if not entries:
        print("  정사영상 원천이 없습니다 → 건너뜀(Unity 는 단색 재질)")
        for t in tiles:
            update_tile_json(ap.tile_dir(t.name), t, cfg, "ortho", None)
        return
    px = int(a.get("ortho_px", 2048))
    prov = sources.provenance(entries)
    for t in tiles:
        res = t.size / px
        T = Affine(res, 0, t.e0, 0, -res, t.n0 + t.size)  # 픽셀 가장자리 = 타일 경계
        rgb = np.zeros((3, px, px), dtype=np.uint8)
        filled = np.zeros((px, px), dtype=bool)
        for e in entries:
            with rasterio.open(sources.abspath(e)) as src:
                src_crs = src.crs or CRS.from_user_input(e["crs"].split()[0])
                bands = [1, 2, 3] if src.count >= 3 else [1, 1, 1]
                tmp = np.zeros((3, px, px), dtype=np.uint8)
                for k, b in enumerate(bands):
                    reproject(source=rasterio.band(src, b), destination=tmp[k],
                              src_transform=src.transform, src_crs=src_crs, src_nodata=src.nodata,
                              dst_transform=T, dst_crs=cfg["crs"], dst_nodata=0,
                              resampling=Resampling.lanczos)
            m = (tmp.sum(axis=0) > 0) & ~filled
            rgb[:, m] = tmp[:, m]
            filled |= m
        d = ap.tile_dir(t.name)
        if not filled.any():
            update_tile_json(d, t, cfg, "ortho", None)
            continue
        Image.fromarray(np.moveaxis(rgb, 0, -1)).save(d / "ortho.jpg", quality=90)
        update_tile_json(d, t, cfg, "ortho", {
            "file": "ortho.jpg", "px": px, "coverage": round(float(filled.mean()), 4),
            "interim": bool(prov and prov["interim"]),
        })
    print(f"  정사영상 타일 {len(tiles)}개({px}px)")
