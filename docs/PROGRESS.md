# 진행 상황

기준 일정: 기획서 「1차 가시화(5일, Nov 22, 2026까지)」. 오케스트레이터(`journey-on-orchestrator`)가 이 파일을 읽고 갱신한다.

## 사용자 조치 필요

| 항목 | 이유 | 안내 |
| --- | --- | --- |
| 국토정보플랫폼 가입·로그인, DEM·정사영상·수치지도 신청 | 공식 지형·영상 원천 | `docs/DATA_ACQUISITION.md` 1·2·4절 |
| 국토정보플랫폼에서 공개 DEM 해상도 확인(5m 여부) | 5m가 공개제한일 수 있음 → D-008 | `docs/decisions.md` D-008 |
| 브이월드 가입, GIS건물통합정보(경기도) SHP 다운로드, API 키 | 공식 건물 원천 | `docs/DATA_ACQUISITION.md` 3절 |
| ~~Unity 로그인 + Personal 라이선스~~ | 완료(11/17) | — |
| 빌드 직접 조작 검수(`dist/JourneyOn_20261118/JourneyOn.exe`) | 자동 스모크로는 조작감·시점·화면 품질을 판단할 수 없음 | `docs/qa/playtest_checklist.md` |

## 1차 가시화(5일)

### 1일차 (11/17) — 데이터 수집, 좌표계 정리
- [x] 하네스 구성(에이전트 4, 스킬 5, 약관 가드 훅), 개발 환경(Python GIS, QGIS, Unity CLI·에디터, .NET)
- [x] 좌표 규약 확정: EPSG:5186, 원점, 1km 격자, 1단계 범위 28타일 (D-001~D-005)
- [x] 공식 데이터 확보 절차 조사 → `docs/DATA_ACQUISITION.md`, `docs/DATA_SOURCES.md`
- [x] 공개 대체 데이터 수집(Copernicus DEM, OSM 건물·수면·도로·철도·섬·경계) — interim
- [ ] 공식 DEM·정사영상·건물통합정보·수치지도 다운로드 — **사용자 조치 대기**
- [x] QGIS 프로젝트에 원천 겹쳐 보기 (`qgis/phase1_sources.qgz`, 레이어 9개) + 개요도 `data/out/phase1/reports/overview.png`

### 2일차 — 지형 + 정사영상 → Unity Terrain
- [x] (선행) 파이프라인 dem 단계: 모자이크·하상 깎기(물가 연속 경사)·타일 RAW (interim DEM)
- [x] Unity 프로젝트 생성(Unity 6.3 LTS 6000.3.28f1, URP 17.3, glTFast 6.21.0, Input System)
- [x] WorldImporter: Terrain 28타일 배치, 이웃 연결, 정사영상 없음 → 생성 지면 텍스처
- [x] 스크린샷 검수 5시점(`_workspace/screens/`) — 물가 계단 절벽, 지평선 갈색 띠 수정
- [x] 독립 검증(qa-inspector) 2차 FAIL(스폰이 물속) → 수정: 육지 스폰(D-010), 수면 피처별 분류·깎기 제한(D-007), DSM 건물 평탄화(D-009), 물가 벽 타일 경계 제외, 비행 범위 제한, 크레딧 문구, validate 보강
- [ ] 정사영상 적용 — 공식 정사영상 도착 대기

### 3일차 — 건물 압출(용도별 재질), 북한강 수면
- [x] (선행) 파이프라인 buildings·water 단계 (interim OSM, 건물 1038동, 수면체 7개)
- [x] Unity: glb 임포트, 용도별 재질 5종(단색 URP Lit), 수면 재질, 메타 1038/1038 연결
- [x] axis_test로 축 검증 (D-005) — Unity에서 동쪽 팔 +X 100m 확인
- [x] 용도별 외벽·지붕 재질: 코드 생성 텍스처 + 파이프라인이 굽는 층 정렬 UV(트라이플래너 대신, D-011). 1038동 UV 검사 PASS
- [x] (선행) 도로 지면 마스크(정사영상 대체, D-012) 27타일, 교량 상판 메시 49개(난간, 상판 밑 지형 낮춤)

### 4일차 — 자유 이동, 충돌·경계, 하늘·조명, 남이섬 원경
- [x] (선행) 보행 컨트롤러(WASD·Shift·Space, F 비행), Terrain·건물 충돌, 범위 벽, 물가 벽
- [x] (선행) 하늘(Procedural)·태양·안개, 건물 클릭 조회 HUD, 출처 표시
- [x] 비행 모드 범위 제한·물 위 착지 금지(수면 Water 레이어, 물가 벽 Ignore Raycast)
- [x] 교량 통행: 물가 벽을 상판 밑면에 맞춰 낮춤(여유 3.5 m), 전 교량 진입 검사 육지 끝 78/82 통과(막힌 4곳은 OSM 데이터 문제), 자라섬 보행교·작은 다리 걷기 스모크 도착
- [x] 물가 벽을 보이는 물가선으로: 수면 메시를 '지형 − 수위 < 0.4 m'로 잘라 묻힌 수면 옆 투명 벽 제거(D-007), 착지 허용치도 0.4 m로 맞춤
- [ ] 플레이 모드 실제 조작 검증(사람) — `docs/qa/playtest_checklist.md`
- [x] 범위 밖 원경 스커트 12 km(Copernicus 60/120 m, 범위 밖 북한강 수면, 범위 안 지면 색조에 맞춤, D-013)

