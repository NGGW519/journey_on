# 출력 manifest 스키마 (manifest_version 1)

Python 파이프라인과 Unity 임포터 사이의 계약이다. 필드를 바꾸면 `manifest_version`을 올리고 Unity 임포터(`Assets/Scripts/Editor/WorldImporter.cs`)를 같이 고친다.

## `data/out/<area>/manifest.json`

```json
{
  "manifest_version": 1,
  "area": "phase1",
  "title": "1단계 관광지 권역 …",
  "crs": "EPSG:5186",
  "origin": {"easting": 246000, "northing": 580000},
  "tile_size_m": 1000,
  "heightmap": {"resolution": 1025, "base_m": -20.0, "range_m": 1200.0,
                 "byte_order": "little", "row_order": "south_to_north"},
  "gltf_axes": "x=-east,y=up,z=north (glTFast flips x on import)",
  "tiles": [
    {"name": "Tile_245_579", "e0": 245000, "n0": 579000,
     "unity_position": [-1000.0, 0.0, -1000.0],
     "dir": "tiles/Tile_245_579"}
  ],
  "provenance": {"dem": {...}, "ortho": null, "buildings": {...}, "water": {...}},
  "stats": {"tiles": 28, "buildings": 1234, "floors_default_ratio": 0.31},
  "generated_at": "2026-11-17T12:00:00+09:00"
}
```

`unity_position`은 타일 남서 모서리의 Unity 좌표(X=E0−원점E, Y=0, Z=N0−원점N)다. Terrain은 Y = `heightmap.base_m`에 둔다.

## 타일 폴더 `tiles/Tile_E_N/`

| 파일 | 있을 때 | 내용 |
| --- | --- | --- |
| `tile.json` | 항상 | 아래 참조 |
| `heightmap.raw` | dem 완료 | uint16 LE, 1025×1025, 첫 행 = 남쪽 |
| `heightmap_preview.png` | dem 완료 | 8-bit 음영 미리보기(북쪽이 위) |
| `ortho.jpg` | ortho 원천 있음 | 정사영상, 이미지 위쪽 = 북쪽, 픽셀 가장자리 = 타일 경계 |
| `buildings.glb` | 건물 있음 | 노드 `B_<id>` 하나당 건물 하나. 타일 로컬 좌표, glTF 축 규약 |
| `buildings.json` | 건물 있음 | `[{id, node, usage_class, usage_name, floors, floors_source, height_m, base_m, address, area_m2}]` |
| `water.glb` | 수면 있음 | 노드 `W_<n>`. 타일 로컬 좌표. 2026-11-18(06a)부터 넓힌 수면 메시를 '보이는 수면'으로 자른 것이다(아래 v2 `water` 참조). 형식은 같다 |

## `tile.json`

```json
{
  "name": "Tile_245_579", "e0": 245000, "n0": 579000, "size_m": 1000,
  "dem": {"min_m": 31.2, "max_m": 410.5, "nodata_ratio": 0.0},
  "ortho": {"px": 4096, "file": "ortho.jpg"} ,
  "buildings": {"count": 312, "file": "buildings.glb", "meta": "buildings.json"},
  "water": {"count": 1, "level_m": 50.4, "file": "water.glb"},
  "flags": {"no_build_zone": false, "interim": ["dem", "buildings", "water"]}
}
```

없는 레이어는 `null`이다. Unity는 `null`이면 해당 오브젝트를 만들지 않고, ortho가 `null`이면 단색 재질을 쓴다.

---

# manifest_version 2 추가분 (2026-11-18 합의, 도로·교량·원경·외벽)

v1 필드는 그대로 둔다. Unity 임포터는 v1과 v2를 모두 읽어야 한다. 새 필드가 없으면(`null` 또는 키 없음) 해당 오브젝트를 만들지 않는다.

## manifest 최상위 `water` (v2 선택 필드, 2026-11-18 06a 추가)
```json
"water": {"shore_overlap_m": 3.0, "shore_clip_above_m": 0.4}
```
- `config/project.yaml`의 `water` 값을 그대로 싣는다. Unity는 저장소의 config를 직접 읽지 않고 이 값을 쓴다. 키가 없는 이전 manifest에서만 config나 기본값(3.0)으로 돌아간다.
- `shore_overlap_m`: 수면 폴리곤을 넓힌 폭이다. C3 교량 여유(= 이 값 + 0.5 m)에 쓴다.
- `shore_clip_above_m`: 보이는 수면 자르기 기준이다(D-007). 파이프라인은 넓힌 메시를 {최종 지형(RAW 이중선형) − 수면 < 이 값} 영역으로 잘라 `water.glb`로 낸다.
  - Unity는 같은 값을 착지 허용치(`WalkController.buriedWaterTolerance`)로 쓴다. 발밑 수면이 지면보다 이 값 넘게 아래일 때만 육지로 본다. 키가 없으면 0.3이다.
  - 그래서 `water.glb` 경계는 두 종류다. (1) 넓힌 띠 경계: 지형이 낮은 물가로, 이전과 같다. (2) 잘린 경계: 지형 − 수면 ≈ 이 값(validate p1 +0.31, p95 +0.41 m)이다.
  - 잘린 경계에서는 수면 가장자리가 지형 아래 최대 이 값만큼 묻힌다. 그래서 가장자리 틈이 없다.
  - Unity는 지금처럼 메시 경계에 물가 벽을 세운다. 그러면 벽이 보이는 물가선(수면이 지형 위로 보이는 곳에서 이 값 안)에 선다.
  - 수면 메시 안에서 지형이 수면보다 높은 곳은 이 값 안의 물가 띠뿐이다(phase1 0.71%).
