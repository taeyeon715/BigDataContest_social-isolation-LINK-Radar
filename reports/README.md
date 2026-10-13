# 분석 리포트

각 폴더의 `README.md`가 리포트 본문입니다. 아래 그림을 누르면 그 리포트로 이동합니다.

이 파일은 리포트 스크립트(`notebooks/`)를 실행하면 자동으로 다시 만들어집니다(`src/bigcontest/gallery.py`). 이름 앞머리: `eda*` 탐색, `m*` 모델, `r*` 규칙, `w*` 자원, `e*` 평가.

## 목록

| 리포트 | 그림 수 |
|---|---:|
| [EDA-0 적재 결과](eda0_적재결과/README.md) | 0 |
| [EDA-1 데이터 진단](eda1_데이터진단/README.md) | 6 |
| [EDA-2 통신 기초](eda2_통신기초/README.md) | 7 |
| [EDA-3 카드 기초](eda3_카드기초/README.md) | 6 |
| [EDA-4 외부 데이터 결합](eda4_외부데이터결합/README.md) | 3 |
| [EDA-5 핵심 인사이트 재현](eda5_인사이트재현/README.md) | 5 |
| [EDA-6 사전 경보 재평가](eda6_사전경보재평가/README.md) | 3 |
| [M1 기대 활동 모델](m1_기대활동모델/README.md) | 2 |
| [M2 대리 과제 B (보조 과제)](m2_대리과제B/README.md) | 1 |
| [R-1 후보·등급 규칙 평가 (rules-v1.0)](r1_등급규칙/README.md) | 1 |
| [W-1 자원 원장 보강](w1_자원원장/README.md) | 0 |

## 그림 모음

### [EDA-1 데이터 진단](eda1_데이터진단/README.md)

| | |
|---|---|
| <a href="eda1_데이터진단/README.md"><img src="eda1_데이터진단/figs/f01_tables_consistency.png" width="420"></a><br>세 파일 정합성 <sub>(2. 통신: 세 파일은 같은 총량을 나눈 것)</sub> | <a href="eda1_데이터진단/README.md"><img src="eda1_데이터진단/figs/f02_cell_size.png" width="420"></a><br>칸별 체류량 <sub>(3. 통신: 값의 단위와 0)</sub> |
| <a href="eda1_데이터진단/README.md"><img src="eda1_데이터진단/figs/f03_gangnam_hq.png" width="420"></a><br>강남 본사등록 업종 <sub>(5. 카드: 법인과 본사등록 업종)</sub> | <a href="eda1_데이터진단/README.md"><img src="eda1_데이터진단/figs/f04_use_cnt_lattice.png" width="420"></a><br>결제건수 배율 <sub>(6. 카드: 결제건수는 '배율이 곱해진 값')</sub> |
| <a href="eda1_데이터진단/README.md"><img src="eda1_데이터진단/figs/f05_local_share.png" width="420"></a><br>거주지 비중 <sub>(8. 카드: 가맹점 위치 ≠ 고객 거주지)</sub> | <a href="eda1_데이터진단/README.md"><img src="eda1_데이터진단/figs/f06_daily_index.png" width="420"></a><br>일별 흐름 <sub>(9. 카드: 날짜별 흐름과 튀는 날)</sub> |

### [EDA-2 통신 기초](eda2_통신기초/README.md)

| | |
|---|---|
| <a href="eda2_통신기초/README.md"><img src="eda2_통신기초/figs/f01_city_composition.png" width="420"></a><br>도시 구성 <sub>(1. 도시별 성·연령 구성)</sub> | <a href="eda2_통신기초/README.md"><img src="eda2_통신기초/figs/f02_profile_강남.png" width="420"></a><br>강남 행정동 지표 <sub>(2. 행정동 프로필)</sub> |
| <a href="eda2_통신기초/README.md"><img src="eda2_통신기초/figs/f02_profile_춘천.png" width="420"></a><br>춘천 행정동 지표 <sub>(2. 행정동 프로필)</sub> | <a href="eda2_통신기초/README.md"><img src="eda2_통신기초/figs/f03_map_강남.png" width="420"></a><br>강남 지도 <sub>(3. 250m 격자 지도)</sub> |
| <a href="eda2_통신기초/README.md"><img src="eda2_통신기초/figs/f03_map_춘천.png" width="420"></a><br>춘천 지도 <sub>(3. 250m 격자 지도)</sub> | <a href="eda2_통신기초/README.md"><img src="eda2_통신기초/figs/f04_daily_rhythm.png" width="420"></a><br>하루 리듬 <sub>(5. 하루 리듬)</sub> |
| <a href="eda2_통신기초/README.md"><img src="eda2_통신기초/figs/f05_monthly.png" width="420"></a><br>월별 변화 <sub>(7. 7월 → 12월 변화 (행정동 단위))</sub> |  |

### [EDA-3 카드 기초](eda3_카드기초/README.md)

| | |
|---|---|
| <a href="eda3_카드기초/README.md"><img src="eda3_카드기초/figs/f01_mix.png" width="420"></a><br>소비 구성 <sub>(1. 연령대별 소비 구성 (건수 비중))</sub> | <a href="eda3_카드기초/README.md"><img src="eda3_카드기초/figs/f02_daily.png" width="420"></a><br>일별 흐름 <sub>(2. 연령대별 일별 흐름)</sub> |
| <a href="eda3_카드기초/README.md"><img src="eda3_카드기초/figs/f03_weekday.png" width="420"></a><br>요일 효과 <sub>(3. 요일 효과)</sub> | <a href="eda3_카드기초/README.md"><img src="eda3_카드기초/figs/f04_chuseok_category.png" width="420"></a><br>추석 업종별 <sub>(4. 공휴일 효과)</sub> |
| <a href="eda3_카드기초/README.md"><img src="eda3_카드기초/figs/f05_common_factor.png" width="420"></a><br>공통 변동 <sub>(5. 도시 전체가 함께 움직이는 부분)</sub> | <a href="eda3_카드기초/README.md"><img src="eda3_카드기초/figs/f06_evening.png" width="420"></a><br>저녁 비중 <sub>(7. 시간대 (보조: 데이터1))</sub> |

