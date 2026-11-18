"""공개 대체 데이터(interim) 받기: Copernicus GLO-30 DEM, OpenStreetMap(Overpass).

공식 데이터(국토정보플랫폼·브이월드)가 오기 전에 파이프라인을 끝까지 돌려 보기 위한 것이다.
모두 manifest 에 interim=true 로 등록되고, 공식 데이터를 register 하면 자동으로 밀려난다.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import requests
from pyproj import Transformer
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import linemerge, polygonize, unary_union

from . import sources
from .config import ROOT
from .grid import area_bounds

UA = {"User-Agent": "journey-on-pipeline/0.1 (public-data GIS pipeline)"}
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

COP_ATTR = (  # docs/DATA_SOURCES.md 「빌드 크레딧 문구」. 접두 문구는 라이선스 원문 확인 필요
    "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provi"
    "ded under COPERNICUS by the European Union and ESA; all rights reserved"
)
COP_LICENSE = "Copernicus DEM GLO-30 licence (무료, 재배포·상업 이용 가능, 출처 표시 필수)"
OSM_ATTR = "© OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)"
OSM_LICENSE = "ODbL 1.0 (출처 표시, 파생 DB 동일조건)"


def wgs84_bbox(cfg: dict[str, Any], area: str, margin_m: float = 300.0) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = area_bounds(cfg, area)
    t = Transformer.from_crs(cfg["crs"], "EPSG:4326", always_xy=True)
    xs, ys = [], []
    for x in (minx - margin_m, maxx + margin_m):
        for y in (miny - margin_m, maxy + margin_m):
            lon, lat = t.transform(x, y)
            xs.append(lon)
            ys.append(lat)
    return min(xs), min(ys), max(xs), max(ys)  # lon_min, lat_min, lon_max, lat_max


# --------------------------------------------------------------------------- DEM
def fetch_copernicus(cfg: dict[str, Any], area: str, force: bool = False) -> list[Path]:
    lon0, lat0, lon1, lat1 = wgs84_bbox(cfg, area, margin_m=500)
    out_dir = ROOT / cfg["paths"]["raw"] / "open" / "dem"
    out_dir.mkdir(parents=True, exist_ok=True)
    got = []
    for lat in range(math.floor(lat0), math.floor(lat1) + 1):
        for lon in range(math.floor(lon0), math.floor(lon1) + 1):
            ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
            ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
            name = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
            url = f"https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
            dst = out_dir / f"{name}.tif"
            if force or not dst.exists():
                print(f"  DEM 받는 중: {url}")
                _download(url, dst)
            sources.register(
                cfg, "dem", dst, source="Copernicus DEM GLO-30 (AWS Open Data)",
                license=COP_LICENSE, attribution=COP_ATTR, date="2021 release",
                crs="EPSG:4326 (EGM2008 높이)", interim=True, kind="copernicus_glo30", url=url,
                notes="DSM(수목·건물 포함), 30m. 공식 5m DEM 도착 시 교체",
            )
            got.append(dst)
    return got


def _download(url: str, dst: Path) -> None:
    tmp = dst.with_suffix(dst.suffix + ".part")
    with requests.get(url, headers=UA, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(dst)


# --------------------------------------------------------------------------- OSM
OSM_QUERIES = {
    "buildings": '(way["building"]({bb});relation["building"]({bb}););',
    "water_areas": (
        '(way["natural"="water"]({bb});relation["natural"="water"]({bb});'
        'way["waterway"="riverbank"]({bb});relation["waterway"="riverbank"]({bb});'
        'way["landuse"="reservoir"]({bb});relation["landuse"="reservoir"]({bb}););'
    ),
    "waterways": '(way["waterway"~"^(river|stream|canal)$"]({bb}););',
    "islands": '(way["place"~"^(island|islet)$"]({bb});relation["place"~"^(island|islet)$"]({bb}););',
    "roads": '(way["highway"]({bb}););',
    "railways": '(way["railway"~"^(rail|light_rail|subway)$"]({bb}););',
    "boundaries": '(relation["boundary"="administrative"]["admin_level"~"^(6|7|8|9|10)$"]({bb}););',
}
AREA_LAYERS = {"buildings", "water_areas", "islands", "boundaries", "skirt_water_areas"}
# 원경 스커트(C4) 범위의 수면 폴리곤만. 기본 목록(OSM_QUERIES)에 넣지 않고 --layers skirt_water_areas 로 따로 받는다.
# bbox = 범위 + skirt.margin_m + 500 m. 실패하면 원경은 수면 없이 만든다.
SKIRT_QUERIES = {
    "skirt_water_areas": (
        '(way["natural"="water"]({bb});relation["natural"="water"]({bb});'
        'way["waterway"="riverbank"]({bb});relation["waterway"="riverbank"]({bb});'
        'way["landuse"="reservoir"]({bb});relation["landuse"="reservoir"]({bb}););'
    ),
}


def overpass(query: str) -> dict[str, Any]:
    last = None
    for url in OVERPASS:
        for attempt in range(2):
            try:
                r = requests.post(url, data={"data": query}, headers=UA, timeout=(15, 240))
                if r.status_code == 200:
                    return r.json()
                last = f"{url} HTTP {r.status_code}: {r.text[:200]}"
            except requests.RequestException as e:  # 네트워크 오류는 한 번 더
                last = f"{url} {e}"
            print(f"    재시도: {last}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Overpass 실패: {last}")


def _ring_polys(lines: list[LineString]) -> list[Polygon]:
    if not lines:
        return []
    merged = linemerge(lines)
    return [p for p in polygonize(merged) if p.is_valid or p.buffer(0).is_valid]


def osm_to_features(data: dict[str, Any], want_area: bool) -> list[dict[str, Any]]:
    feats = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        geom = None
        if el["type"] == "way" and "geometry" in el:
            coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if want_area:
                if len(coords) >= 4 and coords[0] == coords[-1]:
                    geom = Polygon(coords)
            elif len(coords) >= 2:
                geom = LineString(coords)
        elif el["type"] == "relation" and want_area:
            outer, inner = [], []
            for mem in el.get("members", []):
                if mem.get("type") != "way" or "geometry" not in mem:
                    continue
                ls = LineString([(p["lon"], p["lat"]) for p in mem["geometry"]])
                (inner if mem.get("role") == "inner" else outer).append(ls)
            outs = _ring_polys(outer)
            if outs:
                geom = unary_union([p.buffer(0) for p in outs])
                ins = _ring_polys(inner)
                if ins:
                    geom = geom.difference(unary_union([p.buffer(0) for p in ins]))
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        feats.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {"osm_type": el["type"], "osm_id": el["id"], **tags},
        })
    return feats


def fetch_osm(cfg: dict[str, Any], area: str, layers: list[str] | None = None) -> dict[str, int | None]:
    lon0, lat0, lon1, lat1 = wgs84_bbox(cfg, area)
    bb_area = f"{lat0:.6f},{lon0:.6f},{lat1:.6f},{lon1:.6f}"
    sk = float((cfg.get("skirt") or {}).get("margin_m", 12000)) + 500.0
    slon0, slat0, slon1, slat1 = wgs84_bbox(cfg, area, margin_m=sk)
    bb_skirt = f"{slat0:.6f},{slon0:.6f},{slat1:.6f},{slon1:.6f}"
    out_dir = ROOT / cfg["paths"]["raw"] / "open" / "osm" / area
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int | None] = {}
    todo = [(k, v, bb_area) for k, v in OSM_QUERIES.items() if not layers or k in layers]
    todo += [(k, v, bb_skirt) for k, v in SKIRT_QUERIES.items() if layers and k in layers]
    for layer, body, bb in todo:
        q = f"[out:json][timeout:180];{body.format(bb=bb)}out geom;"
        print(f"  OSM {layer} 받는 중…", flush=True)
        try:
            data = overpass(q)
        except RuntimeError as e:  # 한 레이어 실패가 나머지를 막지 않게
            print(f"  ! {layer} 실패: {e}")
            counts[layer] = None
            continue
        feats = osm_to_features(data, layer in AREA_LAYERS)
        dst = out_dir / f"{layer}.geojson"
        with open(dst, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": feats,
                       "crs_note": "EPSG:4326",
                       "osm_base": data.get("osm3s", {}).get("timestamp_osm_base")},
                      f, ensure_ascii=False)
        sources.register(
            cfg, layer, dst, source="OpenStreetMap (Overpass API)", license=OSM_LICENSE,
            attribution=OSM_ATTR, date=data.get("osm3s", {}).get("timestamp_osm_base", ""),
            crs="EPSG:4326", interim=True, kind="osm", url=OVERPASS[0],
            notes=f"bbox {bb}",
        )
        counts[layer] = len(feats)
        time.sleep(2)  # Overpass 예의
    return counts
