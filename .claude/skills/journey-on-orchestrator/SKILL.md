---
name: journey-on-orchestrator
description: "가평읍 3D 가상공간 프로젝트(journey_on)의 작업을 조율한다. 데이터 수집, 지형·정사영상·건물·수면 변환, Unity 배치·컨트롤러·빌드, 검증을 일차·주차 계획(docs/PROGRESS.md)에 따라 에이전트에게 나눠 맡긴다. '다음 작업 진행해', 'N일차 작업', '1단계 데모', '파이프라인 돌려', '타일 다시 만들어', '건물만 다시', 'Unity에 배치', '빌드해', '진행 상황', '검수해' 같은 요청과, 이전 결과 수정·부분 재실행·업데이트·보완·다시 실행 요청에 사용한다. 단순한 질문(좌표계 설명 등)에는 직접 답한다."
---

# journey_on 오케스트레이터

기획서(`가평읍 3D 가상공간 구현 기획서.md`)의 일정을 `docs/PROGRESS.md`로 추적하며, 단계마다 생산 에이전트와 검증 에이전트를 짝지어 실행한다.

## 실행 모드: 혼합

| 단계 | 실행 모드 | 선택 이유 |
| --- | --- | --- |
| 0. 현황 파악·계획 | 메인 에이전트 직접 | PROGRESS.md와 manifest만 읽으면 된다 |
| 1. 생산(수집·변환·Unity) | 서브에이전트 위임 | 단계별로 결과를 한 번 받으면 된다. 서로 독립인 작업(예: 데이터 조사 ↔ Unity 셋업)은 한 메시지에서 병렬 호출 |
| 2. 검증 | 서브에이전트 위임(생성·검증 짝) | 모듈이 끝날 때마다 `qa-inspector`가 독립 판정 |
| 3. 수정 루프 | 서브에이전트 재호출 | FAIL 항목만 담당자에게 되돌린다. 최대 2회 |
| (선택) 대량 타일 검수 | 워크플로 조율 | 2·3단계에서 타일 수십 개를 육안 검수할 때만. 사용자가 명시적으로 요청한 경우에만 Workflow를 쓴다 |

## 에이전트 구성

| 역할 | `subagent_type` | 스킬 | 주 산출물 |
| --- | --- | --- | --- |
| 수집 | `geodata-collector` | `geodata-sourcing` | `data/raw/manifest.json`, `docs/DATA_SOURCES.md` |
| 변환 | `gis-pipeline-engineer` | `geo-pipeline` | `pipeline/*.py`, `data/out/<area>/` |
| Unity | `unity-world-builder` | `unity-world` (+ Unity 플러그인 `unity:unity-cli`) | `unity/JourneyOn/`, `dist/` |
| 검증 | `qa-inspector` | `qa-checks` | `_workspace/*_qa-inspector_verdict.md`, `docs/qa/` |

사용자 정의 유형이 세션에 아직 등록되지 않았으면(하네스를 만든 직후 세션) `general-purpose`로 호출하고, 프롬프트 첫 줄에 "`.claude/agents/<name>.md`를 읽고 그 역할과 원칙을 따르라"고 적는다.

## 작업 절차

### 0단계: 현황 파악(후속 요청 처리 포함)

1. `docs/PROGRESS.md`에서 현재 일차·주차와 미완료 항목을 읽는다.
2. `data/raw/manifest.json`, `data/out/<area>/manifest.json`이 있는지 보고 어디까지 만들어졌는지 확인한다.
3. 요청 유형을 고른다.
   - **다음 작업**: PROGRESS.md의 첫 미완료 항목부터 진행한다.
   - **부분 재실행**(예: "건물만 다시"): 해당 단계와 그 뒤의 의존 단계만 다시 실행한다. 의존 순서: `fetch → grid → dem → ortho → buildings/water/roads → export → unity import → build`.
   - **공식 데이터 도착**(interim 교체): `geodata-collector`로 manifest를 갱신하고 `dem`부터 다시 실행한다. 기존 `data/out/<area>`는 `data/out/<area>_interim_<날짜>/`로 옮겨 둔다.
4. 막힌 항목(사용자 조치 필요: 로그인, 라이선스, API 키)이 있으면 먼저 사용자에게 알리고, 막히지 않은 작업부터 진행한다.

### 1단계: 생산

**실행 모드:** 서브에이전트 위임

에이전트를 호출할 때 프롬프트에 다음을 넣는다: 범위(`--area`), 이번 목표(PROGRESS.md 항목), 입력 파일 경로, 출력 보고서 경로 `_workspace/{NN}_{agent}_report.md`(NN = 일차 두 자리, 예: `02`).

