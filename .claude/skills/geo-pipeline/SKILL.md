---
name: geo-pipeline
description: "가평읍 3D 프로젝트의 Python 변환 파이프라인(pipeline/ 패키지) 규약과 실행법. EPSG:5186 재투영, 1km 타일 격자, DEM→16-bit RAW 1025 높이맵, 정사영상 타일 텍스처, 건물 외곽선 정리·층수×층고 압출·외벽 UV glTF, 수면 메시, 도로 지면 마스크·교량 상판 메시, 원경 스커트, Unity용 manifest(v2) 출력을 만들거나 고치거나 다시 돌릴 때 사용한다. 원천 데이터를 받는 일은 geodata-sourcing, Unity 쪽 조립은 unity-world 스킬이 맡는다."
---

# 변환 파이프라인 규약

## 왜 규약이 중요한가

Python 쪽과 Unity 쪽은 `data/out/<area>/manifest.json` 하나로만 연결된다. 좌표 원점, 축 방향, 높이맵 인코딩 중 하나라도 어긋나면 지형과 건물이 따로 놀거나 뒤집힌다. 그래서 규약은 `config/project.yaml`과 이 문서에만 두고, 코드에 박지 않는다.

## 좌표 규약

| 항목 | 값 |
| --- | --- |
| 작업 좌표계 | EPSG:5186 (모든 레이어를 여기로 재투영) |
| Unity 원점 | `config.origin` = (E 246000, N 580000), 자라섬 서도 동쪽 끝 |
| Unity 축 | X = E − 원점E, Z = N − 원점N, Y = 해발 표고(원점 빼지 않음) |
| 타일 | 1km 격자. 이름 `Tile_{E km}_{N km}` = 남서 모서리(예: `Tile_245_579`) |
| 타일 로컬 좌표 | 타일 메시 정점은 타일 남서 모서리 기준(값을 작게 유지해 float 떨림 방지). Unity에서 타일 오브젝트를 (E0−원점E, 0, N0−원점N)에 둔다 |

## glTF 축 규약(중요)

glTF는 오른손 좌표계(+Y 위), Unity는 왼손 좌표계다. glTFast는 가져올 때 **X 부호를 뒤집는다**. 그래서 내보낼 때 glTF 정점 = (−로컬E, 표고, 로컬N)으로 쓴다. 그러면 Unity에서 (로컬E, 표고, 로컬N)이 된다. 이 변환은 `pipeline/meshio.py`의 `to_gltf_axes()` 한 곳에서만 한다. `axis_test.glb`(동쪽으로 긴 L자)를 함께 내보내 Unity에서 방향을 검증한다.

## 높이맵 규약

- 1025 × 1025 표본, 표본 간격 = 1000/1024 m. 첫 열 = 타일 서쪽 가장자리, 마지막 열 = 동쪽 가장자리(이웃 타일과 같은 표본을 공유 → 이음매 없음).
- 첫 행 = 남쪽 가장자리(`row_order: south_to_north`). Unity `TerrainData.SetHeights(heights[z, x])`와 같은 순서다.
- uint16 little-endian. 값 = (표고 − `base_m`) / `range_m` × 65535. 모든 타일이 같은 `base_m`, `range_m`을 쓴다. Unity Terrain `size.y = range_m`, 타일 Y 위치 = `base_m`.

## 단계와 명령

