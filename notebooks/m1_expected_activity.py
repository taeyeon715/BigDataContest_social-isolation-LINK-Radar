"""M1 기대 활동 모델

실행: uv run python notebooks/m1_expected_activity.py
결과: reports/m1_기대활동모델/ (README.md, figs/, *.csv), configs/models.yaml 의 decisions

1) rolling-origin 예측 평가: 0a / 0b / (0b8 참고) / 1 STLForecast / 2 LightGBM, h=1과 h=7
2) 채택 결정: configs/models.yaml 의 규칙(비교 전에 커밋)대로 계산해 decisions 칸에 기록
3) 탐지: EDA-6과 같은 짝지은 주입 실험을 모델 잔차로 반복 (같은 쌍, 같은 정의)
4) 관측 후 이탈 판정: 같은 날 공통요인·ASOS 강수로 설명되는 부분과 남는 이탈
5) PELT 사후 변화점 분석
개발 중 캐시: 환경변수 M1_CACHE=<폴더> 를 주면 무거운 계산을 그 폴더에 저장·재사용
"""

import os
import pickle
import re
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from bigcontest.card import AGE_ORDER
from bigcontest.config import ROOT, load_config
from bigcontest.models import LGBMModel, SameWeekdayMean, SeasonalNaive, STLModel
from bigcontest.models.changepoint import LABEL as PELT_LABEL
from bigcontest.models.changepoint import noise_sd, pelt, prefix_discovery
from bigcontest.models.evaluate import (ModelPanel, ModelResid, add_intervals, attach_actual, background, bg_summary,
                                        card_cohort_panel, forecast_metrics, origins, ramp, run_pairs, step, transitions)
from bigcontest.models.lgbm import FEATURES
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report
from bigcontest.signals.alarm import AlarmConfig, records, synthetic_normal
from bigcontest.weather import load as load_weather

CFG_PATH = ROOT / "configs" / "models.yaml"
MCFG = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
HOL = [pd.Timestamp(k) for k in load_config()["holidays"]]
CITIES = ["강남", "춘천"]
KEYS = ["city", "age_g", "SEX_CCD"]
cfg = AlarmConfig()
CACHE = Path(os.environ["M1_CACHE"]) if os.environ.get("M1_CACHE") else None
N_SYN = 10
INJ_LEN, N_START, SUCCESS_DAYS = 14, 30, 14
MARGIN = MCFG["adoption"]["margin"]
NOMINAL = MCFG["interval"]["nominal"]


def cached(name, fn):
    if CACHE is None:
        return fn()
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{name}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    v = fn()
    f.write_bytes(pickle.dumps(v))
    return v


def age_of(c):
    return c[1]


r = Report("m1_기대활동모델", "M1 기대 활동 모델")
r.p("코호트(도시 × 연령 × 성)마다 '평소라면 이 정도'(기대 활동)를 예측하는 모델을 만들고, **시간 순서를 지킨 평가**로 단순 기준선보다 나은지 확인했습니다.")
r.p("그다음 같은 모델의 예측 잔차로 EDA-6 경보 실험을 **같은 쌍·같은 정의로** 다시 돌려, 기대값을 바꾸면 경보가 어떻게 달라지는지 봤습니다.")
r.p()
r.p("**두 경로를 나눕니다**")
r.p()
r.p("| 경로 | 질문 | 쓰는 정보 |")
r.p("|---|---|---|")
r.p("| 미래 기대 활동 예측 (1~3절) | 다음 1~7일은 평소라면 얼마? | 달력(공휴일), 기준일까지의 결제·공통요인. **날씨는 넣지 않음**(예보 이력이 없어 미래 실측으로 대신하지 않음). 같은 미래 날짜의 다른 코호트 결제도 쓰지 않음 |")
r.p("| 관측 후 이탈 판정 (4~5절) | 이미 본 날이 평소와 달랐나? | 같은 날 다른 코호트의 공통요인, 같은 날 ASOS 강수 |")

# ------------------------------------------------------------ 0. 자료
Y, n_missing = card_cohort_panel(KEYS)
cols = list(Y.columns)
dates = pd.DatetimeIndex(Y.index)
n = len(dates)
rain = load_weather().pivot_table(index="date", columns="city", values="rain")

r.h("0. 자료와 분할")
r.p(f"- 자료: 카드 데이터2, 같은 시도 거주 고객(강남 가맹점 = 서울 거주, 춘천 가맹점 = 강원 거주), 본사등록 업종 제외. 10대 제외, 80·90대는 80대+. 코호트 {len(cols)}개 × {n}일 ({dates[0]:%Y-%m-%d}~{dates[-1]:%Y-%m-%d})")
r.p(f"- 결제 기록이 없는 코호트·일은 0으로 채우지 않고 빈칸(NaN)으로 둡니다. 이번 자료의 빈칸: **{n_missing}칸**. 날짜 정렬·중복·하루 간격 연속은 `validate_panel`로 검사합니다.")
O_EVAL = origins(n, MCFG["split"]["min_train_days"], MCFG["split"]["step_days"], full_horizon=7)
O_ALL = origins(n, MCFG["split"]["min_train_days"], MCFG["split"]["step_days"], full_horizon=None)
r.p(f"- 분할: rolling-origin. 첫 예측 기준일(prediction_origin)은 {MCFG['split']['min_train_days']}일(8주) 학습 뒤인 {dates[O_EVAL[0]]:%m/%d}, 1주씩 이동. 기준일마다 그날까지의 자료만 모델에 넘기고 다음 1~7일을 예측합니다. 학습창은 자료 시작일~기준일(늘어나는 창).")
r.p(f"- 예측 평가 기준일 {len(O_EVAL)}개 ({dates[O_EVAL[0]]:%m/%d}~{dates[O_EVAL[-1]]:%m/%d}), 모든 코호트가 같은 분할. h=1과 h=7을 따로 봅니다. 탐지용 잔차는 마지막 주까지 덮도록 기준일 {len(O_ALL)}개를 씁니다.")
r.p("- 예측 요청마다 `prediction_origin`, `forecast_horizon`, `target_date`, `feature_available_at`(쓴 특징 중 가장 늦은 관측일 = 기준일), `trained_until`을 남깁니다([예측_전체.csv](예측_전체.csv)).")

r.h3("모델 사다리")
r.p("| 순서 | 방법 | 내용 |")
r.p("|---|---|---|")
r.p("| 0a | seasonal naive | 전 주 같은 요일 값 |")
r.p(f"| 0b | 같은 요일 평균 | 기준일 이전 같은 요일 최근 **n={MCFG['models']['0b']['n_weeks']}주** 평균 (공휴일 제외) |")
r.p("| 0b8 (참고) | 같은 요일 8주 평균 | EDA-6 경보 규칙의 기대값과 같음 |")
r.p(f"| 1 | STLForecast | STL(주기 7, robust)로 요일 계절을 빼고 남은 부분은 {MCFG['models']['1']['deseasonalized_model']}. {MCFG['models']['1']['holiday']} |")
r.p("| 2 | LightGBM | 기준일마다 28개 코호트를 한 모델로 학습. 특징: " + ", ".join(MCFG["models"]["2"]["features"]) + ". 하이퍼파라미터 고정(튜닝 안 함) |")

MODELS = {
    "0a": SeasonalNaive(HOL),
    "0b": SameWeekdayMean(HOL, n_weeks=MCFG["models"]["0b"]["n_weeks"], min_obs=MCFG["models"]["0b"]["min_obs"]),
    "0b8": SameWeekdayMean(HOL, n_weeks=8, min_obs=2),
    "1": STLModel(HOL),
    "2": LGBMModel(HOL, params={k: v for k, v in MCFG["models"]["2"]["params"].items()}),
}
LABEL = {"0a": "0a seasonal naive", "0b": "0b 같은 요일 4주", "0b8": "0b8 같은 요일 8주(EDA-6 기대값)", "1": "1 STLForecast", "2": "2 LightGBM"}
RES = {k: cached(f"resid_{k}", lambda m=m: ModelResid(Y, m, O_ALL)) for k, m in MODELS.items()}

