---
name: unity-world
description: "가평읍 3D 프로젝트의 Unity 6 LTS(URP) 쪽 규약과 작업법. manifest를 읽어 World/Tile_E_N/{Terrain,Buildings,Roads,Water}를 자동 배치하는 에디터 스크립트, Terrain 높이맵·정사영상·도로 마스크 적용, glTFast 건물·교량·원경 임포트, 용도별 외벽·지붕 재질(코드 생성 텍스처), 보행 컨트롤러·충돌·보이지 않는 경계, 하늘·조명·안개, 건물·교량 클릭 조회, 배치 모드 임포트·검수 스크린샷·시연 영상(ffmpeg)·스모크 실행과 Windows 빌드를 만들거나 고칠 때 사용한다. Unity CLI 자체 사용법은 Unity 플러그인의 unity-cli 스킬을 함께 본다."
---

# Unity 월드 조립 규약

## 프로젝트

| 항목 | 값 |
| --- | --- |
| 경로 | `unity/JourneyOn` |
| 에디터 | Unity 6.3 LTS (`6000.3.28f1`) — `unity` CLI(`%LOCALAPPDATA%\Unity\bin\unity.exe`)로 설치·실행 |
| 렌더 파이프라인 | URP |
| 패키지 | `com.unity.cloud.gltfast`(건물·수면 glb), `com.unity.inputsystem`(조작), `com.unity.pipeline`(CLI 연결). 2단계부터 `com.unity.addressables` |
| 단위 | 1m = 1 unit |
| 대상 | Windows 64-bit 스탠드얼론 (Mono) |

## 좌표·축

- X = 동, Z = 북, Y = 해발 표고. 타일 `Tile_E_N` 오브젝트 위치 = manifest의 `unity_position`.
- Terrain: 타일 오브젝트 아래 자식, 로컬 위치 (0, `base_m`, 0), `size = (1000, range_m, 1000)`, `heightmapResolution = 1025`.
- RAW 읽기: uint16 LE, 첫 행 = 남쪽. `heights[z, x] = raw[z*1025 + x] / 65535f`. RAW 임포트 대화상자의 "Flip Vertically"에 기대지 말고 코드로 읽는다.
- glb: glTFast가 X를 뒤집으므로 파이프라인이 미리 −X로 썼다. 추가 회전을 넣지 않는다. 첫 임포트 때 `axis_test.glb`의 긴 팔이 +X(동쪽)를 향하는지 확인한다.
- 원점에서 수 km 이내(1단계)는 원점 고정으로 충분하다. 2단계에서 떨림이 보이면 floating origin을 넣는다.

## 씬 구조

```
World
 ├─ Tile_245_579
 │   ├─ Terrain      (Terrain + TerrainCollider, 레이어 [ground|ortho, 아스팔트, 보도, 자갈] ← roads_mask.png)
 │   ├─ Buildings    (B_<id>: MeshRenderer[벽, 지붕] + MeshCollider + BuildingInfo)
 │   ├─ Bridges      (BR_<osm_id>: kind별 재질 + MeshCollider + BridgeInfo, 레이어 0)
 │   └─ Water        (W_<n>: 수면 재질·레이어 4 / ShoreWall: 레이어 2 보이지 않는 벽)
 └─ Skirt            (SK_<i>: 원경 지형, SKW_<i>: 원경 수면. 월드 좌표, 충돌체 없음, 그림자 받기만)
Player (CharacterController + WalkController + HudOverlay + SmokeProbe, 자식 PlayerCamera)
Bounds (구현 범위 밖 보이지 않는 벽)
Sun (Directional Light), Skybox = Materials/Sky.mat, 지수 안개
```

manifest v1·v2를 모두 읽는다. v2 필드(`facade`, `skirt`, tile.json `roads`·`bridges`)가 없거나 `null`이면 그 부분만 v1처럼 건너뛴다.

## 코드 위치