```bash
.venv/Scripts/python -m pipeline fetch-open --area phase1   # 공개 대체 데이터(interim)
.venv/Scripts/python -m pipeline fetch-open --area phase1 --only osm --layers skirt_water_areas  # 원경 범위 수면(선택, 따로 지정)
.venv/Scripts/python -m pipeline grid       --area phase1   # 타일 격자 → data/interim/<area>/grid.geojson
.venv/Scripts/python -m pipeline dem        --area phase1   # 타일별 heightmap.raw + 미리보기
.venv/Scripts/python -m pipeline ortho      --area phase1   # 타일별 ortho.jpg (원천 없으면 건너뜀)
.venv/Scripts/python -m pipeline buildings  --area phase1   # 타일별 buildings.glb(벽·지붕 2 프리미티브, UV) + buildings.json
.venv/Scripts/python -m pipeline water      --area phase1   # 타일별 water.glb
.venv/Scripts/python -m pipeline roads      --area phase1   # 타일별 roads_mask.png + bridges.glb, reports/bridges.json
.venv/Scripts/python -m pipeline skirt      --area phase1   # skirt/skirt.glb(원경 SK_·SKW_), reports/skirt.json
.venv/Scripts/python -m pipeline export     --area phase1   # data/out/<area>/manifest.json (manifest_version 2)
.venv/Scripts/python -m pipeline all        --area phase1   # grid→dem→ortho→buildings→water→roads→skirt→export
.venv/Scripts/python -m pipeline overview   --area phase1   # reports/overview.png
.venv/Scripts/python -m pipeline validate   --area phase1 [--stage dem|buildings|water|roads|skirt|export]
.venv/Scripts/python -m pytest -q
```

교량 상판 높이는 dem 단계가 정하고(상판 밑 지형을 낮춘 뒤 RAW 를 쓰려고) `data/interim/<area>/decks.json` 에 남긴다. roads 는 dem(RAW·decks.json)과 water 뒤에, skirt 는 dem 뒤에 돌린다(둘 다 최종 RAW 를 읽는다). 다리·도로 폭이나 교량 규칙을 바꾸면 dem 부터 다시 돌린다. 콘솔 한글이 깨지면 `PYTHONIOENCODING=utf-8`.

같은 명령을 다시 돌리면 같은 결과가 나와야 한다. 원천이 바뀌면 다시 돌리기만 하면 된다.

## 원천 선택

`data/raw/manifest.json`에서 레이어마다 공식(`interim: false`) 항목을 먼저 쓰고, 없을 때만 interim을 쓴다. 쓰인 원천은 타일 `tile.json`과 출력 manifest의 `provenance`에 그대로 복사한다.

## 건물 규칙(기획서 그대로)

1. 외곽선 정리: `make_valid` → 멀티폴리곤 분해 → 면적 `min_area_m2` 미만 제거 → 같은 도형 중복 제거 → 0.05m 단순화.
2. 높이 = 층수 × 층고. 층고는 `config.buildings.floor_height_m`(주거 2.8, 상업·업무 3.5). 층수가 없거나 0이면 1층(`floors_source=default`), `max_floors` 초과면 오류로 보고 기본값 처리.
3. 바닥 높이 = 외곽선 아래 DEM의 최저값(경사지에서 뜨지 않게). 지붕은 바닥 + 높이.
4. 용도 → 재질 5종(`residential/commercial/public/industrial/other`). 매핑은 `pipeline/buildings.py`의 `USAGE_CLASS`.
5. `no_build_zones`(남이섬) 안의 건물은 넣지 않는다.
6. 타일 배정은 외곽선 대표점(representative point) 기준. 노드 이름 `B_<id>`, 속성은 `buildings.json`.
7. 층수 기본값을 쓴 건물은 `data/out/<area>/reports/floors_missing.csv`에 남긴다.

### 외벽 UV(계약 C1, D-011)
- 노드 `B_<id>` 하나에 프리미티브 [0] 벽 `M_<cls>_wall`, [1] 지붕 `M_roof`. 둘 다 POSITION·NORMAL·TEXCOORD_0만(속성이 다르면 glTFast 가 submesh 로 묶지 않는다). 바닥면 없음, 평면 음영.
- 벽 u: 변마다 n = max(1, round(변 길이 ÷ `buildings.facade_bay_m[cls]`)) 정수 칸, 링마다 0부터 누적. 벽 v = (z − base_m) ÷ floor_height_m[cls] → 지붕선 v = height_m ÷ floor_height_m.
- 지붕 u·v = (E·N − 건물 최소 E·N) ÷ `buildings.roof_tile_m`.
- glb 에는 v 대신 1 − v 를 쓴다(`meshio.to_gltf_uv` 한 곳). 쓰기는 `meshio.write_glb_nodes`(pygltflib).