# ------------------------------------------------------------ 1. 예측 평가
r.h("1. 예측 성능 (rolling-origin)")
fc = pd.concat([RES[k].fc for k in MODELS], ignore_index=True)
fc["model"] = fc["model"].replace({"0b4": "0b"})
fc = add_intervals(attach_actual(fc, Y), NOMINAL, MCFG["interval"]["min_past_errors"])
fc["city"] = fc["cohort"].map(lambda c: c[0])
fc["age_g"] = fc["cohort"].map(age_of)
fc["near_holiday"] = fc["target_date"].map(lambda d: any(abs((d - h).days) <= 1 for h in HOL))
ev = fc[fc["prediction_origin"].isin(dates[O_EVAL])].copy()
out_fc = ev.assign(cohort=ev["cohort"].map(lambda c: " ".join(c)))
out_fc.to_csv(r.dir / "예측_전체.csv", index=False, encoding="utf-8-sig")

m_city = forecast_metrics(ev, ["model", "city", "forecast_horizon"])
m_city_nh = forecast_metrics(ev[~ev["near_holiday"]], ["model", "city", "forecast_horizon"])
m_age = forecast_metrics(ev, ["model", "city", "age_g", "forecast_horizon"])
m_age.reset_index().drop(columns=["mae"], errors="ignore").to_csv(r.dir / "예측_지표_연령.csv", index=False, encoding="utf-8-sig")
m_city.reset_index().drop(columns=["mae"], errors="ignore").to_csv(r.dir / "예측_지표_도시.csv", index=False, encoding="utf-8-sig")

ORDER = ["0a", "0b", "0b8", "1", "2"]
tab = []
for k in ORDER:
    for c in CITIES:
        row = {"모델": LABEL[k], "도시": c}
        for h in (1, 7):
            x = m_city.loc[(k, c, h)]
            row[f"h={h} 상대 MAE"] = x["rel_mae"]
            row[f"h={h} 구간 포함률"] = x["coverage"]
            row[f"h={h} 상대 MAE(공휴일 앞뒤 제외)"] = m_city_nh.loc[(k, c, h), "rel_mae"]
        tab.append(row)
tab = pd.DataFrame(tab).set_index("모델")
r.p(f"상대 MAE = 코호트 MAE(결제건수) ÷ 코호트 평균 실제값, 도시 안 14개 코호트 평균. 평가 대상일 = 코호트마다 기준일 {len(O_EVAL)}개 × h 하나 = {len(O_EVAL)}일. 구간 포함률은 명목 {NOMINAL:.0%} 경험적 구간(같은 모델·코호트의 과거 오차, 목표일이 기준일 이전인 오차가 {MCFG['interval']['min_past_errors']}개 이상일 때만)이 실제값을 포함한 비율입니다.")
fmt = {"도시": "{}"}
for h in (1, 7):
    fmt.update({f"h={h} 상대 MAE": "{:.1%}", f"h={h} 구간 포함률": "{:.0%}", f"h={h} 상대 MAE(공휴일 앞뒤 제외)": "{:.1%}"})
r.table(tab, fmt)
r.p("- 결제건수는 연령별 배율이 곱해진 값이라 오차는 상대 MAE로만 비교합니다. 원천 규모가 드러나는 건수 단위 MAE는 싣지 않습니다.")
r.p("- 평가 기간에 추석 연휴(10/3~10/9)가 들어 있습니다. 앞선 학습 기간의 공휴일은 8/15 하루뿐이라 어떤 모델도 연휴 효과를 제대로 배우지 못합니다. 그래서 공휴일 앞뒤 1일을 뺀 값도 함께 적었습니다.")

r.h3("도시 × 연령 (상대 MAE)")
for h in (1, 7):
    t = m_age.xs(h, level="forecast_horizon")["rel_mae"].unstack("model")[ORDER]
    t = t.reindex(pd.MultiIndex.from_product([CITIES, AGE_ORDER], names=["city", "age_g"]))
    t.columns = [LABEL[c] for c in t.columns]
    t["가장 나은 모델"] = t.idxmin(axis=1).str.split(" ").str[0]
    r.p(f"**h={h}** (기준선 0a·0b 행 포함, 학습창 = 자료 시작일~기준일)")
    r.table(t.reset_index().set_index("city"), {"age_g": "{}", **{c: "{:.1%}" for c in t.columns if c != "가장 나은 모델"}, "가장 나은 모델": "{}"})
r.p("- 80대+와 춘천 20대는 하루하루 흔들림이 커서 어떤 모델이든 상대 오차가 큽니다.")

fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
for ax, c in zip(axes, CITIES):
    for k, mk in zip(ORDER, ["o", "s", "^", "D", "x"]):
        ax.plot(AGE_ORDER, [m_age.loc[(k, c, a, 1), "rel_mae"] * 100 for a in AGE_ORDER], marker=mk, label=LABEL[k])
    ax.set_title(f"{c}: h=1 상대 MAE (%)")
axes[0].legend(fontsize=8)
r.fig(fig, "f01_rel_mae_by_age.png", "연령별 h=1 상대 MAE")

# ------------------------------------------------------------ 2. 채택 결정
r.h("2. 채택 결정 (규칙은 비교 전에 `configs/models.yaml`에 커밋)")
dec = {"computed_by": "notebooks/m1_expected_activity.py", "metric": "상대 MAE (평가 대상일 전체)", "rows": {}}
best_base = {}
for c in CITIES:
    for h in (1, 7):
        b = {k: float(m_city.loc[(k, c, h), "rel_mae"]) for k in ("0a", "0b")}
        bk = min(b, key=b.get)
        best_base[(c, h)] = (bk, b[bk])
        dec["rows"][f"{c} h={h}"] = {"0a": round(b["0a"], 4), "0b": round(b["0b"], 4), "best_baseline": bk,
                                       "1": round(float(m_city.loc[("1", c, h), "rel_mae"]), 4),
                                       "2": round(float(m_city.loc[("2", c, h), "rel_mae"]), 4)}
stl_ok = {(c, h): m_city.loc[("1", c, h), "rel_mae"] <= (1 - MARGIN) * best_base[(c, h)][1] for c in CITIES for h in (1, 7)}
stl_adopt = all(stl_ok.values())
ref = {(c, h): min(best_base[(c, h)][1], m_city.loc[("1", c, h), "rel_mae"]) for c in CITIES for h in (1, 7)}
lgb_ok = {(c, h): m_city.loc[("2", c, h), "rel_mae"] <= (1 - MARGIN) * ref[(c, h)] for c in CITIES for h in (1, 7)}
old_ok = {}
for c in CITIES:
    for h in (1, 7):
        bb = min(m_age.loc[("0a", c, "80대+", h), "rel_mae"], m_age.loc[("0b", c, "80대+", h), "rel_mae"])
        old_ok[(c, h)] = m_age.loc[("2", c, "80대+", h), "rel_mae"] <= bb
lgb_adopt = all(lgb_ok.values()) and all(old_ok.values())
base_pick = pd.Series({f"{c} h={h}": best_base[(c, h)][0] for c in CITIES for h in (1, 7)})
base_choice = base_pick.mode()[0]
dec["baseline_choice"] = {"per_city_h": base_pick.to_dict(), "overall": base_choice}
dec["stl"] = {"adopted": bool(stl_adopt), "rule": MCFG["adoption"]["stl_rule"],
              "pass": {f"{c} h={h}": bool(v) for (c, h), v in stl_ok.items()}}
