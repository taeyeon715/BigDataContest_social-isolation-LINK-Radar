"""EDA-6 사전 경보 재평가

실행: uv run python notebooks/eda6_alarm_reeval.py   (EDA-5를 먼저 실행하면 EDA-5 값과 나란히 보여 줌)
결과: reports/eda6_사전경보재평가/ (README.md, figs/, 경보_목록.csv, 경보_기준일카드.csv, 탐지_결과.csv, 도시전체주입.csv)

EDA-5의 경보 성능(15% 하락 탐지 87%)은 6개월 전체로 기준값을 만든 사후 계산이었다.
여기서는 날짜 t의 판정에 t까지의 자료만 쓰는 방식(src/bigcontest/signals/alarm.py)으로 다시 평가한다.
이 판에서 보완한 것: 회복 3가지 구분, 기준일 카드, 지연 설명, 보류 회계, 세 값 조합, 입력 계약.
"""

import numpy as np
import pandas as pd

from bigcontest.card import AGE_ORDER, daily, load_card2
from bigcontest.config import ROOT, load_config
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report
from bigcontest.stats import cluster_rate_ci, fmt_rate_ci
from bigcontest.signals.alarm import (
    REC_LOCAL,
    AlarmConfig,
    Panel,
    episode_count,
    null_accounting,
    null_summary,
    records,
    synthetic_normal,
)
from bigcontest.weather import load as load_weather

HOL = [pd.Timestamp(k) for k in load_config()["holidays"]]
CITIES = ["강남", "춘천"]
DROPS = [0.10, 0.15, 0.20]
SMALL_DROPS = [0.02, 0.03, 0.05]  # 하락 폭별 감지율(최소 감지 하락) 보완
ALL_DROPS = SMALL_DROPS + DROPS
INJ_LEN, N_START, SUCCESS_DAYS = 14, 30, 14
N_CITY_START = 20  # 도시 전체 주입 실험의 도시당 시작일 수
KEYS = ["city", "age_g", "SEX_CCD"]
rng = np.random.default_rng(0)
cfg = AlarmConfig()

r = Report("eda6_사전경보재평가", "EDA-6 사전 경보 재평가")
r.p("EDA-5의 경보 성능은 6개월 전체로 평소 값을 만든 **사후 계산**이었습니다. 그래서 '그날 미리 알 수 있었나'를 보여 주지 못합니다.")
r.p("여기서는 날짜 t의 판정에 **t까지의 자료만** 씁니다. 같은 규칙을 3A 모델·3B 규칙·에이전트가 다시 쓰도록 `src/bigcontest/signals/alarm.py`에 두었습니다.")
r.p("이 판에서 보완한 것: 연휴 뒤 회복을 세 가지로 나눔, 기준일 카드와 사후 목록 분리, 지연 설명, 보류 회계, '둘 중 하나' 세 값 조합, 입력 계약.")
r.p()
r.p("**용어**")
r.p()
r.p("| 용어 | 뜻 |")
r.p("|---|---|")
r.p("| absolute 경로 | 코호트 결제가 그 코호트의 평소보다 실제로 줄었나 |")
r.p("| local 경로 | 같은 날 도시 전체 변화(`city_shock`)를 빼고도 그 코호트만 벗어났나 |")
r.p("| city_shock 경로 | 도시 코호트들의 공통 변화 자체에 같은 창 규칙을 적용한 도시 단위 경보 |")
r.p("| alarm_date | 경보를 판정한 날 = 7일 창의 **마지막 관측일**. 창은 `window_start`~`window_end` |")
r.p("| 보류 | 점수나 경보를 만들 수 없어 판정하지 않은 날(학습 기간 부족, 창 안 유효 점수 부족, MAD=0, 미공급 등). 경보 없음(0)과 다름 |")
r.p("| 배경 경보율 | 실제 자료에서 아무것도 넣지 않았을 때 경보가 난 비율. 그 기간에 진짜 이상이 없었는지 모르므로 **오경보율이 아님** |")
r.p("| 오경보율 | 이상이 없도록 만든 합성 시계열에서 경보가 난 비율 |")
r.p("| 순증 경보율 차이 | 같은 (도시, 연령, 성, 시작일) 쌍에서 '주입했을 때 경보율 − 주입 안 했을 때 경보율'. 확률 차이라 **%p**로 적고 전이 표와 함께 봄 |")
r.p("| 사후 목록 | 자료 끝(12/31)까지 본 뒤 정리한 경보 목록. 회복 검토가 경보일 뒤 자료를 씀 |")
r.p("| 기준일 카드 | 그날(as_of)까지의 자료만으로 다시 계산한, 그날 화면에 보였을 경보 상태 |")

# ------------------------------------------------------------ 0. 자료
c2 = load_card2()
base3 = daily(c2, KEYS)
wide = base3.pivot_table(index="date", columns=KEYS, values="USE_CNT").astype(float)
cols = list(wide.columns)
dates = wide.index
n = len(dates)
w = load_weather()
rain = w.pivot_table(index="date", columns="city", values="rain")
p0 = Panel(wide, HOL, cfg)  # 입력 계약(check_panel_input)을 통과해야 만들어짐
n_zero = int((wide == 0).sum().sum())
rain_missing = int(rain.reindex(dates).isna().sum().sum())

r.h("1. 방법")
r.p(f"- 자료: 카드 데이터2, 같은 시도 거주 고객, 본사등록 업종 제외. 코호트 = 도시 × 연령(20대~80대+) × 성 = {len(cols)}개, {n}일")
r.p(f"- 평소 값(기대값): t보다 앞선 **같은 요일** 값의 평균(최근 {cfg.lookback}일, 공휴일 제외). 잔차 e(t) = log(결제+1) − 기대값")
r.p(f"- 점수: e(t)를 t보다 앞선 {cfg.lookback}일(공휴일 제외)의 중앙값·MAD로 나눈 robust z. 처음 {cfg.min_train}일은 학습 기간이라 평가하지 않음")
r.p(f"- 경보: alarm_date = t, 창 [t−6, t]에서 z < {cfg.z_thr}인 날이 {cfg.k}일 이상. 창 안에 점수가 있는 날이 {cfg.min_valid}일 미만이면 보류")
r.p("- 두 경로: absolute(e 자체), local(e − 같은 날 같은 도시 코호트 e의 중앙값 = `city_shock`). 도시 공통 변화는 버리지 않고 `city_shock` 신호와 도시 경보로 따로 저장")
r.p("- '둘 중 하나' 경로는 세 값 논리입니다: 한 경로라도 1이면 1, 두 경로 모두 판정되고 0이면 0, 나머지((0, 보류)·(보류, 보류))는 보류. 한 경로만 본 0을 '경보 없음'으로 세지 않습니다.")
r.p("- MAD가 0이거나 과거 관측일이 부족하면 작은 값을 더해 억지로 점수를 만들지 않고 NULL + 보류 사유를 남깁니다.")
r.p("- 같은 코호트에서 7일 안에 다시 울린 경보는 한 건으로 묶습니다(중복 제거).")
r.p(f"- **입력 계약**: 점수를 만들기 전에 날짜 정렬·중복 없음·하루 간격 연속·값이 유한하고 음수 아님을 검사합니다(`check_panel_input`). 이번 입력({n}일 × {len(cols)}코호트)은 통과했습니다. 빈 칸(NaN)은 '미공급'으로 받아 그날을 보류하고 0건으로 바꾸지 않습니다.")
r.p(f"- **카드 0건 가정**: 카드 `daily()`는 결제 행이 없는 (날짜, 코호트)를 0건으로 채웁니다. 이번 표에서 0인 칸은 {n_zero}개입니다. 제공기관이 '거래 0'과 '미공급'을 구분해 주는지 확인하기 전까지 이 0은 가정입니다.")
r.p(f"- **강수 자료**: ASOS 행은 있는데 강수량이 빈 날은 기상청 정의대로 무강수(0)입니다. 행 자체가 없는 날은 무강수가 아니라 '모름'으로 둡니다(이번 자료에서 빠진 행 {rain_missing}개).")

ns = null_summary(p0)
ns["전체 코호트·일 중"] = ns["코호트·일"] / (len(cols) * n)
na = null_accounting(p0)
first_ok = na.loc["첫 경보 판정 가능일", "local"]
r.h3("보류된 코호트·일")
r.p("점수(z)를 못 만든 사유와, z는 있는데 경보창 판정을 못 한 사유를 따로 셉니다.")
r.table(ns.set_index("path"), {"단계": "{}", "사유": "{}", "코호트·일": "{:,}", "전체 코호트·일 중": "{:.1%}"})
na_t = na.drop(index="첫 경보 판정 가능일").astype(int)
na_t.index.name = "항목 (코호트·일)"
for path in ("absolute", "local"):
    na_t[f"{path} 비율"] = na_t[path] / na_t.loc["전체", path]
r.table(na_t, {"absolute": "{:,.0f}", "local": "{:,.0f}", "absolute 비율": "{:.2%}", "local 비율": "{:.2%}"})
r.p(f"- '학습 기간 부족'은 처음 {cfg.min_train}일입니다. 그 뒤에도 창 안 유효 점수 {cfg.min_valid}일이 쌓일 때까지 {cfg.min_valid - 1}일은 z가 있어도 경보를 판정하지 않습니다.")
r.p(f"- 그래서 **첫 경보 판정 가능일은 {first_ok}** 입니다. 경보율의 분모는 이 '경보 판정 가능 코호트·일'입니다.")

# ------------------------------------------------------------ 2. 실제 경보 목록
r.h("2. 실제 자료에서 울린 경보")
META = {
    "measured_grain": "가맹점 시군구 × 일 × 고객 거주 시도 × 성 × 연령",
    "interpretation_status": "계산 재현",
}


def add_meta(rec: pd.DataFrame) -> pd.DataFrame:
    rec = rec.copy()
    rec["age_g"] = rec["cohort"].str.split(" ").str[0]
    rec["sex"] = rec["cohort"].str.split(" ").str[1]
    rec["merchant_region"] = rec["city"].map({"강남": "강남구", "춘천": "춘천시"})
    rec["customer_residence"] = rec["city"].map({"강남": "서울", "춘천": "강원"})
    rec["analysis_cohort"] = rec["age_g"]
    for k, v in META.items():
        rec[k] = v
    return rec