## 수면 규칙

수면 폴리곤(북한강·가평천)을 `shore_overlap_m`만큼 넓혀 지형과의 틈을 덮는다. 평수면 높이는 폴리곤 안 DEM의 `level_percentile` 백분위수다. 경사 수면(하천)은 평활한 높이장을 쓰고, 하류로 갈수록 낮아지게 강제한다. 다리 자리는 수면 높이 계산에서 빼고 보호 없이 깎는다. 양 끝이 같은 평수면에 닿는 곁수로는 그 평수면 높이를 쓴다. 규칙 전체는 `docs/decisions.md` D-007에 있다. 타일 경계로 잘라 타일별 `water.glb`로 내보낸다.

**보이는 수면으로 자르기:** 수면 메시는 (수면 폴리곤 버퍼) ∩ {최종 지형 − 수위 < `water.shore_clip_above_m`(0.4 m)}로 자른다. 지형은 RAW 이중선형이고, 작은 조각과 구멍은 정리한다(`water.clip_to_visible`). Unity는 `water.glb` 경계에 물가 벽을 세우므로, 이렇게 하면 벽이 실제 물가선에 선다. 묻힌 수면 옆 투명 벽을 막기 위해서다. validate가 잘린 경계 틈과 묻힌 수면 옆 벽을 검사한다.

## 도로 마스크·교량 규칙(계약 C2·C3, D-012)
- 마스크 `roads_mask.png`: 1024² RGB, R 차도·G 보행·B 철도, 4×4 부분 표본 덮임 비율. 행 0 = 북쪽. `bridge`·`tunnel` 구간 제외. 폭 = `bridges.bridge_width`(width → lanes × 3.0 + 1.0 → `roads.width_m`, 철도 way당 단선 5 m). `width`로 낸 폭이 등급 기본값의 3배를 넘으면 버리고, 정상인 `lanes`가 있으면 lanes × 3.0 + 1.0을, 없으면 등급 기본값을 쓰며 경고한다. `lanes`로 낸 폭도 같은 기준으로 거른다(`bridges.width_check`, `roads.width_outliers`).
- 교량 `bridges.glb`: way 하나 = `BR_w<osm_id>`, 중심이 든 타일에 통째로. 상판 상자 + 양옆 난간 1.0 m(모든 종류), 두께 `roads.deck_thickness_m`.
- 상판 높이: 끝끼리 이어진 다리 way 는 사슬로 묶는다. 양 끝 = 끝 노드를 공유하는 다리 아닌 길이 있으면 그 노드의 지형(RAW 이중선형), 다른 다리 가운데에 붙으면 그 상판, 없으면 끝 지점 지형에 고정한다(±0.3 m, 수면 바닥 때문이면 최대 +0.5 m). 바깥 10 m 지형은 경사 계산에만 쓴다. 가운데 = max(선형 보간, 수면 + `bridge_clearance_m[kind]`), 경사 ≤ `bridge_max_grade[kind]`, 20 m 다듬기. 못 맞춘 것은 `reports/bridges.json` warnings.
- 수면은 실제 수면 메시(`water.tile_water_meshes` → `surface.TriSurface`)로 잰다. 바닥: 윗면 ≥ 수면 + `bridge_min_water_gap_m`(0.2) 어디서나(경사 상한보다 앞섬), 그 때문에 끝은 지형보다 `bridge_end_raise_max_m`(0.5)까지 올라간다(넘으면 경고).
- 나란한 way(간격 ≤ `bridge_parallel_m` 12 m, 20° 안): 같은 단면에서 큰 높이 공유(`share_decks` 고정점 반복, 끝은 제 지형).
- 상판 밑 지형(`dem.lower_under_decks`): 이중선형 표본 자리 안 지형 ≤ 밑면 − `under_deck_clear_m`(땅에 닿는 끝 `under_deck_end_m` 동안 윗면 − 0.05 m에서 바뀜), 둘레 경사 0.5로 섞음. validate "상판 밑 지형 관통 0"이 FAIL 기준.

