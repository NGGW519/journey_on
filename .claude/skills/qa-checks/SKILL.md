---
name: qa-checks
description: "가평읍 3D 프로젝트의 검증 점검표와 판정 기준. 원천 데이터·파이프라인 타일·manifest·Unity 씬·빌드를 단계별로 검사하고, 좌표·축·이음매·높이·누락·이용조건 준수를 확인하고, PASS/WARN/FAIL verdict와 검수 리포트를 쓸 때 사용한다. 산출물을 만드는 방법은 geo-pipeline, unity-world 스킬이 맡는다."
---

# 검증 점검표

## 원칙

- 모듈이 끝날 때마다 바로 검증한다. 경계면(원천→타일→manifest→Unity)에서 숫자가 이어지는지가 핵심이다.
- 자동 검사 결과를 그대로 옮기지 말고 수치가 실제 지리와 맞는지 판단한다. 예: 북한강 수면 높이가 30~60m 밖이면 이상하다(청평호 상시만수위 부근).
- 고치지 않고 지적한다: 위치, 원인 추정, 재현 명령, 담당 에이전트.

## 자동 검사

```bash
.venv/Scripts/python -m pipeline validate --area phase1            # 전 단계
.venv/Scripts/python -m pipeline validate --area phase1 --stage dem
.venv/Scripts/python -m pytest -q
```

`validate`는 항목별 PASS/WARN/FAIL을 출력하고 `data/out/<area>/reports/validate.json`에 남긴다.

## 단계별 점검표

### 원천(수집)
- [ ] manifest의 모든 파일이 존재하고 크기 > 0
- [ ] 각 레이어 범위가 대상 bounds를 덮음
- [ ] `docs/DATA_SOURCES.md`에 출처·이용조건·확인 날짜가 있음
- [ ] 금지 소스(로드뷰·구글 3D 타일·민간 지도) 흔적 없음 — URL·파일명·provenance 확인
- [ ] interim 레이어 목록을 WARN으로 보고

### 지형(dem)
- [ ] 타일 수 = 격자 수, 각 RAW 크기 = 1025×1025×2 바이트
- [ ] 표고 범위가 `base_m`~`base_m+range_m` 안이고 0·65535 포화가 없음
- [ ] 이웃 타일 공유 가장자리 값이 같음(이음매 0)
- [ ] 미리보기에서 북한강 골짜기가 자라섬 동쪽·남쪽으로 이어짐(방향 확인)

### 정사영상(ortho)
- [ ] 크기 = `ortho_px`, 위쪽이 북쪽
- [ ] 강·도로가 DEM 골짜기·하천 벡터와 겹침(좌표 어긋남 < 5m 목표)

### 건물(buildings)
- [ ] glb 노드 수 = buildings.json 항목 수
- [ ] 높이 > 0, 바닥 ≤ 대표점 DEM 값 + 0.5m(떠 있지 않음)
- [ ] 층수 기본값 비율을 보고(높으면 WARN, 누락 목록 경로 첨부)
- [ ] `no_build_zones`(남이섬) 안 건물 0
- [ ] `axis_test.glb`의 긴 팔이 +X(동쪽)

### 수면(water)
- [ ] 수면 높이가 상식 범위, 수면이 지형보다 위에서 강 전체를 덮음
- [ ] 자라섬·남이섬이 수면 위로 드러남

### Unity
- [ ] 임포트 로그(`_workspace/logs/NN_import.log`)에 `error CS`·Exception 0건, Safe Mode 아님
- [ ] `data/out/<area>/reports/unity_import.json`: terrains = manifest 타일 수, building_nodes = building_meta = buildings_with_info, `axis_ok: true`, warnings 비어 있음
- [ ] 스크린샷 검토: 원점에서 북쪽, 자라섬 부감, 남이섬 선착장 → 남쪽. 지형-영상-건물-수면이 서로 맞는지, 뒤집힘·떠 있음·틈 없는지
- [ ] 플레이어가 범위 밖·수면으로 나가지 못함

### 빌드
- [ ] exe가 실행되고 30초 이상 프레임이 진행됨, 크레딧(출처 표시)이 있음

## 사람 검수(로드뷰)

로드뷰는 사람이 브라우저에서 눈으로만 본다. 에이전트는 확인할 지점 목록을 `docs/qa/roadview_checklist.md`로 만든다: 지점 이름, WGS84 좌표, Unity 좌표, 볼 방향, 질문(예: "가평역 앞 건물 층수가 3층으로 보이는가?").

## verdict 형식

`_workspace/{NN}_qa-inspector_verdict.md`

```markdown
# {NN}일차 검증 — {범위}/{단계}
| 항목 | 판정 | 근거 | 담당 |
| --- | --- | --- | --- |
| RAW 이음매 | PASS | 최대 차 0 | - |
| 층수 기본값 비율 | WARN | 41% (OSM interim) | geodata-collector |
...
전체: WARN
```