dec["lgbm"] = {"adopted": bool(lgb_adopt), "rule": MCFG["adoption"]["lgbm_rule"],
               "pass_overall": {f"{c} h={h}": bool(v) for (c, h), v in lgb_ok.items()},
               "pass_80plus_not_worse": {f"{c} h={h}": bool(v) for (c, h), v in old_ok.items()}}
adopted_fc = "2" if lgb_adopt else ("1" if stl_adopt else base_choice)
dec["adopted_forecast_model"] = adopted_fc

drows = []
for c in CITIES:
    for h in (1, 7):
        bk, bv = best_base[(c, h)]
        drows.append({"도시·h": f"{c} h={h}", "더 나은 기준선": f"{bk} ({bv:.1%})",
                      "STL": f"{m_city.loc[('1', c, h), 'rel_mae']:.1%} ({m_city.loc[('1', c, h), 'rel_mae'] / bv - 1:+.1%})",
                      "STL 기준 통과(≤ −5%)": "통과" if stl_ok[(c, h)] else "미달",
                      "LightGBM": f"{m_city.loc[('2', c, h), 'rel_mae']:.1%} ({m_city.loc[('2', c, h), 'rel_mae'] / ref[(c, h)] - 1:+.1%} vs 기준선·STL 중 나은 것)",
                      "LightGBM 기준 통과": "통과" if lgb_ok[(c, h)] else "미달",
                      "LightGBM 80대+ 악화 없음": "통과" if old_ok[(c, h)] else "미달"})
r.table(pd.DataFrame(drows).set_index("도시·h"))
r.p(f"- STLForecast: **{'채택' if stl_adopt else '채택 안 함'}**. 규칙: {MCFG['adoption']['stl_rule']}")
r.p(f"- LightGBM: **{'채택' if lgb_adopt else '채택 안 함'}**. 규칙: {MCFG['adoption']['lgbm_rule']}")
r.p(f"- 기준선 중에서는 도시·h 네 칸 중 {int((base_pick == base_choice).sum())}칸에서 **{LABEL[base_choice]}** 가 더 나았습니다.")
if adopted_fc in ("0a", "0b"):
    r.p(f"- 결론: **단순 모델 채택** ({LABEL[adopted_fc]}). 184일·공휴일이 거의 없는 학습 자료로는 복잡한 모델이 기준선을 정해 둔 차이만큼 이기지 못했습니다. 이것도 정당한 결과입니다.")
else:
    r.p(f"- 결론: 예측 모델로 **{LABEL[adopted_fc]}** 채택.")

# ------------------------------------------------------------ 3. 탐지
r.h("3. 모델 잔차로 다시 돌린 경보 실험 (EDA-6과 같은 쌍·같은 정의)")
r.p("이 절의 탐지율은 **관측 후 활동 하락 감지 성능(raw 경보, 반증 전)** 입니다. 실제 고립 탐지율이 아닙니다.")
r.p(f"- 경보 규칙은 EDA-6 그대로입니다(`signals/alarm.py`를 감싼 `models/evaluate.ModelPanel`): 잔차 → 과거 {cfg.lookback}일(공휴일 제외)의 중앙값·MAD로 robust z(**잔차 정규화창 {cfg.lookback}일**) → 창 [t−6, t]에서 z < {cfg.z_thr}인 날이 {cfg.k}일 이상. 두 경로(absolute / local = 같은 날 같은 도시 코호트 잔차 중앙값을 뺀 것). MAD=0이나 과거 관측 부족이면 보류.")
r.p("- 바뀌는 것은 잔차 e(t) = log(y+1) − 기대값의 **기대값** 하나뿐입니다. 모델 기대값은 t보다 앞선 가장 최근 기준일(주 1회 재학습)의 예측입니다(h = 1~7). 같은 요일 평균(0a·0b)은 h ≤ 7이면 매일 계산과 같습니다.")
S_MIN = O_ALL[0] + 1 + cfg.min_past + cfg.min_valid  # 첫 잔차 56 → 첫 z 70 → 첫 판정 가능 경보 74 → 시작일 ≥ 75
rng = np.random.default_rng(0)
p_eda6 = ModelPanel(Y.fillna(0), HOL, cfg, name="EDA-6")
cand_all = [s for s in range(cfg.min_train + cfg.win, n - INJ_LEN + 1) if not p_eda6.near_hol[s:s + INJ_LEN].any()]
pairs_all = [(col, int(s)) for col in cols for s in sorted(rng.choice(cand_all, N_START, replace=False))]
pairs = [(c, s) for c, s in pairs_all if s >= S_MIN]
cand = [s for s in cand_all if s >= S_MIN]
r.p(f"- 쌍: EDA-6과 **같은 난수로 뽑은 같은 (도시, 연령, 성, 시작일) 목록**({len(pairs_all):,}쌍) 중, 모든 모델이 판정할 수 있는 시작일 ≥ {dates[S_MIN]:%m/%d}인 **{len(pairs):,}쌍**만 씁니다(모델 잔차는 8주 학습 뒤인 {dates[O_ALL[0] + 1]:%m/%d}부터 있음). EDA-6 규칙도 이 같은 쌍으로 다시 계산해 나란히 둡니다.")
r.p(f"- 주입: 시작일부터 {INJ_LEN}일 동안 결제건수 × (1 − 하락률) (계단형 10/15/20%), 점진형 = 14일에 걸쳐 0→15% 선형. 주입된 날이 쌓이면 그 뒤 기준일의 모델도 주입된 자료로 다시 학습합니다(실제 운영과 같음).")
r.p("- LightGBM은 28개 코호트를 함께 학습하므로, 주입 실험에서는 다시 학습하지 않고 원래 모델에 주입된 자료로 만든 특징만 넣었습니다(근사). 아래에서 일부 쌍을 다시 학습해 결과가 같은지 확인했습니다.")
VARIANTS = {"step10": step(0.10, INJ_LEN), "step15": step(0.15, INJ_LEN), "step20": step(0.20, INJ_LEN), "ramp15": ramp(0.15, INJ_LEN)}
PATHS = ("local", "absolute", "either")
DET_MODELS = ["EDA-6", "0a", "0b", "0b8", "1", "2"]
DLABEL = {"EDA-6": "EDA-6 규칙 (같은 요일 8주, 매일)", **LABEL}
DLABEL["0b8"] = "0b8 (EDA-6 기대값, 잔차 8주 뒤부터)"


def make_panel(k, Yp=Y, resid=None):
    if k == "EDA-6":
        return ModelPanel(Yp.fillna(0), HOL, cfg, name=k)
    return ModelPanel(Yp, HOL, cfg, resid=resid, name=k)


PANELS = {k: make_panel(k, resid=None if k == "EDA-6" else RES[k]) for k in DET_MODELS}
EX = {k: cached(f"pairs_{k}", lambda k=k: run_pairs(PANELS[k], pairs, VARIANTS, PATHS, SUCCESS_DAYS)) for k in DET_MODELS}


def tr_of(ex, variant, path="local", sel=None):
    a = ex[(ex["variant"] == variant) & (ex["path"] == path)]
    if sel is not None:
        a = a[a["cohort"].map(sel)]
    return transitions(a)


