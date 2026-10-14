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
```

- 단계별 성공 여부와 시간은 `reports/재현_기록.csv`에 남습니다.
- 몇 단계만: `--only r2 m3`. 실패해도 계속: `--keep-going`.
- 자원 추천 평가의 정답 세트(`notebooks/w2_labels.py`)는 결과를 보기 전에 고정한 입력이라 기본으로 다시 만들지 않습니다(`--relabel`).

## 3. 단계와 필요한 것

| 순서 | 단계 | 명령 | 필요한 것 | 결과 | 원본 없이 | 걸린 시간 |
|---|---|---|---|---|---|---|
| 1 | ingest | `python -m bigcontest.ingest` | 대회 원본 zip | `data/interim/*.parquet`, `reports/eda0_적재결과` | 안 됨 | (아래 참고) |
| 2 | eda1 | `notebooks/eda1_data_quality.py` | 1 | `reports/eda1_데이터진단` | 안 됨 | {eda1} |
| 3 | eda2 | `notebooks/eda2_telecom_basics.py` | 1 | `reports/eda2_통신기초` | 안 됨 | {eda2} |
| 4 | eda3 | `notebooks/eda3_card_basics.py` | 1 | `reports/eda3_카드기초` | 안 됨 | {eda3} |
| 5 | w1 | `notebooks/w1_resource_ledger.py` | `external/` | `reports/w1_자원원장`, 원장 v2 | **됨** | {w1} |
| 6 | eda4 | `notebooks/eda4_external.py` | 1, eda2 | `reports/eda4_외부데이터결합` | 안 됨 | {eda4} |
| 7 | eda5 | `notebooks/eda5_insights.py` | 1, eda4 | `reports/eda5_인사이트재현` | 안 됨 | {eda5} |
| 8 | eda6 | `notebooks/eda6_alarm_reeval.py` | 1 | `reports/eda6_사전경보재평가` | 안 됨 | {eda6} |
| 9 | m1 | `notebooks/m1_expected_activity.py` | 1 | `reports/m1_기대활동모델`, `configs/models.yaml` decisions | 안 됨 | {m1} |
| 10 | m2 | `notebooks/m2_proxy_task_b.py` | 1 | `reports/m2_대리과제B` | 안 됨 | {m2} |
| 11 | r1 | `notebooks/r1_level_rules.py` | 1 | `reports/r1_등급규칙` | 안 됨 | {r1} |
| 12 | w2 | `notebooks/w2_resource_eval.py` | `external/`, 정답 세트 | `reports/w2_자원추천평가` | **됨** | {w2} |
| 13 | m4 | `notebooks/m4_risk_factors.py` | 공개 시민생활 + eda2·eda4·w1 CSV | `reports/m3_모델링결과/M4.md` | **됨** | {m4} |
| 14 | r2 | `notebooks/r2_risk_results.py` | 1, r1·eda3·eda5·eda6·m1 | `reports/r2_위험도결과` | 안 됨 | {r2} |
| 15 | m3 | `notebooks/m3_models.py` | 1, eda6·m1·m2·r1·w2·m4 | `reports/m3_모델링결과` | 안 됨 | {m3} |
| 16 | 점검 | `scripts/check_no_raw.py` | — | 리포트 CSV 원천 값 열 점검 | **됨** | {check_no_raw} |
| 17 | 시험 | `uv run pytest -q` | — | 원본이 없으면 일부 건너뜀 | 일부 | {pytest} |

- 시간은 {machine} 에서 `scripts/reproduce.py`로 잰 값입니다(적재 제외 합계 약 {total}). 적재(ingest)는 공용 정리본을 덮어쓰지 않으려고 이번에 다시 돌리지 않았습니다.
- 원본 없이도 되는 것: 자원 원장(w1), 자원 추천 평가(w2), 위험 요인 모델(M4), 점검, API·화면(가짜 데이터, `README.md` 시작하기).
- 난수 seed: 경보 주입 실험 `numpy.random.default_rng(0)` (EDA-6·M3 같은 쌍), 합성 정상 시계열 seed 0~4·0~9, M3 하락률·부트스트랩 seed 0 (`configs/models_m3.yaml`), M4 반복 seed 0~49, LightGBM `random_state=0`.

## 4. 결과 확인 (해시)

- `reports/결과_해시.txt`: 리포트 CSV {n_hash}개의 sha256 (줄 끝을 LF로 맞춘 뒤). 그림(PNG)은 글꼴·라이브러리 판에 따라 바이트가 달라 비교하지 않습니다.
- 다시 만든 뒤 `--check-hash`로 비교하면 같은 파일 / 다른 파일 / 새 파일 / 없어진 파일 수를 보여 주고, 다른 파일이 있으면 종료 코드 1입니다.
- 이번 확인: {hash_note}

## 5. 알려진 것

- 등급 규칙·신호·자원을 바꾸면(예: `configs/rules.yaml`) r1 → r2 → m3 를 다시 돌려야 결과표·평가 숫자가 맞습니다. R-2 요약표의 등급 수가 R-1 결과표와 같은지는 `tests/test_r2_results.py`가 확인합니다.
- 자원 원장이 바뀌면 W-2 정답 세트도 바뀌어야 할 수 있습니다. 그때는 정답 규칙(`configs/resource_eval.yaml`)을 먼저 고정·커밋한 뒤 `--relabel`로 정답 세트를 만들고, 그다음 평가합니다.
