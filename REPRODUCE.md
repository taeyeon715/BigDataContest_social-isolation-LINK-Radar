# 재현 안내

분석 리포트(`reports/`)를 처음부터 다시 만드는 방법입니다. 명령 하나(`scripts/reproduce.py`)가 순서대로 돌리고, 단계마다 걸린 시간을 남기고, 결과 CSV 해시를 비교합니다.

## 1. 준비

```bash
uv sync --frozen                     # Python 3.13 환경 (uv 필요)
# 대회 원본 zip 2개(통신·카드)를 data/raw/ 에 두거나 위치를 지정 (재배포 불가, 레포에 없음)
export BC_RAW_DIR=/path/to/raw       # PowerShell: $env:BC_RAW_DIR = "C:\path\to\raw"
```

- 원본 파일 이름은 [`configs/project.yaml`](configs/project.yaml) `raw_files`에 있습니다.
- LLM 키는 필요 없습니다. 리포트는 LLM을 부르지 않고, 에이전트도 키가 없으면 템플릿 문장으로 같은 등급을 냅니다.

## 2. 한 번에 다시 만들기

```bash
uv run python scripts/reproduce.py --dry-run       # 순서와 원본 필요 여부만 보기
uv run python scripts/reproduce.py --ingest        # 원본 zip 적재부터 전부 (data/interim 을 다시 씀)
uv run python scripts/reproduce.py                 # data/interim 이 이미 있으면 적재 빼고 전부
uv run python scripts/reproduce.py --no-raw        # 원본 없이 되는 단계만
uv run python scripts/reproduce.py --check-hash    # 다 만든 뒤 reports/결과_해시.txt 와 비교
uv run python scripts/reproduce.py --no-raw --check-hash                      # 새 체크아웃, 원본 없이 (w1·w2·m4·점검)
uv run python scripts/reproduce.py --only r1 w2 m4 r2 m3 s1 --check-hash      # data/interim 이 있을 때 부분 재현
```

- 단계별 성공 여부와 시간은 `reports/재현_기록.csv`에 남습니다.
- 몇 단계만: `--only r2 m3`. 실패해도 계속: `--keep-going`.
- 자원 추천 평가의 정답 세트(`notebooks/w2_labels.py`)는 결과를 보기 전에 고정한 입력이라 기본으로 다시 만들지 않습니다(`--relabel`).

## 3. 단계와 필요한 것

| 순서 | 단계 | 명령 | 필요한 것 | 결과 | 원본 없이 | 걸린 시간 |
|---|---|---|---|---|---|---|
| 1 | ingest | `python -m bigcontest.ingest` | 대회 원본 zip | `data/interim/*.parquet`, `reports/eda0_적재결과` | 안 됨 | (아래 참고) |
| 2 | eda1 | `notebooks/eda1_data_quality.py` | 1 | `reports/eda1_데이터진단` | 안 됨 | 20초 |
| 3 | eda2 | `notebooks/eda2_telecom_basics.py` | 1 | `reports/eda2_통신기초` | 안 됨 | 14초 |
| 4 | eda3 | `notebooks/eda3_card_basics.py` | 1 | `reports/eda3_카드기초` | 안 됨 | 6초 |
| 5 | w1 | `notebooks/w1_resource_ledger.py` | `external/` | `reports/w1_자원원장`, 원장 v2 | **됨** | 17초 |
| 6 | eda4 | `notebooks/eda4_external.py` | 1, eda2 | `reports/eda4_외부데이터결합` | 안 됨 | 26초 |
| 7 | eda5 | `notebooks/eda5_insights.py` | 1, eda4 | `reports/eda5_인사이트재현` | 안 됨 | 2분 7초 |
| 8 | eda6 | `notebooks/eda6_alarm_reeval.py` | 1 | `reports/eda6_사전경보재평가` | 안 됨 | 54초 |
| 9 | m1 | `notebooks/m1_expected_activity.py` | 1 | `reports/m1_기대활동모델`, `configs/models.yaml` decisions | 안 됨 | 30분 |
| 10 | m2 | `notebooks/m2_proxy_task_b.py` | 1 | `reports/m2_대리과제B` | 안 됨 | 14초 |
| 11 | r1 | `notebooks/r1_level_rules.py` | 1 | `reports/r1_등급규칙` | 안 됨 | 1분 |
| 12 | w2 | `notebooks/w2_resource_eval.py` | `external/`, 정답 세트 | `reports/w2_자원추천평가` | **됨** | 4초 |
| 13 | m4 | `notebooks/m4_risk_factors.py` | 공개 시민생활 + eda2·eda4·w1 CSV | `reports/m3_모델링결과/M4.md` | **됨** | 21초 |
| 14 | r2 | `notebooks/r2_risk_results.py` | 1, r1·eda3·eda5·eda6·m1 | `reports/r2_위험도결과` | 안 됨 | 17초 |
| 15 | m3 | `notebooks/m3_models.py` | 1, eda6·m1·m2·r1·w2·m4 | `reports/m3_모델링결과` | 안 됨 | 2분 26초 |
| 16 | s1 | `notebooks/s1_link_sim.py` | 1 (실제 자료 비교) | `reports/s1_LINK-Sim`, `data/sim` (가상 자료, git 제외) | 안 됨 | 3분 30초 |
| 17 | t2 | `notebooks/t2_cross_validation.py` | `reports/t1_2축위험도`(태연 분석 결과), r2 CSV, `analysis/taeyeon_2axis/external` | `reports/t2_교차검증` | **됨** | 15초 |
| 18 | t1_5 | `notebooks/t1_5_cohort_s1.py` | 1, r2·t2, `external/resident/` | `reports/t1_5_집단별S1` (집단별 S1 대안 비교, 공식 등급 그대로) | 안 됨 | 25초 |
| 19 | 점검 | `scripts/check_no_raw.py` | — | 리포트·`analysis/` CSV 원천 값 열 점검 | **됨** | 1초 |
| 20 | 시험 | `uv run pytest -q` | — | 원본이 없으면 일부 건너뜀 | 일부 | 3분 40초 |