# LightGBM 근사 확인 (범위를 숫자와 함께 적는다)
def lgb_check():
    rr = np.random.default_rng(1)
    sub = [pairs[i] for i in sorted(rr.choice(len(pairs), 40, replace=False))]
    full = ModelResid.__new__(ModelResid)
    full.__dict__.update(RES["2"].__dict__)
    full.retrain = True
    p_full = make_panel("2", resid=full)
    a = run_pairs(PANELS["2"], sub, {"step15": VARIANTS["step15"]}, ("local",), SUCCESS_DAYS)
    b = run_pairs(p_full, sub, {"step15": VARIANTS["step15"]}, ("local",), SUCCESS_DAYS)
    ai, bi = a["inj"].to_numpy(), b["inj"].to_numpy()
    both = (ai == 1) & (bi == 1)
    dd = np.abs(a["delay"].to_numpy() - b["delay"].to_numpy())[both]
    return {"n": len(sub), "agree": int((ai == bi).sum()), "approx_only": int(((ai == 1) & (bi == 0)).sum()),
            "full_only": int(((ai == 0) & (bi == 1)).sum()), "both": int(both.sum()),
            "delay_diff_mean": float(dd.mean()) if len(dd) else float("nan"),
            "delay_diff_max": float(dd.max()) if len(dd) else float("nan"),
            "eval_ok": int(((a["pre"] == 0) & (a["ctrl"] == 0)).sum())}


LGB = cached("lgb_check_v2", lgb_check)
r.p(f"  - 확인 범위: 위 {len(pairs):,}쌍에서 무작위로 고른 **{LGB['n']}쌍**, **15% 계단 주입 · local 경로 · 주입한 쪽 경보 여부**만 비교했습니다(대조 쪽, 10%·20%·점진형, absolute 경로는 다시 학습해 보지 않음).")
r.p(f"  - 결과: 경보 여부 **{LGB['agree']} / {LGB['n']}쌍 일치 ({LGB['agree'] / LGB['n']:.1%})**. 불일치 {LGB['n'] - LGB['agree']}쌍(근사에서만 경보 {LGB['approx_only']}, 다시 학습에서만 경보 {LGB['full_only']}). 양쪽 모두 경보가 난 {LGB['both']}쌍의 지연 차이 평균 {LGB['delay_diff_mean']:.1f}일(최대 {LGB['delay_diff_max']:.0f}일). 40쌍 중 대조 음성(탐지율 분모에 들어가는 쌍)은 {LGB['eval_ok']}쌍입니다.")
r.p("  - 다시 학습한 쪽도 **목표 코호트의 예측만** 새 모델로 바꿨습니다. local 경로에서 빼는 도시 공통요인에 들어가는 나머지 13개 코호트의 잔차는 원래 모델 값을 그대로 썼습니다. 그래서 이 확인은 '완전 재계산과 같다'는 증명이 아니라 근사가 크게 어긋나지 않는지 보는 표본 점검입니다. LightGBM을 채택하려면 모든 코호트 예측과 공통요인을 함께 다시 계산한 결과로 다시 확인해야 합니다.")

# 배경 경보율 (실제 자료)
BG = {k: background(PANELS[k], cand, "local", SUCCESS_DAYS, from_pos=S_MIN) for k in DET_MODELS}


# 합성 오경보율 (AR(1) 정상 시계열)
def synth(k):
    out = []
    for seed in range(N_SYN):
        syn = synthetic_normal(Y.fillna(0), HOL, seed=seed)
        if k == "EDA-6":
            p = make_panel(k, syn)
        else:
            p = make_panel(k, syn, ModelResid(syn, MODELS[k], O_ALL))
        out.append(background(p, cand, "local", SUCCESS_DAYS, from_pos=S_MIN).assign(seed=seed))
    return pd.concat(out)


FA = {k: cached(f"synth_{k}", lambda k=k: synth(k)) for k in DET_MODELS}

# 표
rows = []
for k in DET_MODELS:
    ex = EX[k]
    t15 = tr_of(ex, "step15")
    tr_ = tr_of(ex, "ramp15")
    bgm = bg_summary(BG[k])
    fam = bg_summary(FA[k])
    rows.append({
        "방법": DLABEL[k],
        "10% 탐지": tr_of(ex, "step10")["탐지"], "15% 탐지": t15["탐지"], "20% 탐지": tr_of(ex, "step20")["탐지"],
        "평가 가능 n": t15["평가 가능"], "대조 음성 n": t15["대조 음성"], "새 탐지 n": t15["새 탐지"],
        "대조 경보": t15["대조 경보"], "순증 경보율 차이(%p)": t15["순증 경보율 차이(%p)"],
        "0→1": t15["0→1"], "1→0": t15["1→0"], "1→1": t15["1→1"], "제외(경보 중/보류)": f"{t15['시작 전 경보 중(제외)']}/{t15['시작 전 보류(제외)']}",
        "지연 중앙값(일)": t15["지연 중앙값(일)"], "점진 15% 탐지": tr_["탐지"], "점진 지연 중앙값(일)": tr_["지연 중앙값(일)"],
        "배경: 새로 경보 난 창": bgm["새로 경보 난 창"], "배경: 경보일/판정 가능 코호트·일": bgm["경보일 / 판정 가능 코호트·일"],
        "합성 오경보: 새로 경보 난 창": fam["새로 경보 난 창"], "합성 오경보: 경보일/판정 가능 코호트·일": fam["경보일 / 판정 가능 코호트·일"],
    })
det = pd.DataFrame(rows).set_index("방법")
det.to_csv(r.dir / "탐지_비교.csv", encoding="utf-8-sig")
r.h3("전체 (local 경로, 같은 쌍)")
r.p(f"탐지율 = 0→1 / (0→0 + 0→1) = 새 탐지 n / 대조 음성 n. 평가 가능 n = 시작 전날 경보 중도 보류도 아닌 쌍. 순증 경보율 차이 = (주입 경보율 − 대조 경보율), %p. 배경 경보율 = 실제 자료 무주입, 분모는 평가창(14일, 공휴일 앞뒤 제외, 시작일 ≥ {dates[S_MIN]:%m/%d}, {len(cand)}개 시작일 × 코호트)과 **경보 판정 가능 코호트·일**. 합성 오경보율 = AR(1) 정상 합성 시계열 {N_SYN}개, 같은 분모.")
pct = "{:.0%}"
r.table(det[["10% 탐지", "15% 탐지", "20% 탐지", "평가 가능 n", "대조 음성 n", "새 탐지 n", "대조 경보", "순증 경보율 차이(%p)", "지연 중앙값(일)", "점진 15% 탐지", "점진 지연 중앙값(일)"]],
        {"10% 탐지": pct, "15% 탐지": pct, "20% 탐지": pct, "평가 가능 n": "{:,.0f}", "대조 음성 n": "{:,.0f}", "새 탐지 n": "{:,.0f}",
         "대조 경보": "{:.1%}", "순증 경보율 차이(%p)": "{:+.0f}%p", "지연 중앙값(일)": "{:.0f}", "점진 15% 탐지": pct, "점진 지연 중앙값(일)": "{:.0f}"})
r.table(det[["0→1", "1→0", "1→1", "제외(경보 중/보류)", "배경: 새로 경보 난 창", "배경: 경보일/판정 가능 코호트·일", "합성 오경보: 새로 경보 난 창", "합성 오경보: 경보일/판정 가능 코호트·일"]],
        {"0→1": "{:,.0f}", "1→0": "{:,.0f}", "1→1": "{:,.0f}", "제외(경보 중/보류)": "{}", "배경: 새로 경보 난 창": "{:.1%}",
         "배경: 경보일/판정 가능 코호트·일": "{:.2%}", "합성 오경보: 새로 경보 난 창": "{:.1%}", "합성 오경보: 경보일/판정 가능 코호트·일": "{:.2%}"})
