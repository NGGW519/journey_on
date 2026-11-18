---
name: geodata-sourcing
description: "가평읍 3D 프로젝트의 원천 공간데이터를 어디서 어떻게 받고 어떻게 기록하는지 정한다. 국토정보플랫폼 DEM·정사영상, 브이월드 GIS건물통합정보·행정경계, 수치지도 2.0, OSM·Copernicus 공개 대체 데이터, 이용조건 기록, 금지 소스(로드뷰·구글 3D 타일) 판단이 필요할 때 사용한다. 데이터 가공·변환 방법은 geo-pipeline 스킬이 맡는다."
---

# 원천 데이터 확보와 기록

## 왜 이렇게 하는가

이 프로젝트는 공개 배포하는 홍보물이다. 원천의 이용조건이 깨끗해야 배포할 수 있다. 그래서 모델링 원천은 국토부·국토지리정보원 공개 데이터로 한정하고, 받은 즉시 출처와 조건을 기록한다. 공식 데이터가 늦을 때만 이용조건이 열린 공개 데이터(OSM ODbL, Copernicus DEM)를 **임시로** 쓰고 반드시 `interim`으로 표시한다.

## 레이어별 제공처

| 레이어 | 공식 원천 | 접근 | 임시 대체 |
| --- | --- | --- | --- |
| 지형 | 국토지리정보원 수치표고모델(공개DEM, 5m) — 국토정보플랫폼 map.ngii.go.kr | 로그인 + 대용량 전송 S/W. 사용자 조치 | Copernicus GLO-30 DSM (AWS 공개 버킷) |
| 정사영상 | 국토정보플랫폼 항공 정사영상(GeoTIFF), 국토위성 0.5m(보조) | 로그인·신청. 승인 대기 가능 | 없음 → 단색 재질(기획서 대비책) |
| 건물 | GIS건물통합정보(AL_D010) — 브이월드 데이터 다운로드(SHP, 시군구 단위) 또는 WFS | 브이월드 로그인 / API 키 | OSM building |
| 도로·하천·철도 | 수치지도 연속 2.0 — 국토정보플랫폼 | 로그인 | OSM highway / water / railway |
| 행정경계 | 브이월드 읍면동·리 경계(LT_C_ADEMD, LT_C_ADRI) | API 키 / 다운로드 | OSM boundary |
| 대조 | Cesium OSM Buildings | — | 누락 건물 비교에만 |

공식 데이터를 받는 절차(사용자 조치)는 `docs/DATA_ACQUISITION.md`에 단계별로 유지한다.

## 금지 소스

카카오·네이버·구글 로드뷰, 구글 3D 타일(Photorealistic 3D Tiles), 민간 지도 타일은 캡처·가공·3D 파생이 약관상 금지다.
- 스크립트·WebFetch·curl로 접근하지 않는다. `.claude/hooks/license_guard.py`가 이런 호출을 막는다.
- 검수에 필요하면 사람이 볼 좌표와 질문을 목록으로 만든다(`qa-checks` 스킬 참조).
- 사용자가 금지 소스를 원천으로 쓰자고 하면 약관 문제를 설명하고 공식 대안을 제시한다.

## 공개 대체 데이터 받기

```bash
.venv/Scripts/python -m pipeline fetch-open --area phase1            # DEM(Copernicus) + OSM 벡터 전부
.venv/Scripts/python -m pipeline fetch-open --area phase1 --only osm # OSM만 다시
```

- Overpass API에는 일반 클라이언트 이름(User-Agent: `journey-on-pipeline`)만 보낸다. 사용자 이메일·이름을 넣지 않는다.
- 결과는 `data/raw/open/`에 저장되고 `data/raw/manifest.json`에 `interim: true`로 기록된다.

## 공식 데이터를 받았을 때

1. 파일을 `data/raw/official/<layer>/`에 넣는다(압축은 풀어서). 원본 파일명은 바꾸지 않는다.
2. `python -m pipeline register --layer <layer> --path <파일> --source "<제공처·데이터명>" --license "<이용조건>" --date <기준일>`로 manifest에 등록한다. 등록하면 같은 레이어의 interim 항목보다 우선 사용된다.
3. 좌표계를 확인한다(`.prj`가 없으면 제공처 문서의 좌표계를 `--crs`로 지정). 국토정보플랫폼 DEM은 보통 EPSG:5186 계열이지만 반드시 파일로 확인한다.
4. `docs/DATA_SOURCES.md`의 표에 한 줄을 추가한다: 레이어, 데이터명, 제공처, 기준일, 형식, 좌표계, 이용조건(출처 표시 문구 포함), 받은 날짜, 경로.

## 이용조건 기록 형식

`docs/DATA_SOURCES.md`의 각 줄에 반드시 넣는다:
- 출처 표시 문구(빌드의 크레딧 화면에 그대로 쓴다)
- 상업적 이용·변경 허용 여부
- 확인한 URL과 확인 날짜

모르는 항목은 비워 두고 "확인 필요"로 적는다. 추측해 채우지 않는다.

## 받은 뒤 확인할 것

- 범위: 대상 `bounds`를 덮는가(`python -m pipeline inspect --path <파일>`)
- 개수: 건물·수면 피처 수가 0이 아닌가
- 좌표계: EPSG 코드가 명확한가
- 크기: 정사영상이 수십 GB면 대상 범위만 잘라 둔다