### 5일차 — Windows 빌드, 시연 자료
- [x] (선행) Windows 시험 빌드 `dist/JourneyOn_20261117/JourneyOn.exe`(203MB, 오류 0, 헤드리스 15초 실행 예외 0), HUD 크레딧
- [x] 빌드 `dist/JourneyOn_20261118/JourneyOn.exe`(322 MB, 오류 0, DoNotShip 폴더 분리), 스모크 5종(정지 60초 평균 약 160 fps, 육지·강·보행교·작은 다리 걷기)
- [x] 시연 영상 `dist/demo_20261118/JourneyOn_phase1_demo.mp4`(1920×1080, 30fps, 75초, 출처 자막), 검수 스크린샷 `_workspace/screens/05c_phase1_*`
- [ ] 실제 화면 조작 검수(사람) 후 배포본 정리

## 알려진 문제
- interim DEM이 Copernicus 30m DSM이다. 지형이 뭉툭하고 수목 높이가 들어 있다. 물가 급경사 표본이 약 4%, 평탄화 구덩이 벽 약 1.6만 표본, 묻힌 건물 7~10동이다. 공식 DEM이 오면 교체한다(D-008, D-009). (남이섬 빈 구덩이는 11/17 수정, QA 4차 확인)
- OSM 건물은 층수 정보가 99% 없어 기본값 1층이다. 용도 '기타'는 38%다. 공식 건물통합정보를 받으면 교체한다.
- 나란한 다리 끝 단: 경강교 상·하행 최대 4.4 m, 경춘선 가평교 두 선로 최대 6.3 m. 끝을 각자 지형에 맞추기 때문이다. 접속 도로 지형 돋우기는 공식 DEM·수치지도 도로가 오면 다룬다.
- 사그막천 상부 물줄기(수면체 9)의 약 72%가 수목 DSM 언덕 아래에 묻혀 있다(interim).
- OSM 교량 데이터 한계: w445330583은 양 끝이 물 위라 걸어서 갈 수 없다. 육지 끝 82곳 중 4곳이 높이 단·벽으로 막힌다.
- 물가 0~0.4 m 띠 위 보행로 일부(약 220 m, 최장 자라섬 연못가 E246497 N579524 51 m)가 물가 벽 고리 안에 남는다. 50 m² 미만 수면 조각 정리로 보이던 웅덩이 약 23 m²가 빠졌다(E244322.7 N580336.3).
- (Unity 다음 작업) 교량 진입 검사 `cause` 문구: 끝이 물속이거나 단 때문에 막힌 경우를 'rule'이 아니라 'data'로 적게 고친다(예: BR_w445330583:end).
- 정사영상이 없어 지면은 생성 텍스처다(기획서 대비책).
- 빌드할 때마다 `*_BurstDebugInformation_DoNotShip` 폴더를 `_workspace/burst_debug/`로 옮긴다(배포 제외).

## 작업 기록

| 날짜 | 한 일 | 산출물 | 판정 |
| --- | --- | --- | --- |
| 2026-11-17 | 하네스·환경 구성, 데이터 절차 조사, interim 데이터로 파이프라인 실행, Unity 프로젝트·임포트·시험 빌드 | `.claude/`, `pipeline/`, `docs/`, `qgis/`, `data/out/phase1/`, `unity/JourneyOn/`, `dist/` | QA 2차 FAIL(스폰) → 수정 → 3차 WARN(FAIL 0, `_workspace/03_qa-inspector_verdict.md`) |
| 2026-11-17 | QA 3차 지적 반영: 수면체 이음 평활·다리 상판 최소값 필터, 남이섬 평탄화 제외, 비행 착지 판정, 단위 테스트 18개, validate 수면 계단 검사 | `pipeline/dem.py`, `tests/test_terrain_water.py`, Unity 스크립트 | validate WARN(FAIL 0, 수면 계단 PASS) — 4차 독립 검증은 다음 세션 |
| 2026-11-18 | QA 4차 독립 검증: 합류부 계단·남이섬 구덩이·비행 착지 고쳐짐 확인. 남은 것은 다리 둔덕·달전천 역경사 1.52 m, 최소값 필터 과적용, 서도 서쪽 수로 분류, 착지 판정 첫 충돌체 문제, validate·테스트 보강, 문서 | `_workspace/04_qa-inspector_verdict.md` | WARN(FAIL 0) → 수정 착수(05a 파이프라인·Unity 병렬) |
| 2026-11-18 | QA 4차 지적 수정(다리 깎기·하류 단조·수로 분류·이음 범위·착지 판정), manifest v2(외벽 UV·도로 마스크·교량 49·원경 스커트), Unity 재질·교량·원경·스모크·영상 도구, 최종 임포트·영상·빌드 | `pipeline/{bridges,roads,skirt,terrain}.py`, `docs/decisions.md` D-011~013, `dist/JourneyOn_20261118/`, `dist/demo_20261118/`, `_workspace/05*_report.md` | validate FAIL 0(PASS 67·WARN 17), pytest 62, 임포트 경고 0, 스모크 5/5 → 5차 독립 검증 중 |
| 2026-11-18 | QA 5차 지적 수정: 수면 메시를 보이는 수면으로 잘라 물가 벽을 물가선으로(투명 벽 23.6 km 대리 지표 → 694 m, 재현 지점 0 m), 접속 길 없는 교량 끝 고정, 폭 이상값 거르기, manifest `water`, Unity 진입 검사 보강·EditMode 테스트 27개·착지 허용치 0.4 m, 최종 재임포트·영상·빌드·스모크 a~e, 계약·스킬 문구 정리 | `pipeline/water.py`, `tests/test_water_clip.py`, `docs/qa/playtest_checklist.md`, `_workspace/06*_report.md` | validate FAIL 0(PASS 73·WARN 18), pytest 71, EditMode 27, QA 6차 WARN(FAIL 0, `_workspace/06_qa-inspector_verdict.md`) |