r.p("- 배경 경보율은 실제 자료에서 무주입으로 난 경보라 **오경보율이 아닙니다**(그 기간에 진짜 이상이 없었는지 모름). 오경보율은 합성 조건에서만 씁니다. 두 값을 같은 숫자로 발표하지 않습니다.")
r.p("- **위 표의 탐지율은 모델마다 분모가 조금 다릅니다**('대조 음성 n' 열). 모델마다 대조에서 이미 울린 쌍(1→1, 1→0)과 시작 전 경보 중인 쌍이 다르기 때문입니다. 그래서 바로 아래에 모든 모델에 같은 쌍을 쓰는 표를 따로 둡니다.")


def _sel(k, variant, path="local"):
    a = EX[k]
    return a[(a["variant"] == variant) & (a["path"] == path)].set_index(["cohort", "start"])


def common_mask(variant="step15", path="local", ctrl_negative=True):
    """모든 탐지 모델에서 '시작 전 경보 없음(pre=0)' (+ ctrl_negative면 '대조 음성(ctrl=0)')인 쌍만 True (공통 분모)."""
    keys = None
    for k in DET_MODELS:
        a = _sel(k, variant, path)
        ok = (a["pre"] == 0) & ((a["ctrl"] == 0) if ctrl_negative else True)
        keys = ok if keys is None else (keys & ok.reindex(keys.index, fill_value=False))
    return keys


r.h3("같은 분모로 다시 본 탐지율")
r.p("분모가 어떻게 줄어드는지 단계별로 적습니다(15% 계단, local 경로).")
r.p()
r.p("| 단계 | 쌍 수 |")
r.p("|---|---|")
r.p(f"| EDA-6 원래 쌍 (코호트 28개 × 시작일 30곳) | {len(pairs_all):,} |")
r.p(f"| 모든 모델의 잔차가 있는 쌍 (시작일 ≥ {dates[S_MIN]:%m/%d}) | {len(pairs):,} |")
for k in DET_MODELS:
    t = tr_of(EX[k], "step15")
    r.p(f"| {DLABEL[k]}: 평가 가능 / 대조 음성 | {t['평가 가능']:,} / {t['대조 음성']:,} |")
CM = {v: common_mask(v) for v in ("step10", "step15", "step20", "ramp15")}
r.p(f"| 공통 평가 가능 (모든 모델에서 시작 전 경보 중·보류 아님) | {int(common_mask('step15', ctrl_negative=False).sum()):,} |")
r.p(f"| **공통 대조 음성** (모든 모델에서 대조 음성 = 아래 표의 분모) | **{int(CM['step15'].sum()):,}** |")
r.p()
crow = []
for k in DET_MODELS:
    row = {"방법": DLABEL[k]}
    for v, lab in (("step10", "10%"), ("step15", "15%"), ("step20", "20%"), ("ramp15", "점진 15%")):
        a = _sel(k, v)
        sel = a[CM[v].reindex(a.index, fill_value=False)]
        row[f"{lab} 탐지"] = sel["inj"].mean()
        row[f"{lab} 새 탐지 / 분모"] = f"{int(sel['inj'].sum())} / {len(sel)}"
        if v == "step15":
            row["15% 지연 중앙값(일)"] = sel.loc[sel["inj"] == 1, "delay"].median()
    crow.append(row)
cdet = pd.DataFrame(crow).set_index("방법")
cdet.to_csv(r.dir / "탐지_비교_공통분모.csv", encoding="utf-8-sig")
r.table(cdet[["10% 탐지", "15% 탐지", "15% 새 탐지 / 분모", "20% 탐지", "점진 15% 탐지", "점진 15% 새 탐지 / 분모", "15% 지연 중앙값(일)"]],
        {"10% 탐지": "{:.1%}", "15% 탐지": "{:.1%}", "15% 새 탐지 / 분모": "{}", "20% 탐지": "{:.1%}", "점진 15% 탐지": "{:.1%}",
         "점진 15% 새 탐지 / 분모": "{}", "15% 지연 중앙값(일)": "{:.0f}"})
r.p(f"- 비교 대상: 코호트 28개(두 도시 × 연령 7개 × 성), 시작일 ≥ {dates[S_MIN]:%m/%d}인 {len(pairs):,}쌍 중 **모든 모델에서 대조 음성인 쌍**. 주입 길이 14일, local 경로, 경보는 주입 시작일부터 14일 안. 모델 기대값은 모두 같은 규칙(t보다 앞선 가장 최근 기준일의 h = 1~7일 예측, 주 1회 재학습)입니다.")
r.p("- 공통 분모는 하락 폭마다 따로 잡지만 대조 결과와 시작 전 상태가 같아 실제로는 같은 쌍입니다('새 탐지 / 분모' 열로 확인).")
E6C, B0C = cdet.loc[DLABEL["EDA-6"], "15% 탐지"], cdet.loc[DLABEL["0b"], "15% 탐지"]
r.p(f"- 같은 분모에서도 결론은 같습니다: EDA-6 규칙 {E6C:.1%}, 0b {B0C:.1%}, STL {cdet.loc[DLABEL['1'], '15% 탐지']:.1%}, LightGBM {cdet.loc[DLABEL['2'], '15% 탐지']:.1%}. 1%p 안팎의 차이로는 우열을 가리지 않습니다(같은 코호트의 쌍이 서로 겹쳐 독립이 아님).")
ex_full = cached("pairs_EDA-6_full", lambda: run_pairs(PANELS["EDA-6"], pairs_all, {"step15": VARIANTS["step15"], "ramp15": VARIANTS["ramp15"]}, ("local",), SUCCESS_DAYS))
tf, tfr = tr_of(ex_full, "step15"), tr_of(ex_full, "ramp15")
r.p(f"- 재현 확인: 같은 코드로 EDA-6 규칙을 **EDA-6의 전체 {len(pairs_all):,}쌍**에 돌리면 15% 탐지 {tf['탐지']:.0%} (새 탐지 {tf['새 탐지']} / 대조 음성 {tf['대조 음성']}, 평가 가능 {tf['평가 가능']}), 지연 중앙값 {tf['지연 중앙값(일)']:.0f}일, 점진 {tfr['새 탐지']} / {tfr['대조 음성']}입니다. EDA-6 리포트와 같은 프로토콜인지 확인하는 값입니다. 위 표는 시작일이 늦은 쌍만 쓰므로 이 값과 다릅니다.")

# 경로별
pr = []
for k in DET_MODELS:
    row = {"방법": DLABEL[k]}
    for path in PATHS:
        t = tr_of(EX[k], "step15", path)
        row[f"{path} 15% 탐지"] = t["탐지"]
        row[f"{path} 대조 경보"] = t["대조 경보"]
    pr.append(row)
r.h3("경로별 (15% 계단)")
r.table(pd.DataFrame(pr).set_index("방법"), {c: "{:.0%}" if "탐지" in c else "{:.1%}" for c in pr[0] if c != "방법"})
r.p("- `either`는 absolute·local 중 하나라도 울리면 경보입니다(둘 다 보류일 때만 보류).")

# 연령별
r.h3("도시 × 연령 (local, 15% 계단 탐지 / 배경 경보율)")
age_rows = []
for c in CITIES:
    for a in AGE_ORDER:
        row = {"city": c, "age_g": a}
        for k in DET_MODELS:
            t = tr_of(EX[k], "step15", sel=lambda col, c=c, a=a: col[0] == c and col[1] == a)
            b = BG[k][BG[k]["cohort"].map(lambda col, c=c, a=a: col[0] == c and col[1] == a)]
            row[f"{k} 탐지"] = t["탐지"]
            row[f"{k} 배경"] = b["새로 경보 난 창"].mean()
            row[f"{k} n"] = t["대조 음성"]
        age_rows.append(row)