- 시간은 Windows 11 노트북(2026-10-14, rules-v1.1) 에서 `scripts/reproduce.py`로 잰 값입니다(적재 제외 합계 약 39분, m1 이 30분). 적재(ingest)는 공용 정리본을 덮어쓰지 않으려고 이번에 다시 돌리지 않았습니다.
- 원본 없이도 되는 것: 자원 원장(w1), 자원 추천 평가(w2), 위험 요인 모델(M4), 점검, API·화면(가짜 데이터, `README.md` 시작하기).
- 태연 분석(`analysis/taeyeon_2axis/`)은 이 명령에 넣지 않았습니다. 노트북(04·07)은 대회 원본 zip을 `analysis/taeyeon_2axis/원본데이터/`에 넣고 `convert_to_csv.py`로 `csv/`를 만든 뒤 그 폴더에서 실행합니다. 결과는 `outputs/`에 쓰고 `sync_reports.py`로 `reports/t1_2축위험도/`에 복사합니다. 재현 확인 결과는 [`reports/t1_2축위험도/README.md`](reports/t1_2축위험도/README.md) 7절.
- 난수 seed: 경보 주입 실험 `numpy.random.default_rng(0)` (EDA-6·M3 같은 쌍), 합성 정상 시계열 seed 0~4·0~9, M3 하락률·부트스트랩 seed 0 (`configs/models_m3.yaml`), M4 반복 seed 0~49, LightGBM `random_state=0`.

## 4. 결과 확인 (해시)

해시는 CSV를 줄 끝(LF)만 맞춘 뒤 잰 sha256입니다. 그림(PNG)은 글꼴·라이브러리 판에 따라 바이트가 달라 비교하지 않습니다. 검증 목록은 두 개입니다.

| 목록 | 무엇 | 개수 | `--check-hash` 에서 |
|---|---|---|---|
| `reports/결과_해시.txt` | git 에 올린(제출하는) 리포트 CSV 전부. S-1 LINK-Sim 24개, T-1 태연 분석 8개, T-2 교차 검증 7개 포함 | 110 | 값이 다르거나, 목록의 파일이 없거나, 목록에 없는 CSV가 `reports/`에 새로 생기면 **실패**(종료 코드 1) |
| `reports/결과_해시_로컬.txt` | 레포에 올리지 않는 로컬 전용 결과: `m1_기대활동모델/예측_전체.csv`(원천 결제건수가 있어 `.gitignore`) | 1 | 파일이 있을 때만 비교, 다르면 실패. 없으면 "건너뜀" (새 체크아웃에는 없음) |