- 서로 독립인 작업은 한 메시지에서 병렬 호출한다(예: 2일차에 `gis-pipeline-engineer`의 DEM 변환과 `unity-world-builder`의 프로젝트 셋업).
- 의존 관계가 있으면 앞 결과를 기다린다. 메인 에이전트는 맡긴 일을 직접 다시 하지 않는다.
- 파이프라인 출력 형식이 바뀌는 작업을 Unity와 병렬로 맡길 때는 먼저 `geo-pipeline/references/manifest-schema.md`에 계약(파일, 필드, 좌표계, 방향, 검사 방법)을 적고, 양쪽 프롬프트가 그 절을 가리키게 한다. 계약 의견은 각자 보고서에 적게 하고, 메인이 계약을 고친 뒤 양쪽에 알린다.
- `data/out/<area>`를 다시 쓰는 동안 Unity 임포트·스크린샷을 돌리지 않는다. Unity 쪽은 그동안 코드 작성과 컴파일 확인만 한다. 새 형식은 시험 임포트(스크린샷·영상 미리보기) → 보완 → 최종 임포트·영상·빌드·스모크 순서로 간다.

### 2단계: 검증

**실행 모드:** 서브에이전트 위임(생성·검증 짝)

생산 단계 하나가 끝날 때마다 `qa-inspector`를 호출한다. 입력: 생산자의 보고서 경로, 검증 범위. 출력: `_workspace/{NN}_qa-inspector_verdict.md`.

### 3단계: 수정 루프

- verdict가 FAIL이면 FAIL 항목만 담당 에이전트에게 되돌린다. 같은 항목이 2회 연속 FAIL이면 멈추고 사용자에게 보고한다.
- WARN은 PROGRESS.md의 "알려진 문제"에 옮기고 진행한다.

### 4단계: 기록과 보고

1. `docs/PROGRESS.md`의 체크박스와 "작업 기록"을 갱신한다(날짜, 한 일, 산출물 경로, 판정).
2. 규약을 바꿨으면 `docs/decisions.md`에 결정을 추가한다.
3. 사용자에게 보이는 결과(스크린샷 경로, 빌드 경로)와 사용자 조치 필요 항목을 짧게 보고한다.

## 데이터 전달

| 경로 | 쓰는 쪽 → 읽는 쪽 | 내용 |
| --- | --- | --- |
| `data/raw/manifest.json` | collector → engineer | 레이어별 원천 파일, 출처, interim 여부 |
| `data/out/<area>/manifest.json` | engineer → unity, qa | 타일 목록, 파일, 원점, 높이맵 인코딩, provenance |
| `_workspace/{NN}_{agent}_report.md` | 생산자 → 메인, qa | 실행한 명령, 통계, 남은 문제 |
| `_workspace/{NN}_qa-inspector_verdict.md` | qa → 메인 | 항목별 판정 |
| `_workspace/screens/` | unity → qa | 검수용 스크린샷 |

`_workspace/`는 지우지 않는다(사후 검증용). 최종 산출물은 `data/out/`, `unity/`, `dist/`, `docs/`에만 둔다.

## 오류 처리

| 상황 | 대응 |
| --- | --- |
| 에이전트 실패 | 한 번 다시 시도한다. 또 실패하면 누락을 PROGRESS.md에 적고 의존하지 않는 작업을 계속한다 |
| 로그인·승인·API 키·Unity 라이선스 필요 | 다시 시도하지 않는다. 사용자에게 할 일을 정확히 알린다(`docs/DATA_ACQUISITION.md` 참조). 그동안 interim 데이터로 진행한다 |
| 사용량 한도·권한 거부 | 다시 시도하지 않는다. 부분 산출물을 직접 열어 어디까지 됐는지 확인하고 기록한 뒤 보고한다 |
| 데이터 충돌(공식 ↔ OSM) | 지우지 않고 출처와 함께 남긴다. 차이 목록은 `docs/qa/`로 |
| 정사영상 지연(기획서 1일차 대비책) | 지형은 단색 재질로 진행하고, 영상이 오면 `ortho`부터 다시 실행한다 |
| 에이전트가 남긴 빈칸 | 메인 에이전트가 직접 확인한 사실만 채운다. 에이전트의 판단은 추측해 채우지 않는다 |

## 테스트 시나리오

### 정상 흐름 — "2일차 진행해"
1. PROGRESS.md에서 2일차 항목(지형+정사영상 → Unity Terrain)을 확인한다.
2. `gis-pipeline-engineer`(dem, ortho, export)와 `unity-world-builder`(Terrain 임포터)를 호출한다. 엔지니어 결과가 나온 뒤 Unity 임포트를 실행한다.
3. `qa-inspector`가 이음매·높이 범위·축 방향을 검증해 PASS를 낸다.
4. PROGRESS.md 2일차를 완료로 표시하고 스크린샷 경로를 보고한다.

### 오류 흐름 — 정사영상 미도착
1. `data/raw/manifest.json`에 ortho 레이어가 없다.
2. `ortho` 단계는 건너뛰고 manifest에 `ortho: null`로 내보낸다. Unity는 단색 재질을 쓴다.
3. qa는 WARN("정사영상 없음")을 낸다. PROGRESS.md 알려진 문제에 적고 사용자에게 다운로드 안내를 다시 알린다.

### 오류 흐름 — 축 뒤집힘
1. qa가 스크린샷에서 자라섬이 서쪽에 보인다고 FAIL을 낸다.
2. 메인은 `axis_test` 결과를 확인하고 담당(엔지니어: glTF X 부호 / Unity: 임포터 회전)을 지정해 되돌린다.
3. 2회 연속 FAIL이면 사용자에게 보고한다.