### [EDA-4 외부 데이터 결합](eda4_외부데이터결합/README.md)

| | |
|---|---|
| <a href="eda4_외부데이터결합/README.md"><img src="eda4_외부데이터결합/figs/f01_resident_vs_telecom.png" width="420"></a><br>통신 vs 주민 <sub>(2. 통신 유동인구는 주민 구성을 얼마나 반영하나)</sub> | <a href="eda4_외부데이터결합/README.md"><img src="eda4_외부데이터결합/figs/f02_single_vs_isolation.png" width="420"></a><br>1인세대 vs 고립률 <sub>(4. 구조 취약성 검증: 1인세대 비율 ↔ 고령 고립률 (강남 22개 동))</sub> |
| <a href="eda4_외부데이터결합/README.md"><img src="eda4_외부데이터결합/figs/f03_single_vs_gap.png" width="420"></a><br>1인세대 vs 거점 거리 <sub>(5. 서비스 공백: 거점 자원까지의 거리)</sub> |  |

### [EDA-5 핵심 인사이트 재현](eda5_인사이트재현/README.md)

| | |
|---|---|
| <a href="eda5_인사이트재현/README.md"><img src="eda5_인사이트재현/figs/f01_midlife_men.png" width="420"></a><br>중장년 남성 <sub>(1. 중장년 남성 1인가구의 소통 결핍 (강남, 시민생활 데이터))</sub> | <a href="eda5_인사이트재현/README.md"><img src="eda5_인사이트재현/figs/f02_holiday_gap.png" width="420"></a><br>연휴 공백 <sub>(3. 연휴 공백: 깊이·준비·회복 (카드 데이터2, 추석))</sub> |
| <a href="eda5_인사이트재현/README.md"><img src="eda5_인사이트재현/figs/f03_winter_sensitivity.png" width="420"></a><br>겨울 감소 민감도 <sub>(4. 춘천 읍·면 주민 거주지의 겨울 감소 (통신))</sub> | <a href="eda5_인사이트재현/README.md"><img src="eda5_인사이트재현/figs/f04_heat_null.png" width="420"></a><br>폭염 우연 검정 <sub>(5. 폭염과 고령층 생필품 (기상청 ASOS))</sub> |
| <a href="eda5_인사이트재현/README.md"><img src="eda5_인사이트재현/figs/f05_alarm_by_age.png" width="420"></a><br>연령별 경보 탐지율 <sub>(6. 시점 경보 규칙의 성능 (가짜 하락 주입 실험, 사후 계산))</sub> |  |

### [EDA-6 사전 경보 재평가](eda6_사전경보재평가/README.md)

| | |
|---|---|
| <a href="eda6_사전경보재평가/README.md"><img src="eda6_사전경보재평가/figs/f01_delay.png" width="420"></a><br>경보 지연 분포 (시작 직전 낮은 날 수별) <sub>(4. 짝지은 하락 주입 실험 (코호트 하나))</sub> | <a href="eda6_사전경보재평가/README.md"><img src="eda6_사전경보재평가/figs/f02_detection_by_age.png" width="420"></a><br>연령별 탐지율 <sub>(4. 짝지은 하락 주입 실험 (코호트 하나))</sub> |
| <a href="eda6_사전경보재평가/README.md"><img src="eda6_사전경보재평가/figs/f03_detection_by_drop.png" width="420"></a><br>하락 폭별 감지율 <sub>(4-1. 하락 폭별 감지율 (최소 감지 하락))</sub> |  |

### [M1 기대 활동 모델](m1_기대활동모델/README.md)

| | |
|---|---|
| <a href="m1_기대활동모델/README.md"><img src="m1_기대활동모델/figs/f01_rel_mae_by_age.png" width="420"></a><br>연령별 h=1 상대 MAE <sub>(1. 예측 성능 (rolling-origin))</sub> | <a href="m1_기대활동모델/README.md"><img src="m1_기대활동모델/figs/f02_detection_by_model.png" width="420"></a><br>모델별 탐지율 <sub>(3. 모델 잔차로 다시 돌린 경보 실험 (EDA-6과 같은 쌍·같은 정의))</sub> |

### [M2 대리 과제 B (보조 과제)](m2_대리과제B/README.md)

| | |
|---|---|
| <a href="m2_대리과제B/README.md"><img src="m2_대리과제B/figs/f01_oof_scatter.png" width="420"></a><br>접기 밖 예측 대 실제 <sub>(2. 결과 (행정동 GroupKFold, 접기 밖 예측))</sub> |  |

### [R-1 후보·등급 규칙 평가 (rules-v1.0)](r1_등급규칙/README.md)

| | |
|---|---|
| <a href="r1_등급규칙/README.md"><img src="r1_등급규칙/figs/후보수_범위.png" width="420"></a><br>기준일·집단별 후보 수 (점 = 기본, 막대 = 임계값 범위) <sub>(3. 민감도와 후보 수 범위)</sub> |  |