rec = add_meta(records(p0, rain=rain))
n_rec = len(rec)
REV_ORDER = ["통과", "확인 필요", "보류", "검토 중", "기각"]
by_path = rec.groupby(["path", "review"]).size().unstack(fill_value=0).reindex(columns=[c for c in REV_ORDER if c in set(rec["review"])])
by_path.index.name = "경로(묶음 안에서 울린 경로 전체)"
r.p(f"같은 코호트에서 7일 안에 다시 울린 경보를 한 묶음으로 보면 {n_rec}건입니다. `path`는 묶음 안에서 한 번이라도 울린 경로 전체, `path_first`는 첫 경보일의 경로입니다.")
r.p("아래는 자료 끝까지 본 **사후 목록**입니다. 경보 당일 화면에 보였을 상태는 바로 뒤 '기준일 카드'에 따로 적었습니다.")
r.table(by_path, {c: "{:,}" for c in by_path.columns})
ep_path = {path: sum(episode_count(p0.res[path]["alarm"][:, j]) for j in range(len(cols))) for path in ("absolute", "local")}
r.p(f"- 경로마다 따로 묶어 센 건수: absolute {ep_path['absolute']}건, local {ep_path['local']}건 (두 경로가 같은 묶음에 들어 있을 수 있어 합이 {n_rec}건보다 큼)")
ep_len = (pd.to_datetime(rec["episode_end"]) - rec["alarm_date"]).dt.days + 1
r.p(f"- **'7일 묶음'은 사건 길이가 7일이라는 뜻이 아닙니다.** 7일 안 재경보를 계속 이어 붙이므로 첫 경보일~마지막 경보일이 최대 {ep_len.max()}일인 묶음이 있습니다(7일 넘는 묶음 {int((ep_len > 7).sum())}건).")
long_ep = rec[rec["episode_near_holiday"] & ~rec["near_holiday"]]
long_txt = ", ".join(f"{a} {b} {c:%m/%d}~{d:%m/%d}" for a, b, c, d in long_ep[["city", "cohort", "alarm_date", "episode_end"]].itertuples(index=False))
r.p("- **반증·강수·방향 검토는 묶음의 첫 창 기준입니다.** 묶음이 길게 이어져 뒤쪽에서 공휴일을 넘는 경우, 뒤쪽 공휴일은 반증에 반영되지 않습니다"
    + (f": {long_txt}" if len(long_ep) else "") + ". 첫 창 검토 결론을 묶음 전체로 넓혀 읽지 않습니다.")

r.h3("공휴일 반증 규칙")
r.p("공휴일 앞뒤 1일에 걸린 창은 '반증 검토 시작'일 뿐 바로 기각하지 않습니다. 대상 코호트의 공휴일 기대값(창 이전 공휴일의 local 잔차 평균), 도시 공통요인, 연휴 뒤 3일(공휴일 아닌 날)의 회복을 봅니다. 회복은 세 가지로 나눕니다.")
r.p()
r.p("| 칸 | 뜻 |")
r.p("|---|---|")
r.p("| `recovery_abs` | 대상 코호트가 **평소 수준**으로 돌아왔나 (absolute 잔차 평균 ≥ −0.05) |")
r.p("| `recovery_local` | 도시 대비 **추가 이탈이 없나** (local 잔차 평균 ≥ −0.05). 도시 전체가 계속 낮아도 '추가 이탈 없음'일 수 있음 |")
r.p("| `recovery_common` | 도시 공통요인이 돌아왔나 |")
r.p()
r.p("| 반증 결과 | 조건 | 결정 시점 |")
r.p("|---|---|---|")
r.p("| 통과 | 공휴일 기대값·공통요인을 빼도 경보 조건이 남음 | alarm_date |")
r.p("| 기각 | 설명되고, 연휴 뒤 **평소 수준으로 회복**했고 도시 대비 추가 이탈도 없음 | 회복 확인일 |")
r.p("| 확인 필요 (도시 공통 변화) | 도시 대비 추가 이탈은 없지만 연휴 뒤에도 평소보다 낮음. 다른 코호트도 함께 낮다는 설명일 뿐 **공휴일이 원인이라는 증거가 아님** → 기각하지 않고 `city_common_flag`로 남겨 원인·접근성 확인 | 회복 확인일 |")
r.p("| 보류 | 설명되나 대상 코호트가 도시 대비로도 회복하지 않음 | 회복 확인일 |")
r.p("| 검토 중 | 설명되나 연휴 뒤 회복을 아직 관측하지 못함 | 정해지지 않음 |")
r.p()
r.p("- 정의된 '기대 연휴 경로'가 아직 없어서, 기각은 평소 수준(absolute) 회복으로만 판단합니다.")

# 이전 규칙(회복 = local만)과 비교: 같은 칸으로 다시 계산
old_rule = np.select(
    [rec["review"] == "통과", ~rec["near_holiday"], rec["recovery_local"] == REC_LOCAL[0]],
    ["통과", "통과", "기각"], default="보류")
rec["review_old_rule"] = old_rule
hol_rec = rec[rec["near_holiday"]]
cmp_old = pd.crosstab(rec["review_old_rule"], rec["review"]).reindex(columns=[c for c in REV_ORDER if c in set(rec["review"])], fill_value=0)
cmp_old.index.name = "이전 규칙 (local 회복만) → 새 규칙"
old_cnt = rec["review_old_rule"].value_counts()
new_cnt = rec["review"].value_counts()
r.h3("이전 규칙과 비교")
r.p(f"이전 규칙은 연휴 뒤 회복을 local 잔차로만 봤습니다(이전 결과: 통과 {old_cnt.get('통과', 0)} · 기각 {old_cnt.get('기각', 0)} · 보류 {old_cnt.get('보류', 0)}). 같은 경보에 새 규칙을 적용하면:")
r.table(cmp_old, {c: "{:,}" for c in cmp_old.columns})
moved = rec[(rec["review_old_rule"] == "기각") & (rec["review"] == "확인 필요")]
r.p(f"- 이전에 '기각/회복'이던 {int((rec['review_old_rule'] == '기각').sum())}건 중 **{len(moved)}건**은 도시 대비 추가 이탈은 없었지만 연휴 뒤 3일 absolute 잔차 평균이 "
    f"{moved['recovery_abs_mean'].max():+.3f} ~ {moved['recovery_abs_mean'].min():+.3f}로 평소보다 낮았습니다. 그래서 '확인 필요(도시 공통 변화)'로 옮겼습니다. "
    f"평소 수준까지 회복한 {int(((rec['review_old_rule'] == '기각') & (rec['review'] == '기각')).sum())}건만 기각으로 남습니다.")
r.p(f"- 새 규칙 결과: " + " · ".join(f"{k} {new_cnt.get(k, 0)}" for k in REV_ORDER if new_cnt.get(k, 0)) + f" (모두 {n_rec}건)")

if len(hol_rec):
    t = hol_rec[["city", "cohort", "alarm_date", "path", "drop_total", "explained_common", "explained_holiday", "remaining",
                 "recovery_abs_mean", "recovery_local_mean", "review"]].copy()
    t["alarm_date"] = t["alarm_date"].dt.date
    t = t.rename(columns={"cohort": "코호트", "drop_total": "하락(전체)", "explained_common": "공통요인", "explained_holiday": "공휴일 기대값",
                          "remaining": "남은 이탈", "recovery_abs_mean": "연휴 뒤 absolute", "recovery_local_mean": "연휴 뒤 local", "review": "반증 결과"})
    r.h3("공휴일 인접 경보: 하락을 무엇이 설명하나")
    r.p("하락 칸은 창 7일의 평균 하락 크기(log 차이, 0.10 ≈ 10% 하락)입니다. 양수 = 하락, 음수 = 그 요인은 오히려 반대 방향. 하락(전체) = 공통요인 + 공휴일 기대값 + 남은 이탈.")
    r.p("'연휴 뒤' 두 칸은 연휴 뒤 3일의 잔차 평균(음수 = 평소·도시보다 낮음)입니다. −0.05보다 낮으면 회복하지 않은 것으로 봅니다.")
    r.table(t.set_index("city"), {"코호트": "{}", "alarm_date": "{}", "path": "{}", "하락(전체)": "{:+.2f}", "공통요인": "{:+.2f}",
                                   "공휴일 기대값": "{:+.2f}", "남은 이탈": "{:+.2f}", "연휴 뒤 absolute": "{:+.2f}", "연휴 뒤 local": "{:+.2f}", "반증 결과": "{}"})
    hc = hol_rec["review"].value_counts()
    r.p(f"- 공휴일 인접 경보 {len(hol_rec)}건: " + " · ".join(f"{k} {hc.get(k, 0)}" for k in REV_ORDER if hc.get(k, 0)) + ".")
    r.p("- 추석 무렵 경보 다수는 도시 대비 추가 이탈이 없었지만 연휴 뒤에도 도시 전체가 평소보다 낮았습니다. 이것은 '공휴일 때문'이라는 증거가 아니라 도시 공통 변화이므로 기각하지 않고 확인 대상으로 남깁니다.")
    r.p("- 2025년 하반기에 앞선 공휴일은 8/15뿐이라 '공휴일 기대값'은 공휴일 하루~이틀로 만든 거친 값입니다.")

# 기준일 카드
r.h3("기준일 카드: 경보 당일 화면에 보였을 상태")
cards = []
for d in sorted(rec["alarm_date"].unique()):
    c_ = records(p0, rain=rain, as_of=d)
    cards.append(c_[c_["alarm_date"] == d])
card = add_meta(pd.concat(cards, ignore_index=True))
key = ["city", "cohort", "alarm_date"]
both_v = rec[key + ["review"]].merge(card[key + ["review"]], on=key, suffixes=("_post", "_card"))
assert len(both_v) == n_rec
ct = pd.crosstab(both_v["review_card"], both_v["review_post"]).reindex(columns=[c for c in REV_ORDER if c in set(rec["review"])], fill_value=0)
ct.index.name = "경보 당일 카드 → 사후 목록"
late_dec = int((pd.to_datetime(rec["review_decided_at"]) > rec["alarm_date"]).sum())
late_rec = int((pd.to_datetime(rec["recovery_observed_at"]) > rec["alarm_date"]).sum())
late_ep = int((pd.to_datetime(rec["episode_end"]) > rec["alarm_date"]).sum())
r.p("`records(panel, as_of=날짜)`는 패널을 그날까지 잘라 다시 계산합니다. 그날 뒤 결제·날씨는 쓰지 않으므로, 회복을 아직 못 본 검토는 **'검토 중 / 회복 미관측'** 으로, 묶음 끝·경로는 그날까지 본 값으로 나옵니다.")
r.table(ct, {c: "{:,}" for c in ct.columns})
r.p(f"- 사후 목록 {n_rec}행 중 연휴 뒤 회복을 alarm_date 뒤에 관측한 행 {late_rec}건, 반증 결론이 alarm_date 뒤에 난 행 {late_dec}건, 묶음이 alarm_date 뒤로 이어진 행 {late_ep}건입니다. "
    "(이전 판은 '통과'도 회복 관측일에 결정한 것으로 적어 31건이었습니다. 이제 '통과'는 alarm_date에 결정됩니다.)")