age_det = pd.DataFrame(age_rows).set_index("city")
age_det.to_csv(r.dir / "탐지_연령.csv", encoding="utf-8-sig")
show = ["age_g"] + [f"{k} 탐지" for k in DET_MODELS] + [f"{k} 배경" for k in ("EDA-6", "0b", "1", "2")] + ["EDA-6 n"]
r.table(age_det[show], {"age_g": "{}", **{c: "{:.0%}" if "탐지" in c else ("{:.1%}" if "배경" in c else "{:,.0f}") for c in show[1:]}})
r.p("- '탐지'의 분모는 그 칸의 대조 음성 쌍 수(EDA-6 n 열, 모델마다 조금 다를 수 있음, 전체 값은 [탐지_연령.csv](탐지_연령.csv)). '배경' = 실제 자료 무주입, 평가창 중 새로 경보 난 창 비율.")

# 지연 분포와 주입 직전 낮은 날
r.h3("지연과 주입 직전 창의 낮은 날 수")
r.p(f"새 하락만으로 낮은 날 {cfg.k}일이 다 쌓이면 지연은 최소 2일입니다. 하지만 주입 직전 창에 이미 낮은 날이 있으면 0~1일에도 울릴 수 있습니다. 그래서 지연을 주입 직전 6일(첫 판정 창에 들어가는 시작 전 날들, EDA-6과 같은 정의)의 낮은 날 수별로 나눴습니다 (15% 계단, local, 새 탐지 쌍).")
dl = []
for k in DET_MODELS:
    a = EX[k][(EX[k]["variant"] == "step15") & (EX[k]["path"] == "local") & (EX[k]["pre"] == 0) & (EX[k]["ctrl"] == 0) & (EX[k]["inj"] == 1)]
    for pl, g in a.groupby("pre_low"):
        dl.append({"방법": DLABEL[k], "직전 낮은 날": int(pl), "쌍 수": len(g), "지연 중앙값(일)": g["delay"].median(),
                   "지연 0~1일 쌍": int((g["delay"] <= 1).sum())})
r.table(pd.DataFrame(dl).set_index("방법"), {"직전 낮은 날": "{:.0f}", "쌍 수": "{:,.0f}", "지연 중앙값(일)": "{:.0f}", "지연 0~1일 쌍": "{:,.0f}"})

fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
for ax, c in zip(axes, CITIES):
    for k, mk in zip(DET_MODELS, ["o", "s", "^", "v", "D", "x"]):
        ax.plot(AGE_ORDER, [age_det.loc[c].set_index("age_g").loc[a, f"{k} 탐지"] * 100 for a in AGE_ORDER], marker=mk, label=DLABEL[k])
    ax.set_title(f"{c}: 15% 하락 탐지 (local, 반증 전)")
    ax.set_ylim(-3, 105)
axes[0].set_ylabel("새로 경보 난 쌍 비율 (%)")
axes[0].legend(fontsize=7)
r.fig(fig, "f02_detection_by_model.png", "모델별 탐지율")

# 탐지 기대값 교체 결정
e6 = det.loc[DLABEL["EDA-6"]]
det_dec = {}
for k in ("0a", "0b", "1", "2"):
    d = det.loc[DLABEL[k]]
    fc_adopted = (k == adopted_fc)
    ok = (fc_adopted and d["15% 탐지"] >= e6["15% 탐지"]
          and d["배경: 새로 경보 난 창"] <= e6["배경: 새로 경보 난 창"] + 0.005
          and d["합성 오경보: 새로 경보 난 창"] <= e6["합성 오경보: 새로 경보 난 창"] + 0.005)
    det_dec[k] = {"adopted": bool(ok), "forecast_adopted": bool(fc_adopted),
                  "detect15_local": round(float(d["15% 탐지"]), 4), "eda6_detect15_local": round(float(e6["15% 탐지"]), 4),
                  "background_new_window": round(float(d["배경: 새로 경보 난 창"]), 4), "eda6_background_new_window": round(float(e6["배경: 새로 경보 난 창"]), 4),
                  "synthetic_new_window": round(float(d["합성 오경보: 새로 경보 난 창"]), 4), "eda6_synthetic_new_window": round(float(e6["합성 오경보: 새로 경보 난 창"]), 4),
                  "median_delay": float(d["지연 중앙값(일)"])}
det_choice = next((k for k, v in det_dec.items() if v["adopted"]), "EDA-6")
dec["detection"] = {"rule": MCFG["adoption"]["detection_rule"], "by_model": det_dec, "expected_value_for_alarm": det_choice,
                    "pairs": len(pairs), "start_from": str(dates[S_MIN].date())}
r.p(f"- 채택된 예측 모델 0b의 15% 탐지 {det_dec['0b']['detect15_local']:.1%} vs EDA-6 규칙 {det_dec['0b']['eda6_detect15_local']:.1%} (각자 분모. 공통 분모에서는 {B0C:.1%} vs {E6C:.1%}), 배경(새로 난 창) {det_dec['0b']['background_new_window']:.1%} vs {det_dec['0b']['eda6_background_new_window']:.1%}, 합성 오경보(새로 난 창) {det_dec['0b']['synthetic_new_window']:.1%} vs {det_dec['0b']['eda6_synthetic_new_window']:.1%}.")
r.p(f"- 경보 기대값 교체: **{'EDA-6 규칙 유지' if det_choice == 'EDA-6' else DLABEL[det_choice] + '로 교체'}**. 규칙: {MCFG['adoption']['detection_rule']}")

# ------------------------------------------------------------ 4. 관측 후 이탈 판정
r.h("4. 관측 후 이탈 판정: 같은 날 공통요인·강수")
r.p("이미 관측한 날을 판정할 때는 같은 날 다른 코호트의 변화(도시 공통요인)와 같은 날 ASOS 강수를 쓸 수 있습니다. **예측 모델(1~2절)은 이 정보를 쓰지 않습니다.** 여기서는 그 정보가 이탈을 얼마나 설명하는지 사후로 계산합니다. 강수로 설명된다고 자동 기각하지 않습니다.")
pk = det_choice
P = PANELS[pk]
dmask = np.arange(n) >= S_MIN
rows = []
for c in CITIES:
    jj = np.where(P.cities == c)[0]
    rainy = (rain[c].reindex(dates).fillna(0).to_numpy() >= 1.0)
    E, S = P.E[:, jj], P.S[:, jj]
    ok = dmask[:, None] & ~P.is_hol[:, None] & ~np.isnan(E) & ~np.isnan(S)
    e, s = E[ok], S[ok]
    rr = np.broadcast_to(rainy[:, None], E.shape)[ok].astype(float)
    tot = np.var(e)
    r2_s = 1 - np.var(e - s) / tot
    X = np.column_stack([np.ones_like(e), s, rr])
    beta, *_ = np.linalg.lstsq(X, e, rcond=None)
    r2_sr = 1 - np.var(e - X @ beta) / tot
    Xr = np.column_stack([np.ones_like(e), rr])
    br, *_ = np.linalg.lstsq(Xr, e, rcond=None)
    r2_r = 1 - np.var(e - Xr @ br) / tot
    rows.append({"도시": c, "코호트·일": int(ok.sum()), "비 온 날(≥1mm)": int(rainy[dmask].sum()),
                 "강수만 R²": r2_r, "공통요인만 R²": r2_s, "공통요인+강수 R²": r2_sr,
                 "비 온 날 평균 잔차": float(e[rr == 1].mean()), "안 온 날 평균 잔차": float(e[rr == 0].mean())})
p01 = pd.DataFrame(rows).set_index("도시")
r.p(f"기대값 = {DLABEL[pk]}. 대상 = {dates[S_MIN]:%m/%d} 이후, 공휴일 제외 코호트·일. absolute 잔차의 분산 중 같은 날 정보로 설명되는 비율(R², 사후 기술 통계):")
r.table(p01, {"코호트·일": "{:,.0f}", "비 온 날(≥1mm)": "{:.0f}", "강수만 R²": "{:.1%}", "공통요인만 R²": "{:.1%}", "공통요인+강수 R²": "{:.1%}",
              "비 온 날 평균 잔차": "{:+.3f}", "안 온 날 평균 잔차": "{:+.3f}"})