- 형식과 노드 이름(`W_<n>`)은 바뀌지 않는다. 자른 결과 비는 노드는 내지 않으므로 번호가 건너뛸 수 있다.

## C1. 건물 외벽 UV (`buildings.glb`)
- **노드:** 건물 하나에 노드 `B_<id>` 하나다(v1과 같다).
- **프리미티브:** 노드의 메시는 프리미티브 2개를 갖는다.
  - `[0]` 벽(모든 외곽선 링의 옆면)
  - `[1]` 지붕(윗면)
  - 바닥면은 넣지 않는다.
  - 두 프리미티브는 정점 속성 구성이 **똑같아야** 한다(POSITION, NORMAL, TEXCOORD_0만 쓰고 한쪽에만 COLOR_0 같은 속성을 넣지 않는다). 다르면 glTFast가 submesh로 묶지 않고 자식 오브젝트로 나눈다.
  - 평면 음영이다(면마다 정점을 따로 둔다).
  - trimesh 기본 export는 노드 하나에 프리미티브 하나만 쓰므로, 이 형식은 pygltflib 등으로 직접 쓴다.
- **glb 재질:** `M_<cls>_wall`, `M_roof`. Unity는 이 재질을 무시하고 submesh 순서대로 바꾼다.
  - `[0]` → `Buildings/<cls>.mat`
  - `[1]` → `Buildings/roof_<cls>.mat`
- **벽 UV:** Unity에 들어간 뒤의 값 기준이다.
  - u: 변마다 칸 수 n = max(1, round(변 길이 ÷ `facade.bay_m[cls]`))을 정수로 정하고, 그 변에서 u가 U0 → U0+n으로 고르게 늘어난다. U0은 앞 변까지의 n을 누적한 값이다. 그래서 모서리와 링 시작점에서 창이 잘리지 않는다.
  - v = 건물 바닥(`base_m`)에서 잰 높이 ÷ `floor_height_m[cls]`
  - 텍스처 0..1이 정확히 "창 한 칸 × 한 층"이다. v가 정수인 곳이 층 경계이고, 지붕선에서 v = height_m ÷ floor_height_m이다(지금 규칙에서는 floors와 같다).
  - 벽을 base_m 아래로 늘리지 않으므로 v ≥ 0이다.
- **지붕 UV:** u = (E − 건물 최소 E) ÷ `facade.roof_tile_m`, v = (N − 건물 최소 N) ÷ `facade.roof_tile_m`.
- **V 뒤집기:** glTFast는 가져올 때 V를 뒤집는다(unity_v = 1 − gltf_v). 그래서 glb에는 `1 − v`를 쓴다.
  - Unity 임포트 뒤 벽 submesh의 uv.y 범위가 건물마다 [0, height_m ÷ floor_height_m]인지 확인한다.
  - 결과는 `unity_import.json`의 `facade_uv_check`에 기록한다.
  - 어긋나면 파이프라인 쪽에서 고친다.
- **manifest 최상위:** `"facade": {"bay_m": {"residential": 3.0, ...}, "roof_tile_m": 4.0, "floor_height_m": {...}}`. Unity는 텍스처 칸 비율을 정할 때 이 값을 쓴다.

## C2. 도로 지면 마스크 (`roads_mask.png`, 타일마다)
- **형식:** 1024×1024 RGB 8-bit.
  - R = 차도(motorway…service)
  - G = 보행·자전거(footway, path, cycleway, pedestrian, steps, track, bridleway)
  - B = 철도
  - 값은 0~255 덮임 비율이다(안티에일리어싱).
- **방향:** 행 0 = 북쪽 가장자리, 열 0 = 서쪽 가장자리.
  - 픽셀 (c, r)의 중심은 (e0 + (c+0.5)·1000/1024, n0 + 1000 − (r+0.5)·1000/1024)다.
  - Unity alphamap은 [z, x]이고 z=0이 남쪽이므로 행을 뒤집어 넣는다.
