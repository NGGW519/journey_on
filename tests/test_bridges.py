"""다리 레코드와 폭 규칙 고정(bridges.py): width 태그 → lanes×3.0+1.0 → 등급별 기본값(D-012, 2026-11-18 줄임)."""
import math

import geopandas as gpd
import pytest
from shapely.geometry import LineString

import pipeline.bridges as bridges_mod
from pipeline.bridges import bridge_width, is_bridge, load_bridges, parse_width
from pipeline.config import load_config

CFG = load_config()
ROADS = CFG["roads"]


def test_width_rule_order():
    assert bridge_width({"width": "3.5 m", "lanes": "4"}, "primary", ROADS) == (3.5, "width")   # width 우선
    assert (ROADS["lane_m"], ROADS["shoulder_m"]) == (3.0, 1.0)
    assert bridge_width({"width": None, "lanes": "4"}, "primary", ROADS) == (4 * 3.0 + 1.0, "lanes")
    assert bridge_width({"width": "넓음", "lanes": "2"}, "primary", ROADS) == (7.0, "lanes")       # 해석 불가 → 다음 규칙
    assert bridge_width({}, "primary", ROADS) == (ROADS["width_m"]["primary"], "default")
    assert bridge_width({"lanes": float("nan")}, "footway", ROADS) == (ROADS["width_m"]["footway"], "default")
    assert bridge_width({}, "secondary_link", ROADS) == (ROADS["width_m"]["link"], "default")
    assert bridge_width({}, "rail", ROADS) == (ROADS["width_m"]["rail"], "default")
    assert bridge_width({}, "모르는등급", ROADS) == (ROADS["width_m"]["default"], "default")


def test_default_widths_2026_11_18():
    """시험 렌더에서 넓어 보인 도로 폭을 줄인 기본값(D-012). 철도는 OSM 선로마다 way → 단선 5 m(D-007)."""
    W = ROADS["width_m"]
    expect = {"trunk": 10, "primary": 10, "secondary": 8, "tertiary": 7, "residential": 5, "unclassified": 5,
              "living_street": 5, "service": 4, "track": 3, "cycleway": 2.5, "footway": 2, "path": 2,
              "pedestrian": 2, "steps": 2, "rail": 5}
    for cls, w in expect.items():
        assert bridge_width({}, cls, ROADS) == (float(w), "default"), cls
    assert bridge_width({"width": "3", "lanes": "2"}, "secondary", ROADS) == (3.0, "width")       # width 가 먼저
    assert bridge_width({"lanes": "2"}, "secondary", ROADS) == (7.0, "lanes")


def test_parse_width_units():
    assert parse_width("12,5") == 12.5
    assert math.isclose(parse_width("10'"), 3.048)
    assert parse_width("0") is None and parse_width("") is None and parse_width(float("nan")) is None


def test_is_bridge_values():
    assert is_bridge("yes") and is_bridge("viaduct") and is_bridge("boardwalk")
    assert not is_bridge("no") and not is_bridge(None) and not is_bridge(float("nan")) and not is_bridge("")


def test_load_bridges_records(monkeypatch):
    roads = gpd.GeoDataFrame({
        "osm_id": [30, 10, 20, 40, 50],
        "highway": ["primary", "footway", "residential", "cycleway", "secondary"],
        "bridge": ["yes", "yes", "no", "yes", None],
        "lanes": ["2", None, "2", None, "2"],
        "width": [None, None, None, "2", None],
        "name": ["경춘로", None, None, "자전거길", None],
        "bridge:name": ["가평2교", None, None, None, None],
        "layer": ["1", "1", None, None, None],
    }, geometry=[LineString([(0, 0), (0, 100)])] * 5, crs="EPSG:5186")
    rails = gpd.GeoDataFrame({"osm_id": [5], "railway": ["rail"], "bridge": ["viaduct"], "name": ["경춘선"]},
                             geometry=[LineString([(10, 0), (10, 50)])], crs="EPSG:5186")
    layers = {"roads": roads, "railways": rails}
    monkeypatch.setattr(bridges_mod, "load_layer", lambda cfg, area, layer, margin_m=200.0: layers.get(layer))
    out = load_bridges(CFG, "phase1")
    assert [b.osm_id for b in out] == [5, 10, 30, 40]            # bridge=no·없음 제외, osm_id 순
    by = {b.osm_id: b for b in out}
    assert (by[30].kind, by[30].width_m, by[30].width_source, by[30].name) == ("road", 7.0, "lanes", "가평2교")
    assert (by[10].kind, by[10].width_m) == ("foot", ROADS["width_m"]["footway"])
    assert (by[40].kind, by[40].width_m, by[40].width_source) == ("foot", 2.0, "width")
    assert (by[5].kind, by[5].width_m, by[5].name) == ("rail", ROADS["width_m"]["rail"], "경춘선")
    assert by[30].geometry.equals(LineString([(0, 0), (0, 100)]))
    fp = by[30].footprint()
    assert fp.area == pytest.approx(100 * 7.0, rel=1e-6)          # 끝이 평평한 폭 7 m 띠