rec = records(P, rain=rain)
rec = rec[rec["alarm_date"] >= dates[S_MIN]]
n_rec = len(rec)
n_rain = int((rec["rain_explained"] == True).sum()) if n_rec else 0  # noqa: E712
n_rain_na = int(rec["rain_explained"].isna().sum()) if n_rec else 0
r.p(f"- 이 기대값으로 {dates[S_MIN]:%m/%d} 이후 실제 자료에서 난 경보(7일 묶음): **{n_rec}건**. 그중 과거로 추정한 비 효과를 빼면 경보 조건을 넘지 않는 경보 {n_rain}건(비 효과를 추정할 과거가 모자라 판단 못 한 경보 {n_rain_na}건). 기각하지 않고 '날씨로 설명되는 부분'과 '보정 후 남는 이탈'을 함께 기록합니다.")
if n_rec:
    t = rec.groupby("path")[["weather_explained_abs", "after_weather_abs"]].mean()
    t["경보 수"] = rec.groupby("path").size()
    t.index.name = "경로(묶음)"
    r.table(t.rename(columns={"weather_explained_abs": "날씨로 설명(평균 log)", "after_weather_abs": "보정 후 남는 이탈(평균 log)"}),
            {"날씨로 설명(평균 log)": "{:+.3f}", "보정 후 남는 이탈(평균 log)": "{:+.3f}", "경보 수": "{:,.0f}"})
    rec.to_csv(r.dir / "경보_목록_관측후.csv", index=False, encoding="utf-8-sig")
r.p("- 강수로 경보 수가 줄어드는 것은 '통계적 경보일 감소'일 뿐 오탐 감소가 아닙니다. 관측소 한 곳(서울 108, 춘천 101) 값입니다.")
r.p("- 비는 도시 전체에 함께 내리므로 강수 효과는 같은 날 공통요인에 거의 다 들어 있습니다(공통요인에 강수를 더해도 R²가 거의 늘지 않음). 그래서 local 경로에서는 강수로 따로 설명되는 부분이 작고, absolute 경로에서 강수 설명분이 큽니다.")
r.p("- 공통요인은 같은 날 다른 코호트 결제라 **관측 후 판정에만** 씁니다. 미래 예측에는 기준일까지의 과거 공통요인만 씁니다.")

# ------------------------------------------------------------ 5. PELT
r.h("5. PELT 변화점 (사후 분석, 경보 아님)")
r.p(f"`{PELT_LABEL}`. ruptures PELT(l2, 최소 구간 5일)를 잔차(absolute, 공휴일 빈칸)에 적용했습니다. 패널티 = β × σ² × ln(n), σ = 잔차 차분의 robust 표준편차 / √2. 잔차는 {DLABEL[pk]} 기준.")
BETAS = [2, 4, 8, 16]
Epk = P.E.copy()
Epk[P.is_hol] = np.nan
Epk[:S_MIN] = np.nan
sens = []
for b in BETAS:
    cps = [pelt(Epk[:, j], b) for j in range(len(cols))]
    sens.append({"β": b, "코호트당 변화점 평균": np.mean([len(x) for x in cps]), "변화점 1개 이상 코호트": np.mean([len(x) > 0 for x in cps]),
                 "변화점 날짜 예(강남 80대+ 여)": ", ".join(f"{dates[i]:%m/%d}" for i in cps[cols.index(("강남", "80대+", "여성"))]) if ("강남", "80대+", "여성") in cols else ""})
r.p("**패널티 민감도 (실제 자료)**")
r.table(pd.DataFrame(sens).set_index("β"), {"코호트당 변화점 평균": "{:.1f}", "변화점 1개 이상 코호트": "{:.0%}", "변화점 날짜 예(강남 80대+ 여)": "{}"})


def pelt_inj():
    rr = np.random.default_rng(2)
    sub = [pairs[i] for i in sorted(rr.choice(len(pairs), min(200, len(pairs)), replace=False))]
    out = []
    for col, s in sub:
        j = cols.index(col)
        y = Y[col].to_numpy(float).copy()
        y[s:s + INJ_LEN] *= 0.85
        e_inj = P._resid_one(col, y)
        e_ctl = P.E[:, j]
        for kind, e in (("주입", e_inj), ("대조", e_ctl)):
            e = e.copy()
            e[P.is_hol] = np.nan
            e[:S_MIN] = np.nan
            sd = noise_sd(e_ctl[S_MIN:])
            for b in BETAS:
                cps = pelt(e, b, sd=sd)
                near = [c for c in cps if abs(c - s) <= 7]
                err = min((c - s for c in near), key=abs) if near else np.nan
                found, est = prefix_discovery(e, s, min(s + SUCCESS_DAYS - 1, n - 1), b, tol=3, sd=sd) if kind == "주입" else (None, None)
                out.append({"kind": kind, "β": b, "start": s, "cp_near": bool(near), "cp_err": err,
                            "prefix_found": found is not None, "discover_delay": (found - s) if found is not None else np.nan,
                            "est_err": (est - s) if est is not None else np.nan})
    return pd.DataFrame(out)


pi = cached(f"pelt_{pk}", pelt_inj)
g = pi.groupby(["β", "kind"])
pt = pd.DataFrame({"±7일 안 변화점 비율": g["cp_near"].mean(), "변화점 위치 오차 중앙값(일)": g["cp_err"].median(),
                   "변화점 위치 |오차| 중앙값(일)": g["cp_err"].apply(lambda x: x.abs().median())}).unstack("kind")
pt.columns = [f"{a} ({b})" for a, b in pt.columns]
pf = pi[pi["kind"] == "주입"].groupby("β").agg(prefix_found=("prefix_found", "mean"), discover_delay=("discover_delay", "median"), est_err=("est_err", "median"))
pt["시점별(그날까지 구간만) 14일 안 발견"] = pf["prefix_found"]
pt["발견일 − 실제 시작일 중앙값(일)"] = pf["discover_delay"]
pt["추정 변화일 − 실제 시작일 중앙값(일)"] = pf["est_err"]
r.p(f"**주입 실험에서 변화 지점 오차** (15% 계단, 무작위 {pi['start'].count() // (2 * len(BETAS))}쌍. 대조 = 같은 쌍의 주입 안 한 시계열)")
pt_show = pt.copy()
for c in pt.columns:
    f = "{:.0%}" if ("비율" in c or ("발견" in c and "일)" not in c)) else "{:+.0f}"
    pt_show[c] = pt[c].map(lambda v, f=f: "—" if pd.isna(v) else f.format(v))
r.table(pt_show)
r.p("- 사후(전체 구간) PELT는 시작일 뒤 자료까지 보고 변화점을 찾으므로 '그날 알 수 있었던 것'이 아닙니다. 시점별로 쓰려면 그날까지의 구간에만 적용해야 하고, 그때는 **추정 변화일**과 **발견일**이 다릅니다(표 오른쪽 세 칸).")
r.p("- 대조 시계열에서도 ±7일 안에 변화점이 나오는 비율이 있어, PELT 결과만으로 경보를 만들지 않습니다.")