- **제외:** 다리 구간(`bridge` 태그, 메시로 따로 만든다)과 터널(`tunnel` 태그)은 마스크에서 뺀다.
- **폭:** 교량과 같은 규칙이다(D-012). `width` 태그 → `lanes` × 3.0 + 1.0 → 등급 기본값 순서로 쓴다. 다만 앞의 둘로 낸 폭이 등급 기본값 × 3(`roads.width_outlier_ratio`)을 넘으면 그 태그는 이상값으로 거르고 다음 규칙으로 넘어간다. 거른 목록은 `reports/width_outliers.csv`에 남는다(06a).
- **tile.json:** `"roads": {"mask": "roads_mask.png", "segments": <int>, "interim": true}`, 없으면 `null`.

## C3. 교량 (`bridges.glb`, 타일마다)
- **배정:** 다리 선의 중심이 든 타일에 통째로 넣는다. 타일 경계에서 자르지 않는다.
- **노드:** OSM way 하나에 노드 `BR_<osm_id>` 하나. 좌표는 타일 로컬이고, glTF 축 규약은 건물과 같다.
  - 메시: 상판 상자(윗면, 옆면, 밑면). 모든 종류(도로·보행·철도) 다리의 양옆에 높이 1.0 m 난간 벽을 둔다. 걸어서 떨어져 하상에 갇히지 않게 하려는 것이다.
  - items에 `deck_thickness_m`를 넣는다.
  - 프리미티브는 하나이고, 재질 이름은 `M_bridge_<kind>`다.
- **높이 규칙:**
  - 양 끝 상판 윗면은 그 자리의 지형 높이(Unity와 같은 RAW 이중선형)와 ±0.3 m 안에서 맞춘다. 그래야 CharacterController가 올라선다.
    - 접속 길이 없는 육지 끝도 끝 지점 지형에 고정한다(06a). 바깥 10 m 지형은 끝 밖 땅의 경사를 재는 데만 쓴다.
    - 물 위 끝(접속 길 없음, 선착장·잔교)은 걸어 들어오는 끝이 아니다. 그 끝은 수면 + 여유 높이다(D-012).
  - 수면 위에서는 상판 윗면 ≥ 수면 + `roads.bridge_clearance_m[kind]`가 목표다. 경사 상한(도로·보행 8%, 철도 3%) 안에서 맞출 수 없으면 경고를 기록한다.
  - 어떤 경우에도 상판 윗면은 수면 + 0.2 m 아래로 내려가지 않는다. 그러다 끝이 지형보다 높아지면 +0.5 m까지 허용한다. Unity CharacterController의 stepOffset은 0.5다.
  - 나란히 붙은 way(상·하행, 선로별 way, 중심선 간격 ≤ 12 m)는 같은 지점에서 상판 높이를 공유한다(둘 중 큰 값).
    - **단, 사슬 끝에서는 이 공유가 성립하지 않는다.** 끝은 각자 접속 지형에 고정되기 때문이다(위 양 끝 규칙).
    - 끝 단면 지형이 다르면, 끝에서 경사 상한으로 오르는 구간에 두 상판 사이 단이 남는다(phase1: 경강교 상·하행 최대 4.35 m, 가평교 두 선로 6.26 m).
    - 공유는 그 구간 바깥(가운데)에서만 보장된다. 끝 단을 없애는 접속부 성토 규칙은 결정 대기다(D-012).
- **tile.json:**
  ```json
  "bridges": {"file": "bridges.glb", "interim": true,
              "items": [{"node": "BR_w123", "osm_id": "w123", "kind": "road|foot|rail", "name": "",
                         "length_m": 0, "width_m": 0, "deck_min_m": 0, "deck_max_m": 0, "deck_thickness_m": 0.8,
                         "footprint_xz": [[x, z], ...]}]}
  ```
  - `footprint_xz`는 **Unity 월드 좌표**(E−원점E, N−원점N)로 쓴 상판 외곽(닫지 않은 링)이다. 점 순서는 왼쪽 가장자리를 시작→끝 방향으로, 이어서 오른쪽 가장자리를 끝→시작 방향으로 쓴다. Unity는 이 순서로 양 끝 변(첫 점·마지막 점 쪽, 왼쪽 끝점·오른쪽 끝점 쪽)을 찾는다.
  - 없으면 `null`이다.
