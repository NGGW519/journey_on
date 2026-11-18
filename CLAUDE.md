# journey_on — 가평읍 3D 가상공간

가평읍을 공공 공간정보로 Unity에 재현한다. 범위는 1단계 관광지(달전리 수변)에서 시작해 시가지, 읍 전역으로 넓힌다. 기획서: `가평읍 3D 가상공간 구현 기획서.md`. 진행 상황: `docs/PROGRESS.md`.

## 프로젝트 규칙
- 모델링 원천은 국토부·국토지리정보원 공개 데이터만 쓴다. 공식 데이터가 오기 전에는 OSM·Copernicus를 `interim`으로 표시해서 쓴다. 카카오·네이버·구글 로드뷰와 구글 3D 타일은 사람이 눈으로 검수할 때만 쓰고, 자동으로 접근하지 않는다. `.claude/hooks/license_guard.py`가 이런 접근을 막는다.
- 좌표·타일·높이맵·축 규약은 `config/project.yaml`과 `docs/decisions.md`에 있다. 규약을 바꾸면 결정 항목을 추가한다.
- Python 실행은 `.venv/Scripts/python -m pipeline <stage> --area <area>`, 테스트는 `.venv/Scripts/python -m pytest -q`로 한다.
- 데이터(`data/`)와 하네스 중간 산출물(`_workspace/`)은 git에서 제외한다. 원천 이용조건은 `docs/DATA_SOURCES.md`에 기록한다.

## 하네스: 가평읍 3D 가상공간 파이프라인

**목표:** 공공 공간정보를 재실행 가능한 Python 파이프라인으로 변환하고 Unity 월드로 조립한 뒤, 단계마다 독립 검증을 거쳐 일정대로 데모·빌드를 낸다.

**호출 조건:** 이 프로젝트의 데이터 수집, 변환, Unity 배치·빌드, 검증, 일정 진행("다음 작업", "N일차", "다시 만들어", "검수해")을 요청받으면 `journey-on-orchestrator` 스킬을 쓴다. 단순한 질문에는 바로 답해도 된다.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
| --- | --- | --- | --- |
| 2026-11-17 | Harness v2로 처음 구성: 에이전트 4개(geodata-collector, gis-pipeline-engineer, unity-world-builder, qa-inspector), 스킬 5개(journey-on-orchestrator, geodata-sourcing, geo-pipeline, unity-world, qa-checks), 약관 가드 훅 | 전체 | 기획서 착수 |
| 2026-11-17 | 1차 실행 결과를 스킬에 반영: geo-pipeline 코드 표·공식 건물 필드 절차, unity-world 배치 모드 명령·경계 처리, qa-checks Unity 항목(unity_import.json) | 스킬 3개 | 실제 코드·검증 흐름과 문서 불일치 |
| 2026-11-18 | manifest v2 계약(C1 외벽 UV, C2 도로 마스크, C3 교량, C4 원경)을 manifest-schema에 추가. 오케스트레이터에 '계약 먼저 → 병렬 → 시험 임포트 → 최종' 절차 추가. geo-pipeline·unity-world 코드 표 갱신 | manifest-schema, 오케스트레이터, 스킬 2개 | 파이프라인·Unity 병렬 작업 중 형식 불일치 방지 |