# ------------------------------------------------------------ SHAP
if lgb_adopt:
    from bigcontest.models.explain import LABEL as SHAP_LABEL
    from bigcontest.models.explain import shap_importance

    st = RES["2"].states[O_EVAL[-1]]
    hist = Y.iloc[: O_EVAL[-1] + 1]
    m2 = MODELS["2"]
    Xs = m2._rows(np.log(hist.to_numpy(float) + 1), pd.DatetimeIndex(hist.index), cols, range(27, len(hist) - 1), m2.horizons, True)
    sh = shap_importance(st["model"], Xs[FEATURES].sample(min(3000, len(Xs)), random_state=0))
    r.h("SHAP (모델 출력 설명, 원인 아님)")
    r.table(sh.set_index("feature"), {"mean_abs_shap": "{:.4f}", "note": "{}"})
    sh.to_csv(r.dir / "shap.csv", index=False, encoding="utf-8-sig")
else:
    r.h("SHAP")
    r.p("LightGBM을 채택하지 않았으므로 SHAP은 계산하지 않았습니다(규칙: 채택될 때만).")

# ------------------------------------------------------------ 모델 카드
r.h("모델 카드")
r.p("| 항목 | 내용 |")
r.p("|---|---|")
r.p(f"| 버전 | configs/models.yaml {MCFG['version']}; 모델 0a/0b/0b8/1/2 각 version {', '.join(m.version for m in MODELS.values())} |")
r.p(f"| 학습 자료 | 카드 데이터2 코호트 일별 결제건수 {dates[0]:%Y-%m-%d}~{dates[-1]:%Y-%m-%d}, 28개 코호트 |")
r.p(f"| 학습 기간 | 기준일마다 자료 시작일~기준일. 첫 기준일 {dates[O_ALL[0]]:%m/%d}, 마지막 {dates[O_ALL[-1]]:%m/%d} |")
r.p(f"| trained_until | 예측 행마다 = prediction_origin (마지막 {dates[O_ALL[-1]]:%Y-%m-%d}) |")
r.p(f"| calibrated_until | 경보 임계값(z < {cfg.z_thr}, 7일 중 {cfg.k}일)은 EDA-6에서 정한 값 그대로, 이 리포트에서 다시 맞추지 않음. 채택 기준은 {dates[O_EVAL[-1] + 7]:%Y-%m-%d}까지의 평가로 정함(사후) |")
r.p("| 특징 | 0a/0b: 같은 요일 지난 값. 1: 해당 코호트 지난 값 + 공휴일 달력. 2: 달력, 같은 요일 지난 값, 기준일 값·7일 평균, 과거 도시 공통요인, 코호트 |")
r.p("| 쓰지 않은 것 | 미래 실측 날씨, 같은 미래 날짜의 다른 코호트 결제, 예보(이력 없음) |")
r.p(f"| 채택 | 예측: {LABEL[adopted_fc]}. 경보 기대값: {DLABEL[det_choice]} |")
r.p("| 한계 | 184일·학습 기간 공휴일 1일, 결제건수에 연령 배율이 곱해짐(연령 간 비교 불가), 카드 거주지는 시도 단위, 카드 자료 공급 지연은 반영 안 함(feature_available_at = 기준일), 예측구간은 과거 오차 분위수라 기준일 초반에는 없음 |")

# ------------------------------------------------------------ 결정 기록
text = CFG_PATH.read_text(encoding="utf-8")
text = re.split(r"\ndecisions:", text)[0].rstrip() + "\n\n"
dumped = yaml.safe_dump(dec, allow_unicode=True, sort_keys=False, width=200)
text += "decisions:  # notebooks/m1_expected_activity.py 가 채움\n" + "".join("  " + line + "\n" for line in dumped.splitlines())
CFG_PATH.write_text(text, encoding="utf-8")

# ------------------------------------------------------------ 정리
r.h("정리")
b1 = {c: best_base[(c, 1)] for c in CITIES}
b7 = {c: best_base[(c, 7)] for c in CITIES}
r.p("| 결과 | 값 | 해석 상태 |")
r.p("|---|---|---|")
r.p("| 예측 상대 MAE h=1 (더 나은 기준선 / STL / LightGBM) | " + "; ".join(
    f"{c} {b1[c][0]} {b1[c][1]:.1%} / {m_city.loc[('1', c, 1), 'rel_mae']:.1%} / {m_city.loc[('2', c, 1), 'rel_mae']:.1%}" for c in CITIES) + " | 계산 재현 |")
r.p("| 예측 상대 MAE h=7 (더 나은 기준선 / STL / LightGBM) | " + "; ".join(
    f"{c} {b7[c][0]} {b7[c][1]:.1%} / {m_city.loc[('1', c, 7), 'rel_mae']:.1%} / {m_city.loc[('2', c, 7), 'rel_mae']:.1%}" for c in CITIES) + " | 계산 재현 |")
r.p(f"| 채택 (사전 규칙) | 예측 {LABEL[adopted_fc]}; STL {'채택' if stl_adopt else '안 함'}, LightGBM {'채택' if lgb_adopt else '안 함'}; 경보 기대값 {DLABEL[det_choice]} | 계산 재현 |")
for k in DET_MODELS:
    d = det.loc[DLABEL[k]]
    r.p(f"| 15% 하락 감지(raw 경보, 반증 전): {DLABEL[k]} | {d['15% 탐지']:.0%} (새 탐지 {d['새 탐지 n']:,} / 대조 음성 {d['대조 음성 n']:,}), 순증 {d['순증 경보율 차이(%p)']:+.0f}%p, 지연 중앙값 {d['지연 중앙값(일)']:.0f}일, 배경 {d['배경: 새로 경보 난 창']:.1%}(평가창)·{d['배경: 경보일/판정 가능 코호트·일']:.2%}(판정 가능 코호트·일), 합성 오경보 {d['합성 오경보: 새로 경보 난 창']:.1%}·{d['합성 오경보: 경보일/판정 가능 코호트·일']:.2%} | 계산 재현 |")
r.p(f"| 15% 하락 감지, **공통 분모** {int(CM['step15'].sum()):,}쌍 | " + "; ".join(f"{DLABEL[k].split(' (')[0]} {cdet.loc[DLABEL[k], '15% 탐지']:.1%}" for k in DET_MODELS) + " | 계산 재현 |")
r.p(f"| LightGBM 근사 확인 | {LGB['agree']} / {LGB['n']}쌍 일치 ({LGB['agree'] / LGB['n']:.1%}). 15% 계단·local·주입 쪽만, 목표 코호트만 다시 학습 | 표본 점검 (완전 재계산 아님) |")
r.p(f"| 관측 후 판정: 같은 날 공통요인+강수 R² | " + ", ".join(f"{c} {p01.loc[c, '공통요인+강수 R²']:.0%} (강수만 {p01.loc[c, '강수만 R²']:.0%})" for c in CITIES) + " | 계산 재현 (사후 기술) |")
r.p("| PELT 변화점 | 사후 분석으로만 사용, 경보 아님 | 계산 재현 |")
r.p("| 실제 고립 사건 예측·탐지 성능 | 알 수 없음 (정답 라벨 없음) | 미확인 |")
r.p()
r.p("**한계**")
r.p("- 카드 `daily()`는 결제 기록이 없는 날을 0으로 채웁니다. 이것은 가정입니다. 이 리포트의 모델 입력은 빈칸(NaN)으로 두었고, 이번 자료에는 빈칸이 " + f"{n_missing}칸이었습니다. EDA-6 규칙 행과 합성 시계열은 EDA-6과 같게 0으로 채운 표를 씁니다.")
r.p("- 탐지율은 정해진 크기의 합성 하락을 얼마나 잡는지일 뿐, 실제 사회적 고립을 잡는 비율이 아닙니다. 반증(공휴일·강수 검토) 전 raw 경보 기준입니다.")
r.p("- 14일 평가창들은 같은 시계열에서 하루씩 밀어 만든 것이라 서로 겹칩니다. 독립 표본 수로 보지 않습니다.")
r.save()
print("done")
