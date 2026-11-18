# 데이터 출처·이용조건 기록

확인 날짜는 2026-11-17이다(원경 수면 `skirt_water_areas` 행만 2026-11-18). 상태: 미확보 / interim(임시 공개 대체) / 확보. "확인 필요"는 공개 웹에서 확인하지 못한 항목이며 추측으로 채우지 않았다. 출처 표시 문구 중 "(제안)"은 제공처가 지정한 문구를 찾지 못해 공공누리 제1유형(출처표시) 관례로 만든 문구다.

| 레이어 | 데이터명 | 제공처 | 기준일 | 형식 | 좌표계 | 이용조건·출처 표시 문구 | 상태 | 확인 URL | 확인 날짜 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dem | 수치표고모델(공개 DEM) | 국토교통부 국토지리정보원(국토정보플랫폼) | 확인 필요(공공데이터포털 수정일 2025-06-17) | IMG. 해상도 확인 필요(5m는 안심구역 공개제한 목록에 있음) | 확인 필요(파일로 확인) | 이용허락범위 제한 없음, 무료. 표시 문구(제안): "국토교통부 국토지리정보원, 수치표고모델(DEM)" | 미확보 | https://www.data.go.kr/data/15059920/fileData.do ; https://www.datafreezone.or.kr/center_01.do | 2026-11-17 |
| ortho | 항공 정사영상 | 국토교통부 국토지리정보원(국토정보플랫폼) | 촬영연도 확인 필요(공공데이터포털 수정일 2025-06-22) | TIFF(도시 12cm, 일반 25cm) | 확인 필요 | 이용허락범위 제한 없음. 표시 문구(제안): "국토교통부 국토지리정보원, 항공 정사영상". 무료 여부는 신청 화면에서 확인 필요 | 미확보 | https://www.data.go.kr/data/15059919/fileData.do | 2026-11-17 |
| ortho(보조) | 국토위성 영상(0.5m 표기는 확인 필요) | 국토지리정보원 국토위성센터 | 확인 필요 | 확인 필요 | 확인 필요 | 확인 필요 | 미확보 | https://www.ngii.go.kr/nlsc/service/method | 2026-11-17 |
| buildings | GIS건물통합정보(AL_D010) | 국토교통부(브이월드, 공공데이터포털) | 파일명 날짜로 확정(공공데이터포털 수정일 2026-08-31) | SHP(면), 시도 단위 파일 | 자료마다 EPSG:5186 또는 5174로 상이 → .prj로 확인 | 공공누리 제1유형(출처표시), 무료. 표시 문구(제안): "국토교통부, GIS건물통합정보(브이월드)". 전국 구축기관이 달라 참고용 활용 안내 | 미확보 | https://www.data.go.kr/data/15083092/fileData.do | 2026-11-17 |
| ndmap | 연속수치지형도(수치지도 2.0) 도로·하천·철도 | 국토교통부 국토지리정보원(국토정보플랫폼; 브이월드에도 레이어별 제공) | 확인 필요(공공데이터포털 수정일 2025-06-22) | SHP 또는 NGI, 1:5,000·1:25,000 | EPSG:5186 또는 5179로 자료마다 상이 → .prj로 확인 | 공공누리 제1유형(출처표시). 브이월드 개별 레이어는 CC BY 표기가 있어 받을 때 화면 표기 확인. 표시 문구(제안): "국토교통부 국토지리정보원, 연속수치지형도" | 미확보 | https://www.data.go.kr/data/15059721/fileData.do ; https://data.edmgr.kr/dataView.do?id=vworld_open_30180 | 2026-11-17 |
| boundary | 행정구역 읍면동(LT_C_ADEMD)·리(LT_C_ADRI) 경계 | 국토교통부(브이월드, 공공데이터포털) | 실시간 갱신(공공데이터포털 표기) | WMS/WFS(WFS명 lt_c_ademd_info, lt_c_adri_info), 다운로드 SHP 가능성은 확인 필요 | 확인 필요 | 공공누리 제1유형(출처표시), 무료. 표시 문구(제안): "국토교통부, 행정구역 경계(브이월드)" | 미확보 | https://www.data.go.kr/data/15059008/openapi.do | 2026-11-17 |
| dem (대체) | Copernicus DEM GLO-30 | ESA / Copernicus (DLR, Airbus), AWS 공개 버킷 | 2024_1 등 릴리스(받을 때 manifest에 기록) | GeoTIFF, 30m | WGS84(EPSG:4326), 수직 EGM2008(EPSG:3855) | Copernicus DEM 라이선스(상업 이용 가능, 크레딧 필수). 아래 크레딧 문구 | interim | https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM ; https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM/resources/license/License-COPDEM-30.pdf | 2026-11-17 |
| buildings·ndmap 등 (대체) | OpenStreetMap 벡터(building, highway, water, railway). raw manifest 레이어: buildings, water_areas, waterways(물줄기 선, 경사 수면 흐름 방향), islands, roads, railways, boundaries | OpenStreetMap contributors (Overpass API) | 받은 날짜를 manifest에 기록(2026-11-17T08:43:34Z~08:54:45Z, bbox 37.770785, 127.495998 ~ 37.839487, 127.548713) | GeoJSON 등 | EPSG:4326 | ODbL 1.0. 크레딧 필수, 가공 결과 데이터베이스는 같은 라이선스(공유 조건). 아래 크레딧 문구 | interim | https://www.openstreetmap.org/copyright | 2026-11-17 |
| skirt_water_areas (대체, 원경) | OpenStreetMap 수면 폴리곤(범위 밖 원경 수면 `SKW_<i>`, D-013). `fetch-open --only osm --layers skirt_water_areas`로 따로 받음 | OpenStreetMap contributors (Overpass API) | 받은 날짜 2026-11-18T02:11:48Z(raw manifest `date`), 등록 2026-11-18T11:12:59+09:00 | GeoJSON(676,452 B), `data/raw/open/osm/phase1/skirt_water_areas.geojson` | EPSG:4326 | ODbL 1.0 (출처 표시, 파생 DB 동일조건). 출처 문구: "© OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)"(raw manifest `attribution`, 다른 OSM 레이어와 같음). bbox 37.660149, 127.357003 ~ 37.949903, 127.688326(위도, 경도 남서 ~ 북동 = 범위 + 12.5 km) | interim | https://overpass-api.de/api/interpreter ; https://www.openstreetmap.org/copyright | 2026-11-18 |