| 파일 | 내용 |
| --- | --- |
| `Assets/Scripts/Editor/WorldImporter.cs` | 메뉴 `JourneyOn/Import Area phase1` + 배치 모드 진입점 `JourneyOn.Editor.WorldImporter.ImportFromCommandLine` (`-area phase1 -dataRoot <path>`). Terrain(+도로 마스크 C2)·건물(외벽·지붕 재질 C1)·교량(C3)·수면·물가 벽·원경(C4)·범위 벽·플레이어·환경을 만들고, `data/out/<area>/reports/unity_import.json`에 수량·축·`facade_uv_check`·교량·원경·far clip·안개 값을 쓴다. `FarClip(m)`, `FogDensity(m)`, `GroundHeight(...)`는 캡처 도구도 쓴다 |
| `Assets/Scripts/Editor/ManifestModels.cs` | manifest·tile.json·buildings.json·unity_import.json 모델(JsonUtility). v2: `FacadeSpec`, `SkirtSpec`, `RoadsInfo`, `BridgesInfo`, `FacadeUvCheck` |
| `Assets/Scripts/Editor/MiniJson.cs` | JsonUtility가 못 읽는 중첩 배열(`footprint_xz`) 읽기 |
| `Assets/Scripts/Editor/ProceduralTextures.cs` | 외부 이미지 없이 코드로 만드는 텍스처·재질: 외벽(창 한 칸 × 한 층, 비율 = `facade.bay_m / floor_height_m`)·노멀맵·지붕(`roof_tile_m`), 도로 TerrainLayer 3종, 교량 재질 3종 → `Assets/World/Generated/{Facades,Roads,TerrainLayers}` |
| `Assets/Scripts/Editor/ShoreRules.cs` | 물가 벽 변 규칙. 순수 함수라 EditMode 테스트를 붙일 수 있다. 다루는 것: 타일 경계 변 제외, 교량 여유, footprint 링에서 양 끝 찾기(`TryRingEnds`), 끝 구역 선분 교차(`FindEndZone`), 구역 판정(`Zone`: None/Side/End), 연결부(`AttachedTo`), 단 한계(`IsStepTooHigh`), 윗면 결정(`BridgeWallTop`) |
| `Assets/Scripts/Editor/Tests/ShoreRulesTests.cs`, `BridgeAccessTests.cs` | EditMode 테스트(NUnit). `ShoreRules` 순수 함수와 `WorldImporter.CheckBridgeAccess`(합성 충돌체) |
| `Assets/Scripts/Editor/BuildScript.cs` | `JourneyOn.Editor.BuildScript.BuildWindows` (`-out <exe>`) |
| `Assets/Scripts/Editor/CaptureCommon.cs` | 캡처 공통(씬 열기, EPSG:5186 → Unity, 지면 높이, URP 렌더 요청 → Texture2D, 셰이더 동기 컴파일) |
| `Assets/Scripts/Editor/ShotCapture.cs` | 검수 시점 스크린샷 `JourneyOn.Editor.ShotCapture.CaptureFromCommandLine` (`-area`, `-outDir`, `-prefix`, `-views a,b`) → `_workspace/screens/<prefix>_<view>.png`. 기본은 원거리 5장 + 근접 5장이다. 보조 시점(`-views`로만 찍힘): `roadmask_topdown`(도로 마스크 방향, 화면 위 = 북), `gapyeonggyo_rail_w/e`, `gyeonggang_bridge`, `river_north_edge`, `river_south_edge`, `wall_check_sagmak`, `wall_check_dalcheon`. 진단: `-showShoreWalls`(보이지 않는 물가 벽을 반투명 빨강으로, 저장 안 함), `-basemapDistance` |
| `Assets/Scripts/Editor/DemoCapture.cs` | 시연 영상 `JourneyOn.Editor.DemoCapture.CaptureFromCommandLine`. 키프레임(EPSG:5186 + 표면 위 높이 + 바라볼 점) Catmull-Rom 보간(축별 넘침 방지), 표면 + 최소 여유 보장, 1920×1080 30fps PNG → ffmpeg H.264(+출처 자막). `-frames n` 미리보기, `-path keys.json` 경로 교체 |
| `Assets/Scripts/Runtime/BuildingInfo.cs` | 건물 속성(용도·층수·주소), `IPickInfo` |
| `Assets/Scripts/Runtime/BridgeInfo.cs` | 교량 속성(이름·종류·길이·폭·상판 높이), `IPickInfo` 인터페이스 정의 |
| `Assets/Scripts/Runtime/WalkController.cs` | 보행 이동(WASD·마우스·Shift·Space), F 비행 모드(시연용). 착지 판정 `LandingAllowed(origin, self, tolerance)`(높이 비교): 일반 레이어(4·2 제외) 첫 지면 g, 수면 레이어만의 w. w가 없으면 허용, g가 교량이면 허용, g가 건물이면 막음(수상 건물 지붕), 아니면 w < g − tolerance일 때만 허용. tolerance = `buriedWaterTolerance` = manifest `water.shore_clip_above_m`(0.4, 없으면 0.3, 임포터가 채움) |
| `Assets/Scripts/Runtime/HudOverlay.cs` | 조작 안내, 클릭 건물·교량 조회(수면 통과), 출처 표시(크레딧) |
| `Assets/Scripts/Runtime/SmokeProbe.cs` | `-smoke <초>`일 때만 동작. 5초마다 `[Smoke]` 프레임 수·fps·위치·발밑 충돌체(`on=`), `landing=OK|BLOCKED`(같은 착지 판정). `-smokeStart E,N`: 그 지점 최상단 지면(교량 포함, 수면 제외) 위로 옮겨 시작. `-smokeWalk "E,N;E,N;…"`: 경유점을 차례로 걷는다(꺾인 보행교용, 중간 통과 반경 0.6 m, 끝 1.5 m). 경유점 통과, 밟은 교량(`교량 위: BR_…`), 도착, 막힘(`lastHit=<충돌체>(layer N)`)을 남기고, 시간이 되면 `Application.Quit` |
| `Assets/Materials/Buildings/<cls>.mat`, `roof_<cls>.mat`, `flat_<cls>.mat` | 외벽(텍스처) / 지붕 / UV 없는 v1 건물용 단색 |
| `Assets/Materials/Bridges/bridge_{road,foot,rail}.mat`, `Skirt.mat`, `SkirtVertexColor.mat`, `Water.mat`, `Sky.mat` | 교량 3종, 원경(단색 / COLOR_0 있으면 glTF 셰이더 정점색), 수면, 하늘 |