r.p("- **사후 목록의 기각·확인 필요·보류를 경보 당일 상태로 읽지 않습니다.** 3A·3B·에이전트는 기준일 카드(`as_of`)를 씁니다.")
r.p("- 시간 칸을 나눴습니다: `trained_until`(기대값·MAD에 쓴 마지막 날 = alarm_date−1), `evidence_observed_to`(경보 근거 마지막 관측일 = alarm_date, 같은 날 도시 공통요인 포함), "
    "`review_observed_to`(반증 검토에 쓴 마지막 관측일), `availability_basis`(카드 공급일 미확인이라 `unknown`). 실제 공급 지연은 반영하지 않은 관측일 기준 재생입니다.")

n_rain = int((rec["rain_explained"] == True).sum())  # noqa: E712
n_rain_f = int((rec["rain_explained"] == False).sum())  # noqa: E712
n_rain_na = int(rec["rain_explained"].isna().sum())
r.h3("강수")
r.p(f"- 비(ASOS 일강수 1mm 이상) 효과는 과거 자료로만 추정합니다(창 이전 {cfg.lookback}일의 '비 온 날 − 안 온 날' 잔차 차이). 경보마다 **날씨로 설명되는 부분**과 **보정 후 남는 이탈**을 함께 적었습니다(`weather_explained_*`, `after_weather_*`).")
r.p(f"- 비 효과를 빼면 경보 조건을 넘지 않는 경보: **{n_rain}건 / {n_rec}건**. 이 경보들도 기각하지 않고 그대로 둡니다. 비 때문에 장보기가 어려웠던 것도 확인할 이유가 될 수 있습니다.")
r.p(f"- 비를 빼도 경보 조건이 남음 {n_rain_f}건, 판정 못 함 {n_rain_na}건. 비 효과를 추정할 과거(비 온 날·안 온 날 각 3일 이상)가 모자라거나 창 안 강수 행이 없으면 설명량을 0으로 두지 않고 NULL + 사유(`weather_status`) + 표본 수(`rain_n_wet`, `rain_n_dry`)를 남깁니다.")
r.p("- `rain_explained=False`는 '비가 설명하지 못함'의 증거가 아닙니다. 관측소 한 곳(서울 108, 춘천 101)과 과거 평균 효과로 본 값일 뿐입니다.")
r.p("- 강수 보정으로 경보 수가 줄어드는 것은 '통계적 경보일 감소'일 뿐, 오탐이 줄었다는 뜻이 아닙니다.")
dirs = rec.assign(direction=rec["direction"].fillna("판정 못 함(보류)")).groupby(["path_first", "direction"]).size().unstack(fill_value=0)
dirs.index.name = "첫 경보일 경로"
r.h3("방향")
r.p("경보를 만든 낮은 날들의 **절대 변화 방향**입니다. local 경로에서 '도시보다 덜 늘어난' 경우는 감소로 부르지 않습니다.")
r.table(dirs, {c: "{:,}" for c in dirs.columns})
out_cols = ["view", "as_of", "city", "age_g", "sex", "alarm_date", "window_start", "window_end", "observed_from", "observed_to", "episode_end", "n_alarm_days",
            "path", "path_first", "low_days_abs", "low_days_local", "mean_change_abs", "city_shock_mean", "mean_change_local",
            "change_abs_low_days", "direction", "direction_note", "near_holiday", "episode_near_holiday", "review_scope", "review", "review_reason", "review_decided_at",
            "drop_total", "explained_common", "explained_holiday", "remaining",
            "recovery_abs", "recovery_local", "recovery_common", "recovery_abs_mean", "recovery_local_mean", "recovery_common_mean", "recovery_observed_at", "city_common_flag",
            "rain_days_in_window", "rain_missing_in_window", "rain_n_wet", "rain_n_dry", "weather_explained_abs",
            "after_weather_abs", "weather_explained_local", "after_weather_local", "rain_explained", "weather_status", "measured_grain",
            "merchant_region", "customer_residence", "analysis_cohort", "trained_until", "evidence_observed_to", "review_observed_to",
            "availability_basis", "availability_note", "interpretation_status"]
rec[out_cols].to_csv(r.dir / "경보_목록.csv", index=False, encoding="utf-8-sig")
card[out_cols].to_csv(r.dir / "경보_기준일카드.csv", index=False, encoding="utf-8-sig")
r.p(f"전체 목록: [경보_목록.csv](경보_목록.csv) (**사후 목록**, {n_rec}행, `view` 칸에 표시) · [경보_기준일카드.csv](경보_기준일카드.csv) (같은 경보의 **경보 당일 기준일 카드**, {len(card)}행)")
cs = pd.concat({c_: p0.city_signal[c_] for c_ in CITIES}, names=["city", "date"]).reset_index()
cs.to_csv(r.dir / "도시공통변화.csv", index=False, encoding="utf-8-sig")
cs_n = cs[cs["city_shock_z"] < cfg.z_thr].groupby("city").size()
cs_a = cs[cs["city_alarm"] == 1].groupby("city").size()
r.p(f"도시 공통 변화(`city_shock`, 같은 날 같은 도시 코호트 잔차의 중앙값)는 버리지 않고 [도시공통변화.csv](도시공통변화.csv)에 날짜별 값·z·도시 경보로 저장했습니다. "
    f"z < {cfg.z_thr}인 날: 강남 {cs_n.get('강남', 0)}일, 춘천 {cs_n.get('춘천', 0)}일. 같은 창 규칙의 도시 경보일: 강남 {cs_a.get('강남', 0)}일, 춘천 {cs_a.get('춘천', 0)}일.")

# ------------------------------------------------------------ 3. 배경 경보율
r.h("3. 배경 경보율 (실제 자료, 주입 없음)")
cand = [s for s in range(cfg.min_train + cfg.win, n - INJ_LEN + 1) if not p0.near_hol[s:s + INJ_LEN].any()]
r.p(f"평가창 = 공휴일 앞뒤가 끼지 않은 14일 구간({len(cand)}개 시작일). 경보는 local 경로 기준이고 absolute·둘 중 하나는 뒤 표에 함께 적었습니다.")


def window_outcome(alarm: np.ndarray, s: int) -> tuple[int, int, float]:
    """(주입 전 경보 중, 창 안 경보, 첫 경보까지 일수)."""
    pre = int(alarm[s - 1] == 1)
    win_ = alarm[s:s + SUCCESS_DAYS]
    hit = np.where(win_ == 1)[0]
    return pre, int(len(hit) > 0), (float(hit[0]) if len(hit) else np.nan)