- **Unity 처리:**
  - 모든 타일의 `footprint_xz`를 먼저 모으고 교량(`BR_*`, MeshCollider + `BridgeInfo`)을 모두 만든 뒤 물가 벽을 만든다.
  - **여유** = manifest `water.shore_overlap_m` + 0.5 m. manifest에 키가 없을 때만 `config/project.yaml`의 같은 값을 쓴다. 값과 출처를 `unity_import.json`의 `bridge_margin_m`, `bridge_margin_source`에 남긴다.
  - **끝 단면:** footprint 링(위 점 순서, n점 + n점)의 끝 단면은 (n−1 → n)과 (2n−1 → 0) 변이다. 단면 길이가 `width_m`의 ±35%를 벗어나면 그 교량은 끝을 모르는 것으로 보고 옆면 규칙(막는 쪽)을 쓴다.
  - **물가 벽 변 처리:**
    1. 변 바로 위에 상판이 있으면 윗면을 그 자리 상판 밑면 − 0.1 m로 낮춘다.
    2. 상판 밖이고, 변(선분)이 끝 구역(끝 단면 앞으로 −0.5 ~ 여유 + 0.5 m, 옆으로 상판 폭/2)을 지나면 윗면을 그 끝 단면 밑면 − 0.1 m로 낮춘다. 육지에서 상판 끝으로 오르는 길이다.
    3. 상판 밖이고, 중점이 footprint + 여유 안이지만 끝 구역이 아니면(옆면) 기본 높이(수면 + 4 m)를 유지한다. 다리 옆으로 하상에 내려가는 틈을 막기 위해서다.
    4. 1·2에서 낮춘 윗면이 수면 + 0.3 m 이하이면 그 변은 만들지 않는다.
    - 밑면은 교량 충돌체로 위쪽 레이캐스트해 구한다. 실패하면 위에서 찾은 상판 윗면 − `deck_thickness_m`을 쓴다.
  - **진입 검사:** 교량 끝마다 '육지 5 m 밖 → 상판 1 m 안' 선분을 보행 높이(지면 + 0.5~1.5 m, 반지름 0.3 m)로 훑는다.
    - 접근 방향은 끝 축(0°)과 ±30° 세 가지다. 바깥 점이 하나라도 육지면 육지 끝이고, 그 가운데 하나라도 막힘 없이 지나면 통과다. 셋 다 육지가 아니면 축 방향 바깥 점의 분류(연결부·건물·물)를 쓴다.
    - 물가 벽(레이어 2)에 막히거나, 이웃 지면 높이 차가 stepOffset을 넘으면 막힘이다.
    - 끝점이 다른 상판에 붙은 끝(연결부)과, 바깥 점이 건물 지붕이거나 물 위인 끝은 육지 끝 통계에서 뺀다.
    - 결과는 `unity_import.json`의 `bridge_access`에 남긴다.
  - **한 값으로 묶음:** 상판 끝 허용치(+0.5 m), 진입 검사 단 한계, Player CharacterController `stepOffset`은 모두 0.5 m다. 하나를 바꾸면 셋을 같이 바꾼다.

## C4. 원경 스커트 (`skirt/skirt.glb`, 범위마다 하나)
- **manifest:**
  ```json
  "skirt": {"file": "skirt/skirt.glb", "bounds": [minE, minN, maxE, maxN],   // bounds = 범위(area) 경계. 고리는 bounds ± margin_m
            "margin_m": 12000, "spacing_m": [60, 120], "inner_overlap_m": 60, "interim": true}
  ```
  없으면 `null`이다.
- **노드:** `SK_<i>`로 덩어리를 나누고, 덩어리마다 정점은 65,000개 이하로 한다.
  - 좌표는 **Unity 월드 좌표**(타일 로컬이 아니다)에 glTF 축 규약을 적용한 값이다: x = −(E−원점E), y = 표고, z = N−원점N.
  - COLOR_0(정점색: 표고·경사·수면 색조)는 넣어도 되고 안 넣어도 된다.
  - 범위 밖 수면이 있으면 `SKW_<i>` 노드로 낸다. Unity는 Water.mat을 쓰고 충돌체는 붙이지 않는다.
- **범위:**
  - 범위 경계부터 경계 + margin까지의 고리를 덮는다.
  - 안쪽으로 `inner_overlap_m`만큼 범위 안에 겹치게 들어온다. 겹치는 부분은 범위 지형보다 2 m 이상 아래로 내려 보이지 않게 한다.
  - **단, 경계 안쪽 1 m 경사 띠는 제외한다.** 경계 줄(d = 0)은 Terrain 경계 높이 그대로이고, −1 m 줄부터 2 m 이상 아래다. 그래서 경계 줄과 −1 m 줄 사이 삼각형은 경계에서 0 m → 1 m 안쪽에서 −2 m 이하로 기운다. 이 띠는 지형 위로 솟지 않는다(validate "겹침 띠 표면이 지형 위로 솟지 않음", 0.05 m 기준).
- **Unity 처리:** 충돌체 없음, 그림자 받기만 한다. 카메라 far clip은 margin보다 크게 둔다.

`manifest_version`은 2로 올린다.