glb 노드 이름: glTFast(편집 시 임포트)는 루트 노드가 하나뿐인 glb에서 그 노드를 프리팹 루트로 쓰고 이름을 에셋 이름(`bridges` 등)으로 바꾼다. 임포터는 이때 이름을 바꾸지 않고 감싸며(`Buildings`/`Bridges`/`Water` 래퍼), 노드 이름은 메시 이름(파이프라인은 메시 이름 = 노드 이름)으로 되찾는다(05b에서 교량 3개 메타 누락으로 발견).

원경 색(05b-나 결정): 원경 정점색을 범위 안 지면 색조로 다시 칠한다. 메시 사본은 `Assets/World/Tiles/<area>/skirt_<SK_i>_recolor.asset`이다. 새 색 = 지면 평균 알베도 × clamp(1 + 0.35·(휘도 ÷ 경계 1.5 km 평균 휘도 − 1), 0.5, 1.6)이다. 지면 평균 알베도는 레이어 텍스처 평균을 알파맵 덮임으로 가중한 값이다(phase1 선형 (0.167, 0.192, 0.093)). basemap 거리(1500 m) 안팎으로 먼 거리 색이 바뀌지 않음을 확인했다. `SkirtVertexColor.mat`의 `baseColorFactor`는 흰색이다. 원본 색은 언제나 `skirt.glb`에서 읽으므로 여러 번 돌려도 누적되지 않는다. 값은 `unity_import.json`의 `ground_albedo_linear`, `ground_layer_coverage`, `skirt_source_mean_linear`, `skirt_recolor_target_linear`, `skirt_recolor_variation`에 남긴다.