## 원경 스커트 규칙(계약 C4, D-013)
- 범위 경계 → 경계 + `skirt.margin_m`(12 km) 고리. 직사각형 줄을 지퍼 삼각분할로 잇는다. 간격 3 km 안 60 m, 밖 120 m.
- 높이 = Copernicus 원천(EGM2008, 간격 맞춤 평활). 경계 줄(약 3.9 m 간격)은 Terrain 경계 높이에 맞추고 보정을 300 m 동안 0 으로 줄인다.
- 범위 안 60 m 겹침: d = −60, −1 m 줄은 붙은 삼각형 범위 최저 지형 − 2 m.
- 덩어리 `SK_<i>` ≤ 65,000 정점(이웃과 줄 하나 공유), COLOR_0 전 덩어리. 범위 밖 수면 `SKW_<i>`(OSM `skirt_water_areas`, 없으면 생략). SKW 삼각형 아래 원경 정점(경계 줄 제외)은 수면 − `skirt.water_drop_m`(2 m) 이하.

## 출력 계약

`references/manifest-schema.md`에 manifest와 `tile.json` 필드가 있다(지금 manifest_version 2: C1 외벽 UV, C2 도로 마스크, C3 교량, C4 원경). 필드를 바꾸면 `manifest_version`을 올리고 `docs/decisions.md`에 적는다. Unity 임포터는 v1·v2를 모두 읽는다.

## 코드 구조