## 빌드 크레딧 문구

빌드의 크레딧 화면에 아래를 그대로 넣는다. 공식 레이어는 실제로 확보·사용한 것만 남긴다.

**공식 데이터(제안 문구, 공공누리 제1유형 관례)**
- 국토교통부 국토지리정보원, 수치표고모델(DEM)
- 국토교통부 국토지리정보원, 항공 정사영상
- 국토교통부, GIS건물통합정보(브이월드)
- 국토교통부 국토지리정보원, 연속수치지형도
- 국토교통부, 행정구역 경계(브이월드)

**Copernicus DEM GLO-30(interim 사용 시)**
- 확인된 공식 표기(Copernicus Data Space 컬렉션 페이지): "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved"
- 가공(재투영·리샘플링)한 경우 "Produced using Copernicus WorldDEM-30"을 앞에 붙이도록 라이선스가 요구한다는 검색 요약이 있다. 아래처럼 합쳐 쓰고, 최종 문구는 라이선스 PDF 원문으로 **확인 필요**:
  "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved"
- 재배포 시 면책 문구(Copernicus 프로그램 담당 기관은 WorldDEM-30 사용에 책임지지 않음)도 따라야 한다는 요약이 있음 → 원문 확인 필요.

**OpenStreetMap(interim 사용 시)**
- "© OpenStreetMap contributors" + ODbL 라이선스 안내(링크 https://www.openstreetmap.org/copyright). OSM 사이트는 고정 문자열이 아니라 매체별 가이드(https://osmfoundation.org/wiki/Licence/Attribution_Guidelines)를 따르라고 안내한다. 위 문구는 OSM 재단의 일반 표기이며 이번 조회에서 해당 가이드 본문은 열람하지 못했다. 크레딧 화면은 링크를 못 걸 수 있으므로 URL을 문자로 적는다.
- ODbL 공유 조건: OSM에서 파생한 데이터를 배포하면 같은 라이선스로 제공해야 한다. 3D 게임에 합쳐 넣었을 때 어디까지가 파생 데이터베이스인지는 법률 판단이 필요하므로 **확인 필요**(OSM은 interim으로만 쓰고 공식 레이어로 교체하면 쟁점이 사라진다).

**금지 소스**: 카카오·네이버·구글 지도/로드뷰/3D 타일은 원천으로 쓰지 않으며 크레딧에도 넣지 않는다.

## 아트 에셋(생성형)

Unity 외벽·지붕·지면·교량 텍스처(`unity/JourneyOn/Assets/Art/Cozy/*.jpg` 17장)는 공간정보가 아니라 프롬프트로만 만든 일반 재질 그림이다. 결정은 `docs/decisions.md` D-014, 계약은 `docs/ART.md`에 있다.

| 항목 | 내용 |
| --- | --- |
| 생성 모델 | OpenAI `gpt-image-2.6-sunburst`(quality `high`, 1024×1024, 공업 외벽만 1536×1024). 같은 외벽·잔디 프롬프트로 `gpt-image-2.6-flare`, `gemini-3-pro-image`, `gemini-nano-banana-2.1`과 비교해 골랐다(`_workspace/art/model_compare/`). 비교용 Gemini·flare 생성물은 Unity에 넣지 않았다 |
| 경유 | MonoGPT 라우터(`https://monogpt.kr/api/monorouter/v1`, `/images/generations`, 키는 `.env`의 `MONOGPT_API_KEY`) |
| 생성일 | 2026-11-18 |
| 용도 | 건물 용도별 외벽·지붕, 잔디·보도·도로·철도 자갈, 교량 상판의 반복 텍스처(아기자기 테마). 정사영상이 오면 지면은 정사영상을 우선한다 |
| 기록 | 파일마다 모델·크기·품질·프롬프트 전문·생성 시각·원본 sha256·후처리 내용, 전체 API 호출 수: `Assets/Art/Cozy/art_manifest.json`. 원본 PNG와 호출 기록은 `art/raw/cozy/`(git 제외) |
| 참조 이미지 | 없음. 지도·로드뷰·실사진·특정 장소 이미지를 넣지 않았다. 특정 건물·장소를 재현한 것이 아니다 |
| 이용조건 요지 | OpenAI 이용약관은 "이용자와 OpenAI 사이에서 이용자가 Output을 소유하고, OpenAI는 Output에 대해 가진 권리를 이용자에게 양도한다"는 취지이며, 출력이 다른 이용자의 출력과 비슷할 수 있다는 단서가 있다(일반에 알려진 약관 요지. 2026-11-18 openai.com 약관 원문 조회는 403으로 실패 → **확인 필요**) |
| 라우터 약관 | MonoGPT(재판매·중계) 쪽 이용약관에서 생성물 권리·상업 이용을 어떻게 정하는지는 확인하지 못했다(monogpt.kr 첫 화면에 약관 본문 없음) → **확인 필요**. 중계를 거칠 때 OpenAI 약관이 이용자에게 그대로 적용되는지도 **확인 필요** |
| 크레딧 | 필수 표기 요건은 확인하지 못했다(확인 필요). 빌드 크레딧에 "일부 텍스처는 생성형 AI(OpenAI gpt-image, MonoGPT 경유)로 제작"을 넣기를 제안한다 |