재질·생성 텍스처·TerrainLayer는 **임포트(와 RefreshLook)마다 코드 값으로 덮어쓴다**(05b-나 결정). 에셋을 지우지 않고 값만 바꾸므로 GUID와 씬 참조는 유지된다. 손 조정은 코드(`ProceduralTextures.cs`, `WorldImporter.Ensure*`)에 넣는다. 아스팔트는 밝은 회색이다(sRGB 평균 0.41).

재질 배정: 건물 submesh 2개 = [벽 `<cls>.mat`, 지붕 `roof_<cls>.mat`], 1개 = 벽 재질만(UV가 없으면 `flat_<cls>.mat`). 교량은 tile.json `kind`(없으면 glb 재질 이름 `M_bridge_<kind>`, 그래도 없으면 road). 원경 `SKW_*`는 `Water.mat`.

경계 처리: 범위 밖은 `Bounds`의 BoxCollider 4개로 막는다. 수면 진입은 수면 메시 바깥 테두리를 따라 만든 `ShoreWall` MeshCollider로 막는다. 테두리 높이는 수면 −1m부터 +4m까지다. 타일 경계에서 잘린 변은 만들지 않는다.

교량 변(계약 C3, 2차-라):
- 여유 = `water.shore_overlap_m` + 0.5 = 3.5 m다. 값은 manifest `water.shore_overlap_m` 키(있으면 값이 0이어도 그것) → `config/project.yaml` water 블록 → 기본 3.0 순서로 찾고, `unity_import.json`의 `bridge_margin_m`·`bridge_margin_source`에 남긴다. manifest `water.shore_clip_above_m`은 착지 허용치(`WalkController.buriedWaterTolerance`)로 쓰고, 값을 `water_shore_clip_above_m`에 남긴다.
- 모든 타일의 교량 충돌체를 먼저 만든 뒤 판정한다. 대상 변은 중점이 상판 외곽(`footprint_xz`) + 여유 안이거나, 선분이 끝 구역을 지나는 변이다.
  1. **변 위에 상판이 있으면**(변 위 5점 위쪽 레이): 윗면 = 그 자리 상판 밑면 − 0.1 m.
  2. **상판 밖이고 끝 구역을 지나면**: 윗면 = 그 끝 단면 밑면 − 0.1 m. 육지에서 다리로 오르는 길이다.
     - 끝 구역: 끝 단면 앞, 바깥 방향 −0.5 ~ 여유 + 0.5 m, 옆으로 상판 폭/2.
     - 양 끝은 footprint 링 구조로 찾는다: 왼쪽 선 앞으로 n점 + 오른쪽 선 뒤로 n점이라, 끝 단면은 (n−1→n)과 (2n−1→0) 변이다. 단면 길이가 폭 ±35% 안일 때만 쓴다.
  3. **상판 밖이고 옆면 바깥이면**: 기본 높이(수면 + 4 m)를 유지한다. 다리 옆으로 하상에 내려가는 틈을 막는다.
- 밑면은 위쪽 레이캐스트로 구한다. 실패하면 위에서 찾은 상판 윗면 − `deck_thickness_m`(없으면 1 m)을 쓴다.
- 낮춘 윗면이 수면 + 0.3 m 이하이면 그 변만 뺀다. 상판을 못 찾으면 기본 높이로 두고 `shore_edges_bridge_unresolved`로 센다.
- 개수 기록: `shore_edges_bridge_{near, lowered, removed, high, end, side_kept, unresolved, thickness_fallback}`.

교량 끝 진입 검사(`bridge_access`, 3차-가 보강):
- 모든 교량 끝에서 '육지 5 m 밖 → 상판 1 m 안'을 0.25 m 간격으로 따라간다.
- 접근 방향은 끝 축과 ±30° 셋이다. 하나라도 바깥 점이 땅이면 육지 끝이고, 그중 하나라도 통과하면 통과다. 셋 다 땅이 아니면 축 방향 분류를 따른다.
- 먼저 분류하고, 다음 끝은 육지가 아니라서 통계에서 뺀다.
  - **joint(연결부):** 끝점이 다른 footprint 1 m 안(사슬 이음, 다른 다리에 붙은 계단 끝)이거나 바깥 점이 다른 상판 위
  - **building:** 바깥 점이 건물(지붕) 위
  - **water:** 바깥 점이 물 위(`LandingAllowed` 거짓)