| 파일 | 내용 |
| --- | --- |
| `pipeline/__main__.py` | CLI. `STAGES`(all 실행 순서), `MODULES`(단계→모듈 이름) |
| `pipeline/config.py` | 설정 로드, 경로(`AreaPaths`), JSON 입출력 |
| `pipeline/grid.py` / `grid_stage.py` | 타일 격자·이름·경계 / grid 단계(격자 GeoJSON) |
| `pipeline/sources.py` | 원천 manifest 읽기·등록, 원천 선택(공식 우선), provenance. 레이어 이름 `LAYERS`(원경 수면 `skirt_water_areas` 포함) |
| `pipeline/fetch_open.py` | Copernicus DEM, OSM Overpass(미러 3곳, 레이어별 실패 격리). `SKIRT_QUERIES`: 원경 범위 bbox(범위 + margin + 500 m) 수면, `--layers skirt_water_areas` 로만 받는다 |
| `pipeline/vectors.py` | 벡터 레이어 → EPSG:5186 GeoDataFrame |
| `pipeline/heightmap.py` | 표본 격자·타일 창·RAW 인코딩(순수 함수) |
| `pipeline/dem.py` | DEM 모자이크, DSM 건물 평탄화, 수면 높이장(`water_surface`: 분류·곁수로·다리 제외 평활·경계 띠 이음·하류 단조), 하상 깎기(다리 자리 보호 해제), 교량 상판 높이(`_deck_terrain` → `roads.plan_decks`, `decks.json`)·상판 밑 지형(`lower_under_decks`, `deck_allowance`, `bilinear_support`, `window_min`), 타일 RAW, `water_body_id.tif`·`bridges.geojson` |
| `pipeline/bridges.py` | 다리 레코드(`load_bridges`): OSM roads·railways의 bridge≠no way → LineString(5186), kind(road/foot/rail), 이름, 폭(width → lanes×3.0+1.0 → `roads.width_m`). 상판 순수 함수: `deck_profile`(끝 높이·수면 여유·수면 바닥·공유 높이·경사 상한·다듬기), `profile_breakpoints`(DP), `deck_mesh`(상자 + 난간, 삼각형마다 감김), `offset_lines`(마이터), `footprint_ring` |
| `pipeline/terrain.py` | 최종 지형 표본 `TerrainSampler`(타일 RAW 이중선형, 월드 좌표, Unity Terrain 과 같은 보간; `from_mosaic` = RAW 쓰기 전 모자이크를 같은 양자화로), `raw_bilinear`, `raw_mosaic`, `quantize` |
| `pipeline/surface.py` | `TriSurface`: 삼각형 묶음 높이 표본(수면 메시·SKW·교량 윗면, 칸 색인, max/min) |
| `pipeline/roads.py` | roads 단계: 마스크(`mask_features`, `mask_tile`, `road_channel`, `pixel_center`/`world_to_pixel`), 다리 사슬·끝 분류(`vertex_index`, `bridge_chains`), 상판 높이(`plan_decks` ← dem 단계, `compute_decks`, `parallel_partners`·`share_decks`), `save_decks`/`load_decks`(decks.json), `bridges.glb`·tile.json `roads`/`bridges`·`reports/bridges.json` |
| `pipeline/skirt.py` | skirt 단계: 고리 줄(`ring_offsets`, `loop_points`, `zip_loops`), 겹침 띠 높이(`band_heights`), 덩어리(`chunk_loops`), 정점색, Copernicus 표본(`CopernicusSampler`), 범위 밖 수면(`OuterWater`, `water_mesh`, `drop_under_water`), `reports/skirt.json` |
| `pipeline/ortho.py` | 정사영상 타일 |
| `pipeline/zones.py` | 건물 금지 구역 폴리곤(섬 이름 → islands 레이어) |
| `pipeline/buildings.py` | 외곽선 정리, 층수·용도, 벽·지붕 프리미티브(`building_primitives`, `wall_bays`), `facade_spec`(manifest `facade`), 누락 목록 |
| `pipeline/water.py` | 수면 메시(보조점 Delaunay, 높이장에 얹음, `tile_water_meshes`), 보이는 수면 자르기(`clip_to_visible`, `clip_triangles`, `below_region`, `clean_region`), 수면 표본기(`water_surface_from_field` = dem 단계, `water_surface_from_tiles` = water.glb) |
| `pipeline/meshio.py` | 축 변환(`to_gltf_axes`), UV V 뒤집기(`to_gltf_uv`), trimesh 쓰기(`write_glb`: water·axis_test), pygltflib 쓰기(`write_glb_nodes`: 노드당 프리미티브 여러 개, TEXCOORD_0·COLOR_0)·읽기(`read_glb_nodes`), axis_test |
| `pipeline/tilejson.py` | tile.json 갱신(기본 키에 `roads`·`bridges` null) |
| `pipeline/export.py` | 출력 manifest(v2: `facade`, `skirt`, stats `bridges`·`roads_tiles`) |
| `pipeline/validate.py` | 자동 검사. v2: 외벽 UV(`FacadeCheck`), 도로 마스크(덮임 비율·행 방향·가평역 앞 지점), 교량(노드 = items, 양 끝 −0.3~+0.5 m, 수면 + 0.2 m 바닥(water.glb), 수면 위 여유, 경사, 상판 밑 지형 관통 0·밑면 − 0.3 m, 나란한 way 높이 공유, 서로 겹침 — glb 윗면을 `TriSurface`(min)로 직접 잼), 원경(정점 수·겹침 띠·경계 높이 차·범위 밖 수면·SKW 아래 2 m), 수면 메시(`check_water_mesh`: 잘린 경계 틈, 묻힌 수면 옆 벽, 넓이), manifest v2 필드(`water`, provenance `waterways`) |
| `pipeline/overview.py` | 개요도 PNG(검수·공유용) |
| `pipeline/inspect_src.py` | 원천 파일 요약(공식 데이터 필드 확인) |
| `scripts/make_qgis_project.py` | QGIS 프로젝트(`qgis/<area>_sources.qgz`) 생성 — QGIS 파이썬으로 실행 |

새 단계를 넣으면 `pipeline/__main__.py`의 `STAGES`에 등록하고, `validate.py`에 검사를 추가하고, 이 표를 고친다.

## 공식 건물 데이터 필드

GIS건물통합정보 열 코드(A0..An)는 아직 확정하지 못했다(`_workspace/01_geodata-collector_report.md`). 받으면 `python -m pipeline inspect --path <shp>`로 열 이름을 보고 컬럼 정의서와 대조한다. 그다음 `pipeline/buildings.py`의 `OFFICIAL_FIELDS` 후보 목록 맨 앞에 실제 열 이름을 넣는다.
