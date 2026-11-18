"""QGIS 프로젝트 생성: 원천(interim/공식)과 파이프라인 중간 산출물을 EPSG:5186 으로 겹쳐 본다.

실행: "C:/Program Files/QGIS 3.44.15/bin/python-qgis-ltr.bat" scripts/make_qgis_project.py phase1
QGIS 는 눈으로 확인하는 용도만(기획서). 변환은 pipeline 이 한다.
"""
import json
import sys
from pathlib import Path

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFillSymbol,
                       QgsLineSymbol, QgsProject, QgsRasterLayer, QgsVectorLayer,
                       QgsHillshadeRenderer, QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
                       QgsTextFormat)
from qgis.PyQt.QtGui import QColor

ROOT = Path(__file__).resolve().parent.parent
area = sys.argv[1] if len(sys.argv) > 1 else "phase1"

app = QgsApplication([], False)
app.initQgis()
proj = QgsProject.instance()
proj.setCrs(QgsCoordinateReferenceSystem("EPSG:5186"))
proj.setFileName(str(ROOT / "qgis" / f"{area}_sources.qgz"))
proj.writeEntryBool("Paths", "/Absolute", False)

manifest = json.loads((ROOT / "data/raw/manifest.json").read_text(encoding="utf-8"))
added = []


def add_vec(path, name, style):
    lyr = QgsVectorLayer(str(path), name, "ogr")
    if not lyr.isValid():
        print("  ! 실패", path); return None
    style(lyr)
    proj.addMapLayer(lyr)
    added.append(name)
    return lyr


def fill(color, outline="#00000000", alpha=1.0):
    def f(lyr):
        s = QgsFillSymbol.createSimple({"color": color, "outline_color": outline, "outline_width": "0.2"})
        s.setOpacity(alpha)
        lyr.renderer().setSymbol(s)
    return f


def line(color, width):
    def f(lyr):
        lyr.renderer().setSymbol(QgsLineSymbol.createSimple({"color": color, "width": str(width)}))
    return f


# 1) DEM 음영(파이프라인 모자이크) + 원천 DEM
dem = ROOT / f"data/interim/{area}/dem_samples.tif"
if dem.exists():
    r = QgsRasterLayer(str(dem), "DEM 모자이크(표본 격자, 하상 깎음)")
    r.setRenderer(QgsHillshadeRenderer(r.dataProvider(), 1, 315, 45))
    proj.addMapLayer(r); added.append(r.name())
for e in manifest["layers"].get("ortho", []):
    r = QgsRasterLayer(str(ROOT / e["path"]), f"정사영상 {Path(e['path']).name}")
    if r.isValid():
        proj.addMapLayer(r); added.append(r.name())

# 2) 벡터 원천(공식이 있으면 공식, 없으면 interim)
STYLE = {
    "water_areas": ("수면", fill("#3d7ab8", alpha=0.6)),
    "islands": ("섬", fill("#00000000", "#2e7d32")),
    "waterways": ("하천선", line("#1f4e79", 0.4)),
    "roads": ("도로", line("#f0ad4e", 0.3)),
    "railways": ("철도(경춘선)", line("#333333", 0.6)),
    "buildings": ("건물", fill("#d9534f", "#7a1f1d")),
    "boundaries": ("행정경계", fill("#00000000", "#8e44ad")),
}
for layer, (label, style) in STYLE.items():
    entries = manifest["layers"].get(layer, [])
    official = [e for e in entries if not e.get("interim")]
    for e in (official or entries):
        tag = "공식" if not e.get("interim") else "interim·OSM"
        add_vec(ROOT / e["path"], f"{label} ({tag})", style)

# 3) 타일 격자 + 라벨
g = add_vec(ROOT / f"data/interim/{area}/grid.geojson", "타일 격자 1km", fill("#00000000", "#000000"))
if g:
    pal = QgsPalLayerSettings(); pal.fieldName = "name"
    fmt = QgsTextFormat(); fmt.setSize(7); pal.setFormat(fmt)
    g.setLabeling(QgsVectorLayerSimpleLabeling(pal)); g.setLabelsEnabled(True)

# 레이어 순서: 위에서부터 격자, 건물, ..., DEM
root = proj.layerTreeRoot()
order = [l for l in proj.mapLayers().values()]
order.sort(key=lambda l: 0 if "격자" in l.name() else 1 if "건물" in l.name() else 5 if "DEM" in l.name() or "정사" in l.name() else 3)
root.setHasCustomLayerOrder(True)
root.setCustomLayerOrder(order)
proj.write()
print(f"QGIS 프로젝트 → {proj.fileName()}  레이어 {len(added)}개: {added}")
app.exitQgis()