- 육지 끝만 막힘을 센다.
  - **wall:** 지면 위 0.5~1.5 m, 반지름 0.3 m 캡슐이 물가 벽(레이어 2)과 겹치거나 부딪힘
  - **step:** 이웃 표본 지면 높이 차 > stepOffset(0.5 m, `WorldImporter.PlayerStepOffset` = Player CharacterController)
- 기록: `blocked[]`(node, which, kind, step_m, 막은 지점, 상판까지 거리, cause), `ends_blocked_wall`·`ends_blocked_step`, `joint_ends`·`building_ends`·`water_ends`.

EditMode 테스트(`Assets/Scripts/Editor/Tests/`, Assembly-CSharp-Editor 안, asmdef 없이 발견됨):
- `ShoreRulesTests`: 타일 경계 변, `TryRingEnds`, 끝 구역 선분 교차, 옆면 유지, 윗면 결정, 연결부, 단 한계, YAML·manifest 여유 출처
- `BridgeAccessTests`: 합성 충돌체로 wall·step·building·joint·water 분류, ±30° 접근
- `LandingTests`: 0.3~0.4 m 띠(허용치 = 자르기 기준), 수면 위 지붕 막기, 상판 허용
- 실행: `"<Editor>/Unity.exe" -batchmode -projectPath … -runTests -testPlatform EditMode -testResults <xml> -logFile <log>`(`-quit` 없이). 06 3차-나: 27/27 통과. 남이섬은 사방이 물이라 들어갈 수 없다. 비행 중 F 착지는 높이 비교로 정한다. 수면이 없거나 첫 지면보다 0.3 m 넘게 아래(지형에 묻힌 수면)면 허용하고, 그 밖에는 첫 지면이 교량 상판일 때만 허용한다.

렌더 값: Terrain `heightmapPixelError = 1`(교량 끝 ±0.3 m·도로 마스크와 LOD 어긋남 방지), 지면 타일 25 m. far clip = 원경이 있으면 `margin + max(4000, 범위 반대각선)`(phase1 약 16,030 m), 없으면 플레이어 9,000 m·캡처 12,000 m. 안개 밀도 = 원경이 있으면 `2.3 / (margin + 반대각선)`(phase1 약 0.000143), 없으면 0.00018.

임포터는 타일 에셋을 `Assets/World/Tiles/<area>/`(git 제외)에 만든다. 생성 텍스처는 `Assets/World/Generated/`(git 포함). 씬은 `Assets/Scenes/<area>.unity`.

## 명령

```bash
unity --no-banner status --project-path unity/JourneyOn
# 배치 모드 임포트(에디터가 닫혀 있을 때). 메서드가 EditorApplication.Exit 로 끝내므로 -quit 를 붙이지 않는다
"<Editor>/Unity.exe" -batchmode -projectPath <절대경로>/unity/JourneyOn \
  -executeMethod JourneyOn.Editor.WorldImporter.ImportFromCommandLine \
  -area phase1 -logFile <절대경로>/_workspace/logs/NN_import.log
# 컴파일만 확인(씬·데이터를 건드리지 않는다). 로그의 "error CS" 가 0 이어야 한다
"<Editor>/Unity.exe" -batchmode -quit -projectPath <절대경로>/unity/JourneyOn -logFile <절대경로>/_workspace/logs/NN_compile.log
# 겉모습만 다시 맞추기(data/out 타일을 읽지 않음, 파이프라인이 다시 쓰는 중이어도 됨): 생성 재질·텍스처·원경 정점색 → 씬 저장
"<Editor>/Unity.exe" -batchmode -projectPath ... -executeMethod JourneyOn.Editor.WorldImporter.RefreshLookFromCommandLine -area phase1 -logFile ...
# 검수 스크린샷(-nographics 를 붙이면 렌더링되지 않는다). -views 로 일부만, -prefix 로 파일 접두사, -basemapDistance 로 진단(저장 안 함). manifest 읽기는 실패하면 10초 간격으로 다시 시도
"<Editor>/Unity.exe" -batchmode -projectPath ... -executeMethod JourneyOn.Editor.ShotCapture.CaptureFromCommandLine -area phase1 -logFile ...
# 시연 영상(렌더링 필요, -nographics 금지). 먼저 -frames 90 으로 미리보기
"<Editor>/Unity.exe" -batchmode -projectPath ... -executeMethod JourneyOn.Editor.DemoCapture.CaptureFromCommandLine -area phase1 \
  [-frames 90] [-out <mp4>] [-path <keys.json>] -logFile ...
# 빌드
"<Editor>/Unity.exe" -batchmode -projectPath ... -executeMethod JourneyOn.Editor.BuildScript.BuildWindows -logFile ...
# 스모크(빌드한 플레이어, 창 모드로 실제 렌더링). 끝나면 스스로 종료한다
dist/JourneyOn_<date>/JourneyOn.exe -screen-width 1600 -screen-height 900 -smoke 40 [-smokeWalk 246300,578400] -logFile <로그>
```

