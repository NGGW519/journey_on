"""범위 개요도: 음영기복 + 수면 + 건물 + 타일 격자 + 주요 지점 → reports/overview.png (검수·공유용)."""
from __future__ import annotations

from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib import font_manager
from pyproj import Transformer

from .config import area_cfg, area_paths
from .grid import area_bounds, area_tiles
from .heightmap import hillshade
from .vectors import load_layer
from .zones import no_build_polygons

LANDMARKS = [  # WGS84 (lon, lat) — OSM 노드·폴리곤 중심에서 확인(2026-11-17)
    ("가평역", 127.51076, 37.81452),
    ("자라섬 서도(오토캠핑장)", 127.52060, 37.81950),
    ("자라섬 중도·남도", 127.52872, 37.81402),
    ("남이섬 선착장(가평 측)", 127.52572, 37.80335),
    ("남이섬(원경)", 127.52555, 37.79178),
    ("가평군청", 127.50973, 37.83125),
]


def _korean_font():
    for name in ("Malgun Gothic", "NanumGothic", "AppleGothic"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def run(cfg: dict[str, Any], area: str, args=None) -> None:
    _korean_font()
    a = area_cfg(cfg, area)
    ap = area_paths(cfg, area).ensure()
    minx, miny, maxx, maxy = area_bounds(cfg, area)
    fig, ax = plt.subplots(figsize=(8, 8 * (maxy - miny) / (maxx - minx) + 0.6), dpi=150)
    dem = ap.interim / "dem_samples.tif"
    if dem.exists():
        with rasterio.open(dem) as src:
            step = 4
            z = src.read(1)[::step, ::step]
        hs = hillshade(z, src.res[0] * step)
        ax.imshow(hs, cmap="gray", extent=(minx, maxx, miny, maxy), alpha=0.9)
        cs = ax.contour(np.linspace(minx, maxx, z.shape[1]), np.linspace(maxy, miny, z.shape[0]), z,
                        levels=np.arange(0, 1000, 50), colors="saddlebrown", linewidths=0.3, alpha=0.5)
        ax.clabel(cs, fontsize=4, fmt="%d")
    water = load_layer(cfg, area, "water_areas")
    if water is not None:
        water[water.geom_type.isin(["Polygon", "MultiPolygon"])].plot(ax=ax, color="#3d7ab8", alpha=0.6, linewidth=0)
    bld = load_layer(cfg, area, "buildings", margin_m=0)
    if bld is not None:
        bld.plot(ax=ax, color="#d9534f", linewidth=0)
    for t in area_tiles(cfg, area):
        x0, y0, x1, y1 = t.bounds
        ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], color="k", lw=0.4, alpha=0.6)
        ax.text(x0 + 30, y1 - 30, t.name.replace("Tile_", ""), fontsize=5, va="top", alpha=0.7)
    for _, zg in no_build_polygons(cfg, area):
        for g in getattr(zg, "geoms", [zg]):
            xs, ys = g.exterior.xy
            ax.plot(xs, ys, color="orange", lw=1, ls="--")
    tr = Transformer.from_crs("EPSG:4326", cfg["crs"], always_xy=True)
    for name, lon, lat in LANDMARKS:
        x, y = tr.transform(lon, lat)
        ax.plot(x, y, "o", ms=4, color="yellow", mec="k", mew=0.5)
        ax.annotate(name, (x, y), xytext=(4, 4), textcoords="offset points", fontsize=7,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7, lw=0))
    o = cfg["origin"]
    ax.plot(o["easting"], o["northing"], "+", ms=12, color="magenta", mew=1.5)
    ax.annotate("Unity 원점", (o["easting"], o["northing"]), xytext=(6, -10), textcoords="offset points",
                fontsize=7, color="magenta")
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("equal")
    ax.set_title(f"{a.get('title', area)}\nEPSG:5186 · 1km 타일 · 빨강=건물, 파랑=수면, 주황 점선=건물 금지 구역", fontsize=8)
    ax.tick_params(labelsize=6)
    fig.tight_layout()
    out = ap.reports / "overview.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  개요도 → {out}")
