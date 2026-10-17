# 제3자 고지 (NOTICE)

이 레포가 쓰는 외부 자료와 라이브러리의 출처·라이선스입니다. 자세한 원본 목록은 `external/README.md`, `ref/README.md`에 있습니다.

## 라이선스 범위

- [MIT 라이선스](LICENSE)는 **이 레포의 코드**(`src/`, `api/`, `web/`, `notebooks/`, `scripts/`, `tests/`, `configs/`)에만 적용됩니다. Copyright (c) 2026 Im Junseo.
- **대회 제공 데이터**(SK텔레콤 유동인구, 신한카드)는 레포에 들어 있지 않고, 대회 규정에 따라 재배포할 수 없습니다. `reports/`에는 이 데이터를 집계한 결과만 있습니다.
- **외부 공공데이터**(`external/`, `ref/`)는 아래 표에 적은 각 원본의 이용 조건(공공누리 등)을 따릅니다. MIT 라이선스로 다시 허락하는 것이 아닙니다.
- `ref/adm_boundary.geojson`, `ref/adm_cd_names.csv`는 vuski/admdongkor 자료(CC BY 4.0)를 가공한 것입니다. 쓸 때 출처를 표시합니다.

## 자료

| 자료 | 쓰는 곳 | 출처 | 라이선스·조건 |
|---|---|---|---|
| 행정동 경계·코드 (vuski/admdongkor) | `ref/adm_cd_names.csv`, 지도 | https://github.com/vuski/admdongkor (원천: 통계청 SGIS) | CC BY 4.0. 출처 표시 필요 |
| Open-Meteo 과거 날씨 | `external/weather/openmeteo_*.json` (비교용) | https://open-meteo.com/ | CC BY 4.0. 출처 표시 필요 |
| 기상청 ASOS 일자료 (기상특보는 받지 않고 이 일자료로 근사) | `external/weather/kma_asos_daily_2025-07_12.csv` (원문은 `raw/`, 올리지 않음) | 공공데이터포털 15059093 (기상청) | 공공누리 1유형 (출처 표시) |
| 공휴일 (2025년 월력요항) | `configs/project.yaml` `holidays`, `external/holidays/공휴일_2025-07_12.csv` | 한국천문연구원 천문우주지식정보 https://astro.kasi.re.kr/kor/life/post/calendarData?year=2025 (우주항공청 관보 게재 자료) | 공공 정보. 출처 표시 |
| 주민등록 인구·세대 | `external/resident/` | 공공데이터포털 15097972, 15097974 (행정안전부) | 이용허락범위 제한 없음 |
| 춘천 독거노인 현황 | `external/chuncheon/` | 공공데이터포털 15081202 (춘천시) | 이용허락범위 제한 없음 |
| 서울 시민생활 데이터 | `external/seoul_living/` | 서울 열린데이터광장 OA-22266, OA-22267 | 공공누리 1유형 (출처 표시) |
| 복지자원 원장 | `external/resources/` | 여러 공공데이터. 원본별 URL은 `external/resources/원본_출처목록.csv` | 각 원본 라이선스 |
| 도로명주소 검색·좌표 (자원 좌표) | `external/resources/좌표_도로명주소.csv` → 원장 `lat`·`lon` (원문은 `raw/juso_coord/`, 올리지 않음) | 행정안전부 도로명주소 개발자센터 https://business.juso.go.kr (검색 API·좌표제공 API) | 승인키 신청 목적 안에서 사용. 출처 "행정안전부 도로명주소" 표시 |
| 휴일 운영 약국·병의원, 응급의료기관 (E-Gen) | `external/resources/추가자원_원장.csv` (원문은 `raw/`, 올리지 않음) | 공공데이터포털 15000576 (약국), 15000736 (병·의원), 15000563 (응급의료기관). 국립중앙의료원 | 이용허락범위 제한 없음 |
| 복지로 복지서비스 (중앙부처·지자체) | 같은 파일 (제도·사업 행) | 공공데이터포털 15090532, 15108347. 한국사회보장정보원 | 이용허락범위 제한 없음 |
| 노인돌봄서비스 기관 현황 (노인맞춤돌봄 수행기관) | `external/resources/수동_추가자원.csv` | 공공데이터포털 15004317 (보건복지부, 2024-04-23 기준) | 이용허락범위 제한 없음 |
| 구청·시청 공식 안내 (강남구 보도·쉼터 목록, 춘천시 복지 안내, 서울시 내 손안에 서울) | `external/resources/수동_추가자원.csv`, `정정_기록.csv` | 행마다 출처 URL·확인일 (`source_url`, `verified_url`, `page_collected_at`) | 사실(기관명·주소·전화·운영 시간)만 옮기고 출처 표시. 글·사진은 옮기지 않음 |
| 행정동 경계 (강남·춘천 47개 동만) | `ref/adm_boundary.geojson` (보강 자원 좌표 → 행정동) | vuski/admdongkor ver20250701 (원천: 통계청 SGIS) | CC BY 4.0. 출처 표시 필요 |
| 지도 바탕 타일 | 화면 지도 (`web/src/pages/MapPage.tsx`) | © OpenStreetMap contributors (https://www.openstreetmap.org/copyright) | 자료는 ODbL. 화면에 출처 표시 필요 |
| 대회 제공 자료 (카드·통신 등) | `data/` (레포에 올리지 않음) | 2026 빅콘테스트 | 대회 규정. 재배포 금지 |

### 자원 원장 보강 주의
- API 원문 응답(`external/**/raw/`)은 올리지 않습니다. 키가 URL에 들어가므로 수집 기록·오류 문장은 키를 가린 뒤 남깁니다.
- E-Gen 공휴일 진료시간은 평소 공휴일 기준입니다. 명절 당일 운영은 응급의료포털에서 다시 확인해야 합니다.
- 아직 받지 못한 것: 행정안전부 무더위쉼터·한파쉼터(재난안전데이터공유플랫폼 safetydata.go.kr 별도 인증키 필요). 자세한 것은 `reports/w1_자원원장/README.md` 6절.
- **원장 좌표 (도로명주소로 바꿈, 2026-10-18)**: 주소가 있는 자원 좌표는 행정안전부 도로명주소 개발자센터(business.juso.go.kr) **검색 API + 좌표제공 API**(승인키 `JUSO_SEARCH_KEY`·`JUSO_COORD_KEY`)로 받은 건물 출입구 좌표입니다(`external/resources/좌표_도로명주소.csv`, `coord_source` = 도로명주소 좌표제공 API). 승인키 신청 목적(이 분석) 안에서 쓰고, 출처를 "행정안전부 도로명주소"로 표시합니다. API 원문은 올리지 않습니다.
- 처음 쓴 Esri ArcGIS World Geocoder(키 없음, 2026-10-13) 좌표는 도로명주소로 하나로 정해지지 않은 **10건에만** 남아 있고, `coord_source`에 "도로명주소 미매칭"이 붙은 탐색용입니다. Esri 약관은 결과 저장을 따로 다루므로 이 10건도 주소를 고쳐 도로명주소로 바꿀 대상입니다. 지오코더 응답 원문은 레포와 제출 묶음에 넣지 않습니다.
- 원천 자료가 준 위경도(E-Gen, 정신건강복지센터 기초데이터, 춘천 노인복지관)는 그대로 둡니다(탐색용 표시 유지). 비교 결과는 `external/resources/좌표_교체_기록.csv`.

### 기상특보 근사
- 기상청 기상특보 자료는 받지 않았습니다. R4·R6 트리거는 ASOS 일자료에 다음 기준을 대어 근사합니다: 폭염 최고기온 ≥33℃, 호우 일강수량 ≥80mm, 대설 일 신적설 ≥5cm, 한파 최저기온 ≤−12℃ (`src/bigcontest/agent/tools/signal.py`). 기상청 특보 기준(예상 기준, 3·12시간 강수, 2일 이상 지속 등)과 같지 않으므로 "특보 발효"로 말하지 않습니다. 비교표는 `docs/데이터_설명.md`.

### 지도 타일 주의
- 공용 OSM 타일 서버(`tile.openstreetmap.org`)는 시연·개발용으로만 씁니다. [타일 이용 정책](https://operations.osmfoundation.org/policies/tiles/)상 실제 서비스 운영에 쓰면 안 됩니다.
- 기관에 배포할 때는 국토지리정보원 바로e맵, 기관 내부 타일 서버, 또는 행정동 경계만 그리는 방식으로 바꿉니다. 망분리 환경에서는 공용 타일에 접속할 수 없습니다.

## 라이브러리

설치 목록 전체는 `uv.lock`, `web/package-lock.json`에 있습니다. 주요한 것만 적습니다.

### Python (PyPI)
| 이름 | 라이선스 |
|---|---|
| fastapi, starlette, pydantic, uvicorn, httpx | MIT / BSD-3-Clause |
| langgraph, langgraph-checkpoint-sqlite | MIT |
| anthropic | MIT |
| duckdb | MIT |
| pandas, numpy, scipy, scikit-learn, statsmodels | BSD-3-Clause |
| pyarrow | Apache-2.0 |
| lightgbm | MIT |
| shap | MIT |
| ruptures | BSD-2-Clause |
| matplotlib | Matplotlib License (PSF 계열) |
| pyyaml | MIT |

### 화면 (npm)
| 이름 | 라이선스 |
|---|---|
| react, react-dom | MIT |
| react-router-dom | MIT |
| leaflet | BSD-2-Clause |
| react-leaflet, @react-leaflet/core | Hippocratic License 2.1 |
| recharts | MIT |
| vite, vitest, tailwindcss, typescript | MIT / Apache-2.0 (typescript) |

- react-leaflet의 Hippocratic License는 OSI 승인 라이선스가 아니며 인권 관련 사용 조건이 붙어 있습니다. 기관 배포 전에 법무 검토가 필요합니다.
- 라이선스는 각 패키지 버전에 따라 바뀔 수 있습니다. 배포 전에 lock 파일 기준으로 다시 확인합니다.