def background(p: Panel, path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(평가창별 결과, 코호트별 경보일 수·경보 판정 가능일 수)."""
    wins, days = [], []
    for j, col in enumerate(p.cols):
        a = p.alarm_any(j, path)
        for s in cand:
            pre, hit, _ = window_outcome(a, s)
            wins.append((*col, s, pre, hit))
        ev = ~np.isnan(a)
        days.append((*col, int((a[ev] == 1).sum()), int(ev.sum())))
    return (pd.DataFrame(wins, columns=KEYS + ["s", "pre", "hit"]),
            pd.DataFrame(days, columns=KEYS + ["alarm_days", "eval_days"]))


def bg_table(p: Panel, path: str) -> pd.DataFrame:
    """평가창당·코호트·일당 경보율과 중복 제거 건수 (도시 × 연령)."""
    win_, day = background(p, path)
    g = win_.groupby(["city", "age_g"])
    t_ = pd.DataFrame({
        "평가창 수": g.size(),
        "평가창 중 경보 있음": g["hit"].mean(),
        "주입 전부터 경보 중인 창": g["pre"].mean(),
        "새로 경보 난 창": g.apply(lambda x: x.loc[x["pre"] == 0, "hit"].mean(), include_groups=False),
    })
    day["episodes"] = [episode_count(p.alarm_any(p.cols.index(tuple(r_)), path)) for r_ in day[KEYS].itertuples(index=False)]
    dd = day.groupby(["city", "age_g"])[["alarm_days", "eval_days", "episodes"]].sum()
    t_["경보일 / 코호트·일"] = dd["alarm_days"] / dd["eval_days"]
    t_["경보 건수(중복 제거) / 코호트·100일"] = dd["episodes"] / dd["eval_days"] * 100
    return t_.reindex(pd.MultiIndex.from_product([CITIES, AGE_ORDER], names=["city", "age_g"]))


BG_FMT = {"age_g": "{}", "평가창 수": "{:,.0f}", "평가창 중 경보 있음": "{:.1%}", "주입 전부터 경보 중인 창": "{:.1%}",
          "새로 경보 난 창": "{:.1%}", "경보일 / 코호트·일": "{:.2%}", "경보 건수(중복 제거) / 코호트·100일": "{:.2f}"}
bg = bg_table(p0, "local")
r.table(bg.reset_index().set_index("city"), BG_FMT)
r.p("- **분모**: 평가창 = (코호트 × 14일 창) 중 창 안에 경보가 하나라도 있는 비율. '주입 전부터 경보 중'은 창 시작 전날 이미 경보가 켜져 있던 창, '새로 경보 난 창'은 그런 창을 뺀 나머지 중 경보가 난 비율입니다.")
r.p(f"- 코호트·일 = 경보가 켜진 날 / **경보 판정 가능 코호트·일**(경보가 보류가 아닌 날, {first_ok}부터, 공휴일 무렵 포함). 중복 제거 건수 = local 경로에서 7일 안 재경보를 한 건으로 셈. 평가창은 공휴일 앞뒤를 빼서 두 값이 다를 수 있습니다.")
r.p("- **겹치는 창 주의**: 14일 창들은 같은 184일 시계열에서 하루씩 밀어 만든 것이라 서로 겹칩니다. 창 수를 독립 표본 수로 보지 않습니다. 그래서 신뢰구간 대신 도시·연령별 값을 그대로 보고합니다.")

# ------------------------------------------------------------ 4. 짝지은 주입 실험
r.h("4. 짝지은 하락 주입 실험 (코호트 하나)")
pairs = [(col, int(s)) for col in cols for s in sorted(rng.choice(cand, N_START, replace=False))]
pre_hol = sum(bool(p0.near_hol[s - (cfg.win - 1):s].any()) for _, s in pairs)
r.p(f"(도시, 연령, 성, 시작일) 목록을 **한 번만** 뽑아 고정했습니다(코호트마다 시작일 {N_START}곳, 모두 {len(pairs):,}쌍). 같은 목록에 대조(주입 없음)와 10/15/20% 하락 주입을 모두 적용합니다. 시작 전날 이미 경보 중이던 쌍은 평가에서 빼고 따로 셉니다.")
r.p(f"- 주입: 시작일부터 {INJ_LEN}일 동안 **대상 코호트 하나**의 결제건수에 (1 − 하락률)을 곱함. 기대값·MAD는 과거 자료만 쓰므로, 주입된 날이 쌓이면 뒤쪽 기준값도 같이 내려갑니다(실제 운영과 같음).")
r.p(f"- 공휴일 제외는 **주입 구간 {INJ_LEN}일(앞뒤 1일 포함)에만** 적용했습니다. 첫 판정 창에 들어가는 시작 전 {cfg.win - 1}일은 제외 조건이 아닙니다. 그 6일에 공휴일 앞뒤가 걸린 쌍: {pre_hol}쌍 / {len(pairs)}쌍.")
r.p(f"- 성공(탐지): 대조에서는 {SUCCESS_DAYS}일 안에 경보가 없었는데(0) 주입하면 경보가 난(1) 쌍. 탐지율 = 0→1 / (0→0 + 0→1). 대조에서도 울린 쌍(1→1)은 탐지로 세지 않습니다.")
r.p(f"- 지연: 시작일부터 첫 경보의 alarm_date까지 일수. 새 하락만으로 낮은 날 {cfg.k}일이 모두 쌓이면 최소 {cfg.k - 1}일이지만, **시작 직전 창에 이미 낮은 날이 있으면 0~1일도 가능**합니다(창이 시작 전 날짜를 포함하므로). 미래 누출이 아니라 기존 징후에 주입이 더해진 정상 동작입니다.")


def pre_low_days(p: Panel, path: str = "local") -> dict:
    """(코호트, 시작일) → 시작 직전 win−1일(첫 판정 창에 들어가는 날) 중 z < 기준인 날 수. 대조 자료 기준."""
    out = {}
    for col, s in pairs:
        z = p.res[path]["z"][s - (cfg.win - 1):s, p.cols.index(col)]
        out[(*col, s)] = int(np.nansum(z < cfg.z_thr))
    return out


def run_experiment(p: Panel) -> pd.DataFrame:
    rows = []
    for col, s in pairs:
        j = p.cols.index(col)
        y0 = wide[col].to_numpy()
        ctrl = {path: p.alarm_any(j, path) for path in ("absolute", "local", "either")}
        for drop in [0.0] + DROPS:
            if drop == 0:
                al = ctrl
            else:
                y = y0.copy()
                y[s:s + INJ_LEN] *= 1 - drop
                al = p.score_one(col, y)
            for path in ("absolute", "local", "either"):
                pre, hit, delay = window_outcome(al[path], s)
                rows.append((*col, s, drop, path, pre, hit, delay))
    return pd.DataFrame(rows, columns=KEYS + ["start", "drop", "path", "pre", "hit", "delay"])


ex = run_experiment(p0)
PRE_LOW = pre_low_days(p0)


def paired(ex: pd.DataFrame, path: str, drop: float) -> pd.DataFrame:
    a = ex[(ex["path"] == path) & (ex["drop"] == 0)].set_index(KEYS + ["start"])
    b = ex[(ex["path"] == path) & (ex["drop"] == drop)].set_index(KEYS + ["start"])
    out = pd.DataFrame({"pre": a["pre"], "ctrl": a["hit"], "inj": b["hit"], "delay": b["delay"]})
    out["pre_low"] = [PRE_LOW[k] for k in out.index]
    return out


def transitions(pr: pd.DataFrame) -> dict:
    """시작 전 경보 중이 아닌 쌍만으로 전이 표와 탐지율. pr: pre, ctrl, inj, delay."""
    new = pr[pr["pre"] == 0]
    c00 = int(((new["ctrl"] == 0) & (new["inj"] == 0)).sum())
    c01 = int(((new["ctrl"] == 0) & (new["inj"] == 1)).sum())
    c10 = int(((new["ctrl"] == 1) & (new["inj"] == 0)).sum())
    c11 = int(((new["ctrl"] == 1) & (new["inj"] == 1)).sum())
    return {
        "탐지": c01 / (c00 + c01) if (c00 + c01) else np.nan,
        "대조 경보": new["ctrl"].mean(),
        "순증 경보율 차이": new["inj"].mean() - new["ctrl"].mean(),
        "0→0": c00, "0→1": c01, "1→0": c10, "1→1": c11,
        "평가 쌍(시작 전 경보 없음)": len(new), "대조 음성(0→0+0→1)": c00 + c01, "시작 전부터 경보 중(제외)": int((pr["pre"] == 1).sum()),
        "지연 중앙값(일)": new.loc[(new["ctrl"] == 0) & (new["inj"] == 1), "delay"].median(),
    }


ORDER = ["10% 탐지", "15% 탐지", "20% 탐지", "대조 경보", "순증 경보율 차이", "0→0", "0→1", "1→0", "1→1",
         "평가 쌍(시작 전 경보 없음)", "대조 음성(0→0+0→1)", "시작 전부터 경보 중(제외)", "지연 중앙값(일)"]


def summarize(ex: pd.DataFrame, path: str) -> pd.DataFrame:
    out = []
    for (cty, age), _ in ex.groupby(["city", "age_g"]):
        row = {"city": cty, "age_g": age}
        for drop in DROPS:
            tr = transitions(paired(ex, path, drop).xs((cty, age), level=["city", "age_g"]))
            row[f"{int(drop * 100)}% 탐지"] = tr["탐지"]
            if drop == 0.15:
                row.update({k: v for k, v in tr.items() if k != "탐지"})
        out.append(row)
    return pd.DataFrame(out).set_index(["city", "age_g"])[ORDER].reindex(pd.MultiIndex.from_product([CITIES, AGE_ORDER], names=["city", "age_g"]))


def pp(df: pd.DataFrame, col: str = "순증 경보율 차이") -> pd.DataFrame:
    """표시용: 확률 차이를 %p 숫자로."""
    d = df.copy()
    d[col] = d[col] * 100
    return d


det = summarize(ex, "local")
fmt_det = {"age_g": "{}", "10% 탐지": "{:.0%}", "15% 탐지": "{:.0%}", "20% 탐지": "{:.0%}", "대조 경보": "{:.0%}", "순증 경보율 차이": "{:+.0f}%p",
           "0→0": "{:,.0f}", "0→1": "{:,.0f}", "1→0": "{:,.0f}", "1→1": "{:,.0f}", "평가 쌍(시작 전 경보 없음)": "{:,.0f}", "대조 음성(0→0+0→1)": "{:,.0f}",
           "시작 전부터 경보 중(제외)": "{:,.0f}", "지연 중앙값(일)": "{:.0f}"}
all15 = transitions(paired(ex, "local", 0.15))


def cohort_counts(pr: pd.DataFrame) -> pd.DataFrame:
    """코호트별 (새 탐지 0→1, 대조 음성 0→0+0→1). 시작 전 경보 중인 쌍은 뺌."""
    new = pr[(pr["pre"] == 0) & (pr["ctrl"] == 0)]
    return new.groupby(level=KEYS)["inj"].agg(["sum", "count"])


def rate_ci(pr: pd.DataFrame, seed: int = 0) -> tuple[float, float, float]:
    """코호트 군집 부트스트랩 CI: 쌍이 아니라 코호트(도시×연령×성)를 다시 뽑음."""
    g = cohort_counts(pr)
    return cluster_rate_ci(g["sum"].to_numpy(), g["count"].to_numpy(), n_boot=5000, seed=seed)


ci15 = rate_ci(paired(ex, "local", 0.15))
r.h3("15% 계단형 하락: 분모를 나란히")
r.p("| 평가 가능 쌍 (시작 전 경보 없음) | 대조 음성 (0→0 + 0→1) | 새 탐지 (0→1) | 탐지율 | 대조 경보 (1→1+1→0) | 순증 경보율 차이 |")
r.p("|---|---|---|---|---|---|")
r.p(f"| {all15['평가 쌍(시작 전 경보 없음)']:,} | {all15['대조 음성(0→0+0→1)']:,} | {all15['0→1']:,} | {all15['0→1']:,} / {all15['대조 음성(0→0+0→1)']:,} = {fmt_rate_ci(*ci15)} | "
    f"{all15['1→1'] + all15['1→0']} / {all15['평가 쌍(시작 전 경보 없음)']} = {all15['대조 경보']:.0%} | {all15['순증 경보율 차이'] * 100:+.0f}%p |")
r.p()
r.p(f"- 탐지율의 분모는 평가 가능 {all15['평가 쌍(시작 전 경보 없음)']:,}쌍이 아니라 대조에서 울리지 않은 {all15['대조 음성(0→0+0→1)']:,}쌍입니다. 시작 전부터 경보 중이던 {all15['시작 전부터 경보 중(제외)']}쌍은 따로 뺐습니다.")
r.p("- 이 값은 **raw local 경보**의 **관측 후 활동 하락 감지 성능**입니다. 반증(공휴일·강수) 뒤 최종 후보의 탐지율이 아니고, 하락이 시작되기 전에 예측한 성능도 아닙니다. 지연도 카드 공급 지연을 반영하지 않은 관측일 기준입니다.")
r.p(f"- **CI는 코호트 단위 부트스트랩**입니다. 같은 코호트의 시작일 {N_START}곳은 기간이 서로 겹쳐 독립이 아니므로, 쌍이 아니라 코호트 {len(cols)}개를 다시 뽑았습니다(5,000번). 그래서 탐지율을 소수점 없이 적습니다.")
r.p("- **코호트 전체 결제가 줄어드는 경우의 감지율이며, 개인 고립이나 5% 이하의 작은 변화는 이 경보로 잡을 수 없다.** 하락 폭별 값은 아래 '4-1. 하락 폭별 감지율'에 있습니다.")

r.h3("도시 × 연령 (local 경로)")
r.p(f"분모 = 시작 전날 경보 중이 아니던 쌍(전체 {all15['평가 쌍(시작 전 경보 없음)']:,}쌍, 제외 {all15['시작 전부터 경보 중(제외)']}쌍). 탐지율 = 0→1 / (0→0 + 0→1). 대조 경보 = 대조에서 14일 안에 경보가 난 비율. 순증 경보율 차이 = 주입 경보율 − 대조 경보율 = (0→1 − 1→0) / 평가 쌍 (%p). 전이 표는 15% 주입(대조 → 주입).")
r.table(pp(det).reset_index().set_index("city"), fmt_det)
r.p("- **0→1만 '새로 잡음'** 입니다. 1→1(주입 안 해도 울렸을 경보)은 탐지로 세지 않습니다. 1→0은 주입한 뒤 오히려 경보가 사라진 쌍으로, 숨기지 않고 함께 적습니다.")
r.p("- **한계**: 주입은 시작일부터 14일 동안 한 번에 같은 비율로 떨어지는 계단형이라 쉬운 과제입니다. 실제 고립은 천천히 줄어들 수 있습니다.")
ramp_rows = []
for col, s in pairs:
    j = p0.cols.index(col)
    y = wide[col].to_numpy().copy()
    y[s:s + INJ_LEN] *= 1 - 0.15 * np.arange(1, INJ_LEN + 1) / INJ_LEN
    pre, c_hit, _ = window_outcome(p0.alarm_any(j, "local"), s)
    _, i_hit, dly = window_outcome(p0.score_one(col, y)["local"], s)
    ramp_rows.append((*col, s, pre, c_hit, i_hit, dly))
ramp = pd.DataFrame(ramp_rows, columns=KEYS + ["start", "pre", "ctrl", "inj", "delay"]).set_index(KEYS + ["start"])
tr_r = transitions(ramp)
tr_r80 = {c_: transitions(ramp.xs((c_, "80대+"), level=["city", "age_g"]))["탐지"] for c_ in CITIES}
r.p(f"- 점진 하락(14일에 걸쳐 0→15%로 선형 감소) 주입: 탐지 {tr_r['0→1']} / {tr_r['대조 음성(0→0+0→1)']} = {tr_r['탐지']:.0%} (계단형 15% {all15['탐지']:.0%}), 지연 중앙값 {tr_r['지연 중앙값(일)']:.0f}일 (계단형 {all15['지연 중앙값(일)']:.0f}일). 80대+ 강남 {tr_r80['강남']:.0%} · 춘천 {tr_r80['춘천']:.0%}.")

# 지연과 시작 직전 낮은 날
pr15 = paired(ex, "local", 0.15)
succ = pr15[(pr15["pre"] == 0) & (pr15["ctrl"] == 0) & (pr15["inj"] == 1)].copy()
succ["지연 구간"] = pd.cut(succ["delay"], [-0.5, 0.5, 1.5, 2.5, SUCCESS_DAYS], labels=["0일", "1일", "2일", "3일 이상"])
dl = pd.crosstab(succ["pre_low"], succ["지연 구간"], margins=True, margins_name="합계")
dl["지연 중앙값(일)"] = succ.groupby("pre_low")["delay"].median().reindex(dl.index).fillna(succ["delay"].median())
dl.index.name = "시작 직전 6일 중 낮은 날 수"
r.h3("지연 분포와 시작 직전 낮은 날 (15% 계단형, 0→1 쌍)")
r.p(f"'시작 직전 낮은 날'은 시작일 앞 {cfg.win - 1}일(첫 판정 창에 들어가는 날) 중 대조 자료의 local z < {cfg.z_thr}인 날 수입니다. 시작 전날 경보 중인 쌍은 이미 뺐으므로 최대 {cfg.k - 1}일입니다.")
r.table(dl, {**{c: "{:,.0f}" for c in dl.columns if c != "지연 중앙값(일)"}, "지연 중앙값(일)": "{:.0f}"})
fast = succ[succ["delay"] < cfg.k - 1]
r.p(f"- 지연 0~1일 {len(fast)}쌍(0일 {int((fast['delay'] == 0).sum())}, 1일 {int((fast['delay'] == 1).sum())}) 중 시작 직전 낮은 날이 있던 쌍은 {int((fast['pre_low'] > 0).sum())}쌍입니다. "
    f"시작 직전 낮은 날이 없으면 최소 지연은 {succ.loc[succ['pre_low'] == 0, 'delay'].min():.0f}일입니다. 지연 중앙값 {all15['지연 중앙값(일)']:.0f}일은 그대로입니다.")
clean = transitions(pr15[pr15["pre_low"] == 0])
r.p(f"- **보조 민감도 (시작 직전 창에 낮은 날 없음)**: {clean['평가 쌍(시작 전 경보 없음)']:,}쌍 중 대조 음성 {clean['대조 음성(0→0+0→1)']:,}쌍, 새 탐지 {clean['0→1']:,}쌍 → 탐지 {clean['탐지']:.0%}, "
    f"지연 중앙값 {clean['지연 중앙값(일)']:.0f}일, 최소 지연 {succ.loc[succ['pre_low'] == 0, 'delay'].min():.0f}일. 운영형 평가(위 {all15['탐지']:.0%})와 조건이 달라 숫자를 섞지 않습니다.")

det_abs = summarize(ex, "absolute")
det_eit = summarize(ex, "either")
both = pd.DataFrame({
    "local 15%": det["15% 탐지"], "absolute 15%": det_abs["15% 탐지"], "둘 중 하나 15%": det_eit["15% 탐지"],
    "local 대조": det["대조 경보"], "absolute 대조": det_abs["대조 경보"], "둘 중 하나 대조": det_eit["대조 경보"],
})
r.h3("경로별 비교 (15% 하락)")
r.table(both.reset_index().set_index("city"), {"age_g": "{}", **{c: "{:.0%}" for c in both.columns}})
r.p("- absolute 경로는 도시 공통 변화(휴가철·날씨·연휴)도 함께 잡으므로 대조 경보가 더 많습니다. 예상 밖 이탈 후보에는 local 경로를 주로 쓰고, absolute는 '실제로 줄었는지' 방향 확인에 씁니다.")
r.p("- '둘 중 하나'는 세 값 조합입니다. 이번 자료는 두 경로의 보류 구간이 같아 이전 계산과 숫자가 같습니다.")

# 지연 분포 그림
fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=True)
bins = np.arange(0, SUCCESS_DAYS + 1)
for ax, cty in zip(axes, CITIES):
    d_ = succ.xs(cty, level="city")
    stacks = [d_.loc[d_["pre_low"] == k, "delay"] for k in (0, 1, 2)]
    ax.hist(stacks, bins=bins - 0.5, stacked=True, color=["#2b6cb0", "#90cdf4", "#e2e8f0"], edgecolor="white",
            label=["직전 낮은 날 0일", "1일", "2일"])
    ax.set_title(f"{cty}: 15% 하락 첫 경보까지 일수 (중앙값 {d_['delay'].median():.0f}일)")
    ax.set_xlabel("시작일부터 alarm_date까지 (일)")
axes[0].set_ylabel("쌍 수")
axes[0].legend(fontsize=8)
r.fig(fig, "f01_delay.png", "경보 지연 분포 (시작 직전 낮은 날 수별)")
fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
for ax, cty in zip(axes, CITIES):
    for drop, color in zip(DROPS, ["#90cdf4", "#3182ce", "#1a365d"]):
        ax.plot(AGE_ORDER, det.loc[cty, f"{int(drop * 100)}% 탐지"] * 100, marker="o", color=color, label=f"{int(drop * 100)}% 하락 탐지")
    ax.plot(AGE_ORDER, det.loc[cty, "대조 경보"] * 100, marker="x", ls="--", color="#c53030", label="대조(주입 없음) 경보")
    ax.set_title(f"{cty}: 과거만 쓰는 경보 (local 경로)")
    ax.set_ylim(-3, 105)
axes[0].set_ylabel("새로 경보 난 쌍 비율 (%)")
axes[0].legend(fontsize=8)
r.fig(fig, "f02_detection_by_age.png", "연령별 탐지율")

# ------------------------------------------------------------ 4-1. 하락 폭별 감지율
r.h("4-1. 하락 폭별 감지율 (최소 감지 하락)")
r.p("**코호트 전체 결제가 줄어드는 경우의 감지율이며, 개인 고립이나 5% 이하의 작은 변화는 이 경보로 잡을 수 없다.**")
r.p()
r.p(f"4절과 **같은 쌍 목록**({len(pairs):,}쌍, 같은 시작일·같은 대조)에 2·3·5·10·15·20% 계단형 하락을 {INJ_LEN}일 동안 넣었습니다(local 경로). 탐지율 = 0→1 / (0→0 + 0→1), 시작 전부터 경보 중인 쌍은 뺍니다. "
    "CI는 코호트(도시×연령×성) 단위 부트스트랩입니다(5,000번). 쌍은 서로 겹쳐 독립이 아니라서 쌍 단위로 재표집하면 범위가 지나치게 좁아집니다.")


def run_small(p: Panel, drops) -> pd.DataFrame:
    rows = []
    for col, s in pairs:
        y0 = wide[col].to_numpy()
        for drop in drops:
            y = y0.copy()
            y[s:s + INJ_LEN] *= 1 - drop
            pre, hit, delay = window_outcome(p.score_one(col, y)["local"], s)
            rows.append((*col, s, drop, "local", pre, hit, delay))
    return pd.DataFrame(rows, columns=KEYS + ["start", "drop", "path", "pre", "hit", "delay"])


ex_all = pd.concat([ex, run_small(p0, SMALL_DROPS)], ignore_index=True)
DL = {d: f"{d * 100:.0f}%" for d in ALL_DROPS}
mde_rows, mde_cells = [], []
for d in ALL_DROPS:
    g = cohort_counts(paired(ex_all, "local", d))
    for scope, sub in [("전체", g)] + [(c_, g.xs(c_, level="city", drop_level=False)) for c_ in CITIES]:
        rt, lo_, hi_ = cluster_rate_ci(sub["sum"].to_numpy(), sub["count"].to_numpy(), n_boot=5000, seed=0)
        mde_rows.append({"범위": scope, "하락": DL[d], "새 탐지": int(sub["sum"].sum()), "대조 음성": int(sub["count"].sum()),
                         "탐지율": rt, "CI 하한": lo_, "CI 상한": hi_})
    ga = g.groupby(level=["city", "age_g"]).sum()
    for (c_, a_), v in ga.iterrows():
        mde_cells.append({"city": c_, "age_g": a_, "하락": DL[d], "탐지율": v["sum"] / v["count"] if v["count"] else np.nan, "대조 음성": int(v["count"])})
mde = pd.DataFrame(mde_rows)
mde["탐지율 (코호트 CI)"] = [fmt_rate_ci(a, b, c) for a, b, c in mde[["탐지율", "CI 하한", "CI 상한"]].itertuples(index=False)]
mt = mde.pivot(index="범위", columns="하락", values="탐지율 (코호트 CI)").reindex(index=["전체"] + CITIES, columns=list(DL.values()))
mt.index.name = "범위 (local)"
r.h3("전체·도시별 (코호트 군집 CI)")
r.table(mt, {c_: "{}" for c_ in mt.columns})
m15 = mde[(mde["범위"] == "전체") & (mde["하락"] == "15%")].iloc[0]
r.p(f"- 15% 하락: {int(m15['새 탐지'])}/{int(m15['대조 음성'])} = {fmt_rate_ci(m15['탐지율'], m15['CI 하한'], m15['CI 상한'])}. 4절의 값과 같은 쌍·같은 계산입니다.")
small = mde[mde["범위"] == "전체"].set_index("하락")
r.p(f"- 작은 하락의 감지율: 2% 하락 → {small.loc['2%', '탐지율']:.0%}, 3% 하락 → {small.loc['3%', '탐지율']:.0%}, 5% 하락 → {small.loc['5%', '탐지율']:.0%}. 사회적 고립 비율(시민생활 추정 약 2%)만큼 코호트 결제가 줄어도, 이 경보로는 거의 잡히지 않습니다.")
cells = pd.DataFrame(mde_cells)
mc = cells.pivot_table(index=["city", "age_g"], columns="하락", values="탐지율").reindex(columns=list(DL.values()))
mc = mc.reindex(pd.MultiIndex.from_product([CITIES, AGE_ORDER], names=["city", "age_g"]))
mc["대조 음성 n (15%)"] = cells[cells["하락"] == "15%"].set_index(["city", "age_g"])["대조 음성"]
r.h3("도시 × 연령 (점추정)")
r.p("도시 × 연령 칸은 코호트가 2개(남녀)뿐이라 군집 CI를 내지 않고 점추정만 적습니다. 칸끼리 몇 %p 차이는 우연 범위 안일 수 있습니다.")
r.table(mc.reset_index().set_index("city"), {"age_g": "{}", **{c_: "{:.0%}" for c_ in DL.values()}, "대조 음성 n (15%)": "{:,.0f}"})
mde_out = pd.concat([mde.drop(columns=["탐지율 (코호트 CI)"]).assign(age_g="전체"),
                     cells.rename(columns={"city": "범위"})], ignore_index=True)
mde_out["path"] = "local"
mde_out["interpretation_status"] = "계산 재현 (코호트 전체 계단형 하락 주입, 개인 고립 아님)"
mde_out.to_csv(r.dir / "하락폭별_탐지.csv", index=False, encoding="utf-8-sig")
fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
xs = [d * 100 for d in ALL_DROPS]
for ax, cty in zip(axes, CITIES):
    for a_ in AGE_ORDER:
        ax.plot(xs, mc.loc[(cty, a_), list(DL.values())].to_numpy(dtype=float) * 100, marker="o", lw=1, label=a_,
                color=plt.cm.viridis(AGE_ORDER.index(a_) / len(AGE_ORDER)))
    sub = mde[mde["범위"] == cty].set_index("하락").reindex(list(DL.values()))
    ax.fill_between(xs, sub["CI 하한"] * 100, sub["CI 상한"] * 100, color="#cbd5e0", alpha=0.5, label="도시 전체 코호트 CI")
    ax.set_title(f"{cty}: 하락 폭별 감지율 (local, 코호트 전체 하락)")
    ax.set_xlabel("코호트 전체 결제 하락 폭 (%)")
    ax.set_ylim(-3, 105)
axes[0].set_ylabel("새로 경보 난 쌍 비율 (%)")
axes[0].legend(fontsize=7, ncol=2)
r.fig(fig, "f03_detection_by_drop.png", "하락 폭별 감지율")
r.p("- 결과: [하락폭별_탐지.csv](하락폭별_탐지.csv). 주입은 14일 계단형·코호트 하나라 쉬운 과제입니다. 실제 하락이 더 짧거나 일부 업종에서만 생기면 감지율은 더 낮을 수 있습니다.")

# ------------------------------------------------------------ 4-2. 도시 전체 하락 주입
r.h("4-2. 도시 전체 하락 주입 (city_shock·absolute 경로)")
rng_city = np.random.default_rng(1)  # 4절 쌍 목록과 다른 난수열 (4절 숫자를 바꾸지 않음)
city_rows = []
for c_ in CITIES:
    ccols = [col for col in cols if col[0] == c_]
    for s in sorted(rng_city.choice(cand, N_CITY_START, replace=False)):
        Yc = wide.copy()
        Yc.loc[Yc.index[s:s + INJ_LEN], ccols] *= 0.85
        pc = Panel(Yc, HOL, cfg)
        pre, ch, _ = window_outcome(p0.city_signal[c_]["city_alarm"].to_numpy(), s)
        _, ih, dly = window_outcome(pc.city_signal[c_]["city_alarm"].to_numpy(), s)
        city_rows.append((c_, s, "city_shock (도시 경보)", "도시", pre, ch, ih, dly))
        for col in ccols:
            j = cols.index(col)
            for path in ("absolute", "local"):
                pre, ch, _ = window_outcome(p0.alarm_any(j, path), s)
                _, ih, dly = window_outcome(pc.alarm_any(j, path), s)
                city_rows.append((c_, s, path + " (코호트)", f"{col[1]} {col[2]}", pre, ch, ih, dly))
cx = pd.DataFrame(city_rows, columns=["city", "start", "path", "unit", "pre", "ctrl", "inj", "delay"])
cx["start_date"] = dates[cx["start"].to_numpy()].date
cx.to_csv(r.dir / "도시전체주입.csv", index=False, encoding="utf-8-sig")
ct_rows = []
for (c_, path), g in cx.groupby(["city", "path"], sort=False):
    tr = transitions(g)
    ct_rows.append({"city": c_, "경로": path, "평가 단위": tr["평가 쌍(시작 전 경보 없음)"], "대조 음성": tr["대조 음성(0→0+0→1)"], "0→1": tr["0→1"],
                    "탐지": tr["탐지"], "대조 경보": tr["대조 경보"], "지연 중앙값(일)": tr["지연 중앙값(일)"]})
ctab = pd.DataFrame(ct_rows).set_index("city")
r.p(f"4절은 코호트 하나만 낮춥니다. local 경로는 도시 공통 변화를 빼도록 만들었으므로, 도시 전체가 함께 낮아지는 경우는 따로 봐야 합니다. "
    f"도시마다 시작일 {N_CITY_START}곳(4절과 같은 후보 시작일, 다른 난수)을 뽑아 **그 도시 14개 코호트 모두**를 {INJ_LEN}일 동안 15% 낮추고, 전체 패널을 다시 계산했습니다.")
r.table(ctab, {"경로": "{}", "평가 단위": "{:,}", "대조 음성": "{:,}", "0→1": "{:,}", "탐지": "{:.0%}", "대조 경보": "{:.0%}", "지연 중앙값(일)": "{:.0f}"})
c_city = ctab[ctab["경로"].str.startswith("city_shock")]["탐지"]
c_abs = ctab[ctab["경로"].str.startswith("absolute")]["탐지"]
c_loc = ctab[ctab["경로"].str.startswith("local")]["탐지"]
r.p(f"- 도시 경보(city_shock): 강남 {c_city.get('강남', np.nan):.0%} · 춘천 {c_city.get('춘천', np.nan):.0%}. 코호트 absolute: 강남 {c_abs.get('강남', np.nan):.0%} · 춘천 {c_abs.get('춘천', np.nan):.0%}. "
    f"코호트 local: 강남 {c_loc.get('강남', np.nan):.0%} · 춘천 {c_loc.get('춘천', np.nan):.0%} (설계상 거의 울리지 않아야 함).")
r.p("- 도시 전체 하락은 local 경로로는 잡히지 않고 city_shock·absolute 경로로 잡힙니다. 이렇게 잡힌 경보는 공휴일 앞뒤가 아니면 공휴일 반증 대상이 아니고, 공휴일 앞뒤라면 '확인 필요(도시 공통 변화)'로 남습니다.")
r.p(f"- 도시당 시작일 {N_CITY_START}곳의 작은 실험입니다. 점진형·더 작은 하락·일부 연령만 함께 낮아지는 경우는 아직 보지 않았습니다. 결과: [도시전체주입.csv](도시전체주입.csv)")

# ------------------------------------------------------------ 4-3. 운영 경보량
r.h("4-3. 운영 경보량: 주당 경보 수")


def episode_starts(alarm: np.ndarray, dedup_days: int = 7) -> list[int]:
    """episode_count와 같은 규칙(7일 안 재경보는 한 건)으로 묶음의 첫 경보일 위치."""
    out, last = [], None
    for t in np.where(alarm == 1)[0]:
        if last is None or t - last > dedup_days:
            out.append(int(t))
        last = t
    return out


load_rows = []
for path in ("either", "local"):
    for c_ in CITIES:
        js = [j for j, col in enumerate(cols) if col[0] == c_]
        starts, judged = [], np.zeros(n)
        for j in js:
            a = p0.alarm_any(j, path)
            assert len(episode_starts(a)) == episode_count(a)
            starts += episode_starts(a)
            judged += ~np.isnan(a)
        st_month = pd.Series(dates[starts].month).value_counts()
        cohort_days = pd.Series(judged, index=dates).groupby(dates.month).sum() / len(js)  # 코호트 평균 판정 가능일
        row = {"경로": path, "도시": c_, "경보 건수": len(starts), "판정 가능 주": judged.sum() / len(js) / 7}
        row["주당 경보 수 (전체)"] = len(starts) / row["판정 가능 주"]
        for m in range(7, 13):
            wk_m = cohort_days.get(m, 0) / 7
            row[f"{m}월"] = st_month.get(m, 0) / wk_m if wk_m > 0 else np.nan
        load_rows.append(row)
load = pd.DataFrame(load_rows).set_index("경로")
no_month = [c_ for c_ in [f"{m}월" for m in range(7, 13)] if load[c_].isna().all()]
load = load.drop(columns=no_month)  # 학습 기간이라 경보 판정이 없는 달
r.p(f"실제 자료(주입 없음)에서 울린 경보를 7일 묶음으로 세고, 경보 판정 가능 기간(코호트 평균, {first_ok}부터)의 주 수로 나눴습니다. 월별 값은 묶음의 첫 경보일이 속한 달 기준입니다. 도시마다 코호트 14개를 합친 값입니다. "
    "'둘 중 하나'(either)는 absolute·local 중 하나라도 울린 경보입니다.")
r.table(load, {"도시": "{}", "경보 건수": "{:,.0f}", "판정 가능 주": "{:.1f}", "주당 경보 수 (전체)": "{:.1f}", **{f"{m}월": "{:.1f}" for m in range(7, 13) if f"{m}월" not in no_month}})
if no_month:
    r.p(f"- {', '.join(no_month)}은 학습 기간이라 경보를 판정하지 않았습니다(표에서 뺌).")
tot = load.groupby(level=0)[["경보 건수"]].sum()
wks = load.groupby(level=0)["판정 가능 주"].mean()
r.p(f"- 두 도시 합: '둘 중 하나' {int(tot.loc['either', '경보 건수'])}건 → 주 {tot.loc['either', '경보 건수'] / wks['either']:.1f}건, local {int(tot.loc['local', '경보 건수'])}건 → 주 {tot.loc['local', '경보 건수'] / wks['local']:.1f}건 (28개 코호트 전체).")
r.p("- 월별로 보면 경보는 추석이 있는 10월에 몰립니다. 평소 주에는 이보다 적고, 연휴 주에는 많습니다.")
r.p("- **코호트 경보는 어느 동인지 알려 주지 않습니다.** 카드 자료 단위가 '가맹점 시군구 × 고객 거주 시도 × 성·연령'이라, 경보 1건은 '강남 80대+ 여성 전체'처럼 도시 단위 집단을 가리킵니다. "
    "현장 확인으로 옮기려면 동을 고르는 규칙(예: 통신·외부 자료로 그 연령이 많은 동 K곳)이 따로 필요하고, 주당 현장 확인 건수 ≈ 주당 경보 수 × K입니다. 이 규칙은 아직 정하지 않았습니다.")

# ------------------------------------------------------------ 5. 오경보율 (합성 정상 시계열)
r.h("5. 오경보율 (정상으로 만든 합성 시계열)")
N_SYN = 10
fa_list, lag_syn = [], []
for seed in range(N_SYN):
    syn = synthetic_normal(wide, HOL, seed=seed)
    lag_real = pd.Series(syn.attrs["lag1_real"])
    lag_syn.append(pd.Series(syn.attrs["lag1_synthetic"]))
    fa_list.append(bg_table(Panel(syn, HOL, cfg), "local"))
fa = sum(fa_list) / N_SYN
r.p(f"실제 자료의 요일 효과, 흔들림 크기, **이웃한 날끼리의 자기상관(AR(1))** 만 빌려 이상이 없는 시계열을 {N_SYN}번 만들고 같은 규칙을 돌렸습니다. 여기서 난 경보는 정의상 모두 오경보입니다.")
r.p(f"- 경보 규칙이 보는 local 잔차의 lag-1 자기상관: 실제 중앙값 {lag_real.median():.2f}, 합성 중앙값 {pd.concat(lag_syn).median():.2f}. 날마다 독립인 잡음(자기상관 0)으로 만들면 경보가 덜 나서 오경보율이 낙관적으로 나옵니다.")
r.table(fa.reset_index().set_index("city"), BG_FMT)
r.p("- 이 값은 **이 생성 모형(AR(1) 잡음, 공휴일 효과·추세 변화·이상 없음)에서의 값**입니다. 실제 자료의 오경보율이나 현장 정확도로 넓혀 말하지 않습니다. 실제 자료의 무주입 경보는 3절의 **배경 경보율**로만 부릅니다.")
r.p("- 다음 모델 비교에서도 같은 쌍·같은 생성 조건으로 비교합니다.")

r.h3("배경 경보율과 합성 오경보율 (같은 분모로 나란히)")
cmp_rows = []
for label, tb in [("배경 경보율 (실제 자료)", bg), ("오경보율 (합성 조건)", fa)]:
    for c_ in CITIES:
        x = tb.xs(c_, level="city")
        cmp_rows.append({"구분": label, "도시": c_, **{k: x[k].mean() for k in BG_FMT if k not in ("age_g", "평가창 수")}})
cmp = pd.DataFrame(cmp_rows).set_index("구분")
r.table(cmp, {"도시": "{}", **{k: v for k, v in BG_FMT.items() if k not in ("age_g", "평가창 수")}})
r.p("(local 경로, 연령 평균) 두 줄은 같은 규칙·같은 분모로 계산했지만 자료가 다릅니다. 같은 숫자로 발표하지 않습니다.")

# ------------------------------------------------------------ 6. 민감도
r.h("6. 민감도")
r.h3("공통요인에서 대상 코호트 빼기 (leave-target-out)")
p_lto = Panel(wide, HOL, AlarmConfig(leave_target_out=True))
rec_lto = records(p_lto, rain=rain)
ex_lto = run_experiment(p_lto)
det_lto = summarize(ex_lto, "local")
cnt = lambda d: d.groupby("path").size() if len(d) else pd.Series(dtype=int)  # noqa: E731
lto = pd.DataFrame({
    "기본(대상 포함) 15% 탐지": det["15% 탐지"], "leave-target-out 15% 탐지": det_lto["15% 탐지"],
    "기본 대조 경보": det["대조 경보"], "leave-target-out 대조 경보": det_lto["대조 경보"],
})
lto["탐지 차이"] = (lto["leave-target-out 15% 탐지"] - lto["기본(대상 포함) 15% 탐지"]) * 100
r.table(lto.reset_index().set_index("city"), {"age_g": "{}", **{c: ("{:+.0f}%p" if "차이" in c else "{:.0%}") for c in lto.columns}})
c0, c1 = cnt(rec), cnt(rec_lto)
r.p(f"- 실제 경보 수(중복 제거): 기본 {len(rec)}건 (absolute {c0.get('absolute', 0)}, local {c0.get('local', 0)}, 둘 다 {c0.get('both', 0)}) → leave-target-out {len(rec_lto)}건 (absolute {c1.get('absolute', 0)}, local {c1.get('local', 0)}, 둘 다 {c1.get('both', 0)})")
r.p(f"- 15% 하락 탐지 평균: 기본 {det['15% 탐지'].mean():.0%} → leave-target-out {det_lto['15% 탐지'].mean():.0%}. 고령층처럼 흔들림이 큰 코호트에서 차이가 더 큽니다.")
r.h3("학습 기간 길이")
p_42 = Panel(wide, HOL, AlarmConfig(min_train=42))
cand_42 = [s for s in cand if s >= 42 + cfg.win]
ex_42_rows = []
for col, s in [(c, s) for c, s in pairs if s in cand_42]:
    j = p_42.cols.index(col)
    a0 = p_42.alarm_any(j, "local")
    y = wide[col].to_numpy().copy()
    y[s:s + INJ_LEN] *= 0.85
    a1 = p_42.score_one(col, y)["local"]
    pre, c_hit, _ = window_outcome(a0, s)
    _, i_hit, _ = window_outcome(a1, s)
    ex_42_rows.append((*col, s, pre, c_hit, i_hit))
e42 = pd.DataFrame(ex_42_rows, columns=KEYS + ["start", "pre", "ctrl", "inj"]).assign(delay=np.nan)
tr42 = transitions(e42)
base_same = paired(ex, "local", 0.15).reset_index()
tr28 = transitions(base_same[base_same["start"].isin(cand_42)])
r.p(f"최소 학습 기간을 28일 → 42일로 늘리고, 두 설정 모두 평가할 수 있는 같은 시작일의 쌍만 비교했습니다(평가 쌍 28일 {tr28['평가 쌍(시작 전 경보 없음)']:,} / 42일 {tr42['평가 쌍(시작 전 경보 없음)']:,}).")
r.p(f"- 15% 하락 탐지(0→1/(0→0+0→1)): 28일 {tr28['탐지']:.0%} / 42일 {tr42['탐지']:.0%}. 대조 경보: 28일 {tr28['대조 경보']:.1%} / 42일 {tr42['대조 경보']:.1%}")

# ------------------------------------------------------------ 7. EDA-5와 나란히
r.h("7. EDA-5 값과 나란히 보기")
old = ROOT / "reports" / "eda5_인사이트재현" / "경보_사후평가.csv"
side = pd.DataFrame({"EDA-6 local 15% 탐지(사전)": det["15% 탐지"].groupby(level="age_g").mean(),
                     "EDA-6 배경 경보율(평가창당, 새로 난 창)": bg["새로 경보 난 창"].groupby(level="age_g").mean()}).reindex(AGE_ORDER)
if old.exists():
    o = pd.read_csv(old).set_index("연령")
    side["EDA-5 15% 탐지(사후)"] = o["15% 하락 탐지"]
    side["EDA-5 무주입 경보(사후)"] = o["무주입 경보(배경 경보율)"]
side.index.name = "연령"
r.table(side, {c: "{:.0%}" for c in side.columns})
r.p("- **두 결과는 평가 방법이 다릅니다.** EDA-5는 6개월 전체로 평소 값을 만든 사후 계산이고, 시작일·대조를 짝짓지 않았고, 공휴일 앞뒤 창을 바로 기각했습니다. EDA-6은 과거 자료만 쓰고 짝지은 대조를 둡니다.")
r.p("- 그래서 두 숫자의 차이를 **개선이나 악화로 해석하지 않습니다.** 앞으로 사전 경보 성능으로 인용할 수 있는 값은 EDA-6 값뿐입니다.")

# ------------------------------------------------------------ 결과 표 저장
det_out = []
for path, d_ in [("local", det), ("absolute", det_abs), ("either", det_eit), ("local_leave_target_out", det_lto)]:
    x = d_.reset_index()
    x.insert(0, "path", path)
    det_out.append(x)
det_out = pd.concat(det_out)
bg_l = bg[["평가창 중 경보 있음", "새로 경보 난 창", "경보일 / 코호트·일", "경보 건수(중복 제거) / 코호트·100일"]].add_prefix("배경 경보율(local) ")
det_out = det_out.merge(bg_l.reset_index(), on=["city", "age_g"], how="left")
fa_l = fa[["평가창 중 경보 있음", "새로 경보 난 창", "경보일 / 코호트·일", "경보 건수(중복 제거) / 코호트·100일"]].add_prefix("합성 오경보율(local) ")
det_out = det_out.merge(fa_l.reset_index(), on=["city", "age_g"], how="left")
det_out["분모"] = ("탐지 = 0→1/(0→0+0→1), 시작 전 경보 중인 쌍 제외 / 순증 경보율 차이는 확률 차이(소수, %p로 읽음) / "
                 "배경: 14일 평가창, 경보 판정 가능 코호트·일 / 합성 = AR(1) 잡음 정상 시계열")
det_out["interpretation_status"] = "계산 재현"
det_out.to_csv(r.dir / "탐지_결과.csv", index=False, encoding="utf-8-sig")
r.p()
r.p("전체 표: [탐지_결과.csv](탐지_결과.csv) (경로별 도시 × 연령, 전이 표·지연·배경 경보율·오경보율 포함)")

# ------------------------------------------------------------ 해석·평가의 남은 제한
r.h("해석·평가의 남은 제한")
r.p(f"- **분모**: 15% 계단형 탐지는 평가 가능 {all15['평가 쌍(시작 전 경보 없음)']:,}쌍 · 대조 음성 {all15['대조 음성(0→0+0→1)']:,}쌍 · 새 탐지 {all15['0→1']:,}쌍을 함께 적습니다. "
    f"{all15['탐지']:.0%} = {all15['0→1']:,}/{all15['대조 음성(0→0+0→1)']:,}입니다. 순증 경보율 차이 {all15['순증 경보율 차이'] * 100:+.0f}%p는 같은 쌍의 확률 차이입니다.")
r.p("- **무엇의 성능인가**: raw local 경보의 관측 후 활동 하락 감지 성능입니다. 공휴일·강수 반증 뒤 최종 후보의 탐지율이 아니고, 하락 시작 전 예측도 아닙니다. 3B 정책이 생기면 raw와 검토 후 평가를 나눕니다.")
r.p("- **주입 범위**: 4절은 코호트 하나만 낮춥니다. 도시 전체 하락은 4-2절의 작은 실험(도시당 시작일 20곳, 15% 계단형)으로만 봤습니다.")
r.p("- **합성 오경보율**: AR(1) 10회 값은 그 생성 모형 조건의 값입니다. 실제 고립 오경보율·현장 정확도로 넓히지 않습니다.")
r.p("- **같은 6개월로 설정·평가**: 임계값과 시작일을 같은 6개월에서 정하고 평가했습니다. 코드의 시간 누출 방지와 미래 기간 일반화는 다른 문제입니다. 임계값·모델을 고도화할 때는 시간 순서로 개발/검증 구간을 나눕니다.")
r.p(f"- **7일 묶음**: 7일 안 재경보를 계속 이어 붙이므로 묶음은 7일보다 길 수 있습니다(이번 최대 {ep_len.max()}일). 반증은 첫 창만 봅니다.")
r.p("- **사후 목록과 기준일 카드**: 경보_목록.csv는 사후 목록입니다. 경보 당일 상태는 경보_기준일카드.csv와 `records(panel, as_of=...)`로 봅니다.")
r.p("- **카드 0건 가정**: 카드 `daily()`가 빠진 (날짜, 코호트)를 0건으로 채웁니다. 경보 모듈은 NaN을 미공급으로 다루지만, 지금 입력에서는 미공급과 거래 0을 구분할 수 없습니다.")
r.p("- **공급 지연**: 관측일 t의 카드·도시 공통요인을 t에 쓸 수 있다고 가정한 재생입니다. 실제 공개일은 시간 계약(`contracts/time.py`)의 공급 가능일로 따로 다룹니다.")

# ------------------------------------------------------------ 정리
r.h("정리")
old80 = det.xs("80대+", level="age_g")
bgm, fam = bg.mean(), fa.mean()
r.p("| 결과 | 값 | 해석 상태 |")
r.p("|---|---|---|")
r.p(f"| 15% 계단형 하락 감지 (raw local, 관측 후, 0→1/(0→0+0→1), **코호트 전체 하락**) | {all15['0→1']:,} / {all15['대조 음성(0→0+0→1)']:,} = {fmt_rate_ci(*ci15)} (평가 가능 {all15['평가 쌍(시작 전 경보 없음)']:,}쌍 중 대조 음성 {all15['대조 음성(0→0+0→1)']:,}쌍), 80대+ 강남 {old80.loc['강남', '15% 탐지']:.0%} · 춘천 {old80.loc['춘천', '15% 탐지']:.0%} | 계산 재현 |")
r.p("| 하락 폭별 감지 (같은 쌍, 코호트 CI) | " + ", ".join(f"{k} 하락 → {fmt_rate_ci(*small.loc[k, ['탐지율', 'CI 하한', 'CI 상한']])}" for k in DL.values()) + " | 계산 재현 |")
r.p(f"| 운영 경보량 (실제 자료, 7일 묶음) | 둘 중 하나 주 {tot.loc['either', '경보 건수'] / wks['either']:.1f}건, local 주 {tot.loc['local', '경보 건수'] / wks['local']:.1f}건 (두 도시 28코호트). 코호트 경보는 동을 알려 주지 않음 | 계산 재현 |")
r.p(f"| 대조 경보·순증 경보율 차이 (같은 쌍) | 대조 {all15['대조 경보']:.0%}, 순증 {all15['순증 경보율 차이'] * 100:+.0f}%p (0→1 {all15['0→1']}, 1→0 {all15['1→0']}, 1→1 {all15['1→1']}) | 계산 재현 |")
r.p(f"| 지연 중앙값 (15%, 0→1 쌍, 관측일 기준) | {all15['지연 중앙값(일)']:.0f}일 (0일 {int((succ['delay'] == 0).sum())}쌍, 1일 {int((succ['delay'] == 1).sum())}쌍은 시작 직전 낮은 날이 있던 경우) | 계산 재현 |")
r.p(f"| 시작 직전 창에 낮은 날 없음 (보조 민감도) | 탐지 {clean['탐지']:.0%} ({clean['0→1']}/{clean['대조 음성(0→0+0→1)']}), 지연 중앙값 {clean['지연 중앙값(일)']:.0f}일 | 계산 재현 |")
r.p(f"| 점진 하락(0→15%, 14일) 감지 | {tr_r['0→1']} / {tr_r['대조 음성(0→0+0→1)']} = {tr_r['탐지']:.0%}, 지연 중앙값 {tr_r['지연 중앙값(일)']:.0f}일 | 계산 재현 |")
r.p(f"| 도시 전체 15% 하락 (도시당 {N_CITY_START}곳) | 도시 경보 강남 {c_city.get('강남', np.nan):.0%} · 춘천 {c_city.get('춘천', np.nan):.0%}, 코호트 absolute 강남 {c_abs.get('강남', np.nan):.0%} · 춘천 {c_abs.get('춘천', np.nan):.0%}, 코호트 local 강남 {c_loc.get('강남', np.nan):.0%} · 춘천 {c_loc.get('춘천', np.nan):.0%} | 계산 재현 (작은 실험) |")
r.p(f"| 배경 경보율 (실제 자료, local) | 평가창당 {bgm['평가창 중 경보 있음']:.1%} (새로 난 창 {bgm['새로 경보 난 창']:.1%}), 경보 판정 가능 코호트·일당 {bgm['경보일 / 코호트·일']:.2%}, 중복 제거 {bgm['경보 건수(중복 제거) / 코호트·100일']:.2f}건/코호트·100일 | 계산 재현 |")
r.p(f"| 오경보율 (합성 조건, AR(1) 잡음) | 평가창당 {fam['평가창 중 경보 있음']:.1%} (새로 난 창 {fam['새로 경보 난 창']:.1%}), 코호트·일당 {fam['경보일 / 코호트·일']:.2%}, 중복 제거 {fam['경보 건수(중복 제거) / 코호트·100일']:.2f}건/코호트·100일 | 계산 재현 (합성 조건) |")
r.p(f"| 보류 회계 (local) | z 없음 {na.loc['z 없음 (경보도 보류)', 'local']:,}, z 있음·경보창 판정 불가 {na.loc['z 있음, 경보창 판정 불가', 'local']:,}, 경보 판정 가능 {na.loc['경보 판정 가능', 'local']:,} / {na.loc['전체', 'local']:,} 코호트·일. 첫 판정 가능일 {first_ok} | 계산 재현 |")
r.p(f"| 실제 경보 (7일 묶음, 사후 목록) | {n_rec}건 (경로 전체 기준 absolute {int((rec['path'] == 'absolute').sum())}, local {int((rec['path'] == 'local').sum())}, 둘 다 {int((rec['path'] == 'both').sum())}). "
    f"반증 " + " · ".join(f"{k} {new_cnt.get(k, 0)}" for k in REV_ORDER if new_cnt.get(k, 0))
    + f" (이전 규칙: 통과 {old_cnt.get('통과', 0)} · 기각 {old_cnt.get('기각', 0)} · 보류 {old_cnt.get('보류', 0)}). 강수로 설명되는 경보 {n_rain}건(기각하지 않음) | 계산 재현 |")
r.p("| 실제 고립 사건 탐지율 | 알 수 없음 (정답 라벨 없음) | 미확인 |")
r.p()
r.p("- 위 숫자는 정해진 크기의 합성 하락을 얼마나 잡는지 보여 줄 뿐, 실제 사회적 고립을 잡는 비율이 아닙니다. **코호트 전체 결제가 줄어드는 경우의 감지율이며, 개인 고립이나 5% 이하의 작은 변화는 이 경보로 잡을 수 없다.**")
r.p("- 80대+는 하루하루 흔들림이 커서 잡기 어렵습니다. 설계대로 연휴·폭염·겨울 같은 **예정된 점검**으로 보완합니다.")
r.save()
print("done")