- `--write-hash`는 git 추적 및 무시되지 않은 신규 `reports/**/*.csv`로 검증 목록을 다시 씁니다. 리포트 CSV를 새로 추가했으면 변경을 확인한 뒤 `--write-hash`로 목록에 넣습니다. 목록 밖 CSV는 검사에서 실패하므로 빠뜨릴 수 없습니다(`tests/test_r2_results.py`가 목록 = 추적·무시되지 않은 신규 CSV 인지 확인).
- `reports/재현_기록.csv`(실행 시간)는 컴퓨터마다 달라 해시에서 뺍니다.
- 새 체크아웃에서 문서의 순서로 확인한 결과 (2026-10-14):
  - 원본 없이: `--only w1 w2 m4 s1 check_no_raw --check-hash` → 검증 목록 같음 95, 다름·새 파일·없어짐 0, 로컬 전용 건너뜀 1, 종료 코드 0. (s1 은 `data/interim`이 필요합니다. 원본이 없으면 `--no-raw --check-hash` 로 w1·w2·m4·점검만 돌려도 같은 결과입니다.)
  - `data/interim`이 있을 때: r1·w2·r2·m3 를 다시 만들어도 커밋된 CSV와 같습니다.
- 예전에 `reports/r1_등급규칙/카드_판단변화.csv`는 실행마다 **행 순서**가 바뀌었습니다(파이썬 집합 순서). 지금은 노트북이 도시·집단·기준일·코호트 순으로 정렬해 저장합니다. 정렬 전후 213행의 내용은 같고 순서만 바뀌었습니다.
- 원본(대회 zip)이 필요한 단계: ingest. `data/interim`(원본을 적재한 정리본)이 필요한 단계: eda1~6, m1, m2, r1, r2, m3, s1. 원본 없이 되는 단계: w1, w2, m4, 점검. 원본은 재배포할 수 없어 새 체크아웃에는 없습니다.
- M1 전체(`--only m1`)를 다시 돌리면 로컬 전용 `예측_전체.csv`도 다시 생기고, 그때부터 로컬 목록과 비교합니다.
- `reports/eda5_인사이트재현`, `eda6_사전경보재평가`, `w1_자원원장`의 README 는 노트북 출력 뒤 문구를 손으로 다듬은 판이 커밋돼 있어, 다시 만들면 문장 몇 줄이 다릅니다(숫자·CSV 는 같음).

## 5. 알려진 것

- 등급 규칙·신호·자원을 바꾸면(예: `configs/rules.yaml`) r1 → r2 → m3 를 다시 돌려야 결과표·평가 숫자가 맞습니다. R-2 요약표의 등급 수가 R-1 결과표와 같은지는 `tests/test_r2_results.py`가 확인합니다.
- 자원 원장이 바뀌면 W-2 정답 세트도 바뀌어야 할 수 있습니다. 그때는 정답 규칙(`configs/resource_eval.yaml`)을 먼저 고정·커밋한 뒤 `--relabel`로 정답 세트를 만들고, 그다음 평가합니다.


## 2026-10-18 접근성 재검증

공식 경로당 372곳은 `external/resources/chuncheon_centers/시설목록.csv`와 `주소좌표.csv`에 고정했습니다. `notebooks/w1_resource_ledger.py`를 다시 실행하면 네트워크 없이 원장 1,440건을 만듭니다. 자원 출처와 미확인 운영·좌표 처리는 해당 폴더 README를 따릅니다.

대회 정리본이 있는 환경에서는 W1 → R1 → W2 → M4 → R2 → M3 → S1 순서로 관련 결과를 재계산합니다. 고정된 W2 정답을 보존하기 위해 `--relabel`은 사용하지 않습니다. 보강 전후 감사는 `PYTHONPATH=src python notebooks/a1_access_audit.py`로 다시 만듭니다. 공개 보강 전 원장 스냅샷도 함께 제공했습니다.

사용자 평가는 `evaluation/user_study_20261018/index.html`에서 시작합니다. `응답_양식.csv`는 헤더뿐이며 실제 응답을 넣어야 합니다. `analyze.py`는 빈 응답이나 배정 불일치·중복 응답을 거부합니다. 자료 준비를 실무 검증 완료로 표현하지 않습니다.