스모크 로그 판정:

- 기본 줄: `[Smoke] 시작` 1줄, 5초마다 `[Smoke] t=… frames=… fps=…` 줄, `[Smoke] 종료: … frames=… avgFps=…` 1줄.
- 걷기 시험: `walk: traveled=… wp=i/n remain=…`, `도착` 또는 `막힘: … lastHit=<충돌체>(layer N)`로 판정한다. 물가 벽이 막으면 layer 2다. 종료 줄의 `bridges=[…]`는 밟은 교량이다.
- 교량 건너기 예(05c): 시작·끝은 `reports/bridges.json`의 `ends[].e/n`에서 `out` 방향으로 4 m 밖이고, 경유점은 OSM 중심선 꼭짓점이다.
- 05c의 짧은 다리 막힘(여유 1.5 m < 수면 확장 3 m)은 2차-라에서 해소했다. 여유 3.5 m + 끝 구역 선분 교차, 스모크 e 도착.

`<Editor>` = `C:/Program Files/Unity/Hub/Editor/6000.3.28f1/Editor`(다른 버전이면 `unity --no-banner editors --format json`으로 찾는다). 열린 에디터가 있으면 배치 모드 대신 `unity command`로 같은 메서드를 실행한다(Unity 플러그인 `unity-cli` 스킬).

## 라이선스

배치 모드도 활성화된 라이선스가 필요하다. `unity license status`가 비활성이면 멈추고 사용자에게 로그인·Personal 라이선스 동의를 묻는다. 대신 동의하지 않는다.

## 검증 포인트(qa-inspector에게 넘길 것)

- 가평역(Unity X −1026, Z −465)에서 동쪽을 본 스크린샷, 자라섬 상공 부감(원점 부근), 남이섬 선착장(X 298, Z −1697)에서 남쪽을 본 스크린샷
- 근접 5장(목표에서 약 80 m, 지면 위 35~38 m, 방위 = 목표에서 눈 쪽): `dalcheon_bridge` E245325 N579843 방위 240°, `main_junction` E245997 N580258 160°, `west_tributary` E244955 N579826 110°, `sagmak_upper` E244372 N580271 240°, `jarasum_footbridge` E245768 N579938 244°
- 콘솔 오류 0건, manifest 타일 수 = 씬 타일 수, buildings.json 수 = 씬 건물 수
- v2: `bridge_access.ends_blocked` = 0(막히면 `blocked[].cause`가 data/rule로 나뉜다)
- v2: `unity_import.json`의 `facade_uv_check.status` = PASS(기준 v 범위 [0, height_m ÷ floor_height_m(용도별)]), `bridge_nodes` = `bridge_items`, 교량이 있으면 `shore_edges_bridge_near` > 0이고 `shore_edges_bridge_unresolved` = 0(낮춘 변 `_lowered`, 뺀 변 `_removed`, 기본 높이 유지 `_high`, 두께 대비 사용 `_thickness_fallback`), `roads_tiles` = 마스크 있는 타일 수, `skirt_nodes` > 0, `camera_far_clip` ≥ margin + 4000
