"""EDA-5 핵심 인사이트 재현

실행: uv run python notebooks/eda5_insights.py   (EDA-4를 먼저 실행해야 함: eda4_외부데이터결합/행정동_결합.csv 사용)
결과: reports/eda5_인사이트재현/ (README.md, figs/)
"""

import json

import numpy as np
import pandas as pd

from bigcontest.card import AGE_ORDER, daily, load_card2
from bigcontest.config import ROOT, load_config
from bigcontest.external import resident_population, seoul_living
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report
from bigcontest.stats import fmt_p, fmt_perm_p, perm_p, spearman_report
from bigcontest.telecom import AGE_COLS, WKDY_COLS, adm_names, age_cols, grid_id
from bigcontest.weather import load as load_weather

CARD80 = {"강남": "강남 가맹점의 서울 거주 80대+ 고객", "춘천": "춘천 가맹점의 강원 거주 80대+ 고객"}
HOL = {pd.Timestamp(k): v for k, v in load_config()["holidays"].items()}
D = load_config()["interim_dir"]
CITIES = ["강남", "춘천"]
rng = np.random.default_rng(0)
rng_new = np.random.default_rng(1)  # 추가 재표집용 (기존 값이 바뀌지 않게 따로 둠)
rng_rev = np.random.default_rng(2)  # 보완용 재표집 (기존 난수열을 건드리지 않음)
status = []  # (인사이트, 결과, 근거)

r = Report("eda5_인사이트재현", "EDA-5 핵심 인사이트 재현")
r.p("탐색 단계에서 나온 핵심 인사이트를 레포 코드로 다시 확인합니다. 각 절 끝에 **재현 결과**를 적었습니다.")
r.p()
r.p("| 기호 | 뜻 |")
r.p("|---|---|")
r.p("| ✅ 재현 | 같은 결론이 다시 나오고, 기준을 바꿔도 유지됨 |")
r.p("| ⚠️ 부분 재현 | 방향은 같지만 일부만 확인되거나 기준에 따라 달라짐 |")
r.p("| ❌ 재현 안 됨 | 결론을 바꿔야 함 |")
r.p("| ⚠️ 탐색적 방향 재현 | 방향은 같고 우연과도 구분되지만, 한 번(한 계절) 관측이라 탐색 결과로만 씀 |")
r.p()
r.p("**용어**")
r.p()
r.p("| 용어 | 뜻 |")
r.p("|---|---|")
r.p("| 순위상관 ρ | 두 값의 순위가 얼마나 같이 움직이는지(−1~1) |")
r.p("| 순열 p | 값을 무작위로 섞었을 때 지금만큼 강한 결과가 나올 확률. 작을수록 우연이 아님 |")
r.p("| 95% CI | 표본을 다시 뽑아도 값이 대체로 들어갈 범위 |")
r.p("| 하나씩 빼기 | 동(또는 단위)을 하나씩 빼고 다시 계산한 값의 범위. 한 곳 때문에 생긴 결과인지 확인 |")
r.p("| robust z | 평소 값(중앙값)에서 얼마나 벗어났는지. 극단값에 덜 흔들리게 계산한 표준점수 |")

# ============================================================ 1. 중장년 남성
r.h("1. 중장년 남성 1인가구의 소통 결핍 (강남, 시민생활 데이터)")
r.p("시민생활 1인가구 중 '커뮤니케이션이 적은 집단'의 비율을 성·연령대별로 봅니다(2025년 7~12월 합).")
r.p("개포3동은 이 지표가 6개월 내내 0이라 품질 의심 결측으로 둡니다(원인 미확인, [EDA-4 3절](../eda4_외부데이터결합/README.md)). 그래서 **n=21(결측 처리)** 을 기본으로 하고, 원본 0을 그대로 넣은 **n=22** 결과도 함께 적습니다.")
BANDS = ["청년(20~39)", "중장년(40~64)", "노년(65+)"]


def i2(slx, gen, n_boot=2000):
    """1인가구 중 소통 적음 비율의 남/여 비. 분자가 결측인 행은 분모에서도 뺀다."""
    slx = slx.dropna(subset=["comm_low_single"]).copy()
    slx["band"] = pd.cut(slx["age"], [19, 39, 64, 120], labels=BANDS)
    g_ = slx.groupby(["band", "sex"], observed=True)[["comm_low_single", "single"]].sum()
    g_["비율"] = g_["comm_low_single"] / g_["single"]
    t_ = g_["비율"].unstack("sex")
    t_["남/여"] = t_["남"] / t_["여"]
    t_["남성 월평균 인원"] = g_.xs("남", level="sex")["comm_low_single"] / slx["month"].nunique()
    dong_ = slx[slx["band"] == "중장년(40~64)"].groupby(["adm_cd", "sex"])[["comm_low_single", "single"]].sum()
    dr_ = (dong_["comm_low_single"] / dong_["single"]).unstack("sex")
    adms_ = dr_.index.to_numpy()
    boot_ = []
    for _ in range(n_boot):
        s = gen.choice(adms_, len(adms_), replace=True)
        sub = dong_.loc[s]
        m = sub.xs("남", level="sex").sum()
        f = sub.xs("여", level="sex").sum()
        boot_.append((m["comm_low_single"] / m["single"]) / (f["comm_low_single"] / f["single"]))
    lo_, hi_ = np.percentile(boot_, [2.5, 97.5])
    return t_, dong_, int((dr_["남"] > dr_["여"]).sum()), len(dr_), lo_, hi_


sl = seoul_living()
sl22 = seoul_living(quality_na=False)
t, dong, n_male_higher, n_dong, lo, hi = i2(sl, rng)
t22, _, n_male22, n_dong22, lo22, hi22 = i2(sl22, rng_new)
t.index.name = "연령대"
r.table(t, {"남": "{:.1%}", "여": "{:.1%}", "남/여": "{:.2f}", "남성 월평균 인원": "{:,.0f}"})
r.p("(n=21)")
names = adm_names().set_index("adm_cd")
top = dong.xs("남", level="sex")["comm_low_single"].sort_values(ascending=False).head(5)
mid = sl[sl["age"].between(40, 64)].groupby("sex")[["single", "pop"]].sum()
sr = mid["single"] / mid["pop"]
r.p()
r.p("| 기준 | 중장년 남/여 | 동 단위 부트스트랩 95% CI | 남성이 높은 동 |")
r.p("|---|---:|---|---:|")
r.p(f"| n={n_dong} (개포3동 결측 처리, 기본) | {t.loc['중장년(40~64)', '남/여']:.2f}배 | {lo:.2f}~{hi:.2f} | {n_male_higher}/{n_dong} |")
r.p(f"| n={n_dong22} (원본 0 그대로) | {t22.loc['중장년(40~64)', '남/여']:.2f}배 | {lo22:.2f}~{hi22:.2f} | {n_male22}/{n_dong22} |")
r.p()
r.p("- 두 기준의 결론(남성이 더 높음)은 같습니다. n=22에서 남성이 높지 않던 한 동이 바로 0값 이상이 있는 개포3동이었습니다.")
r.p(f"- 해당 인원이 많은 동: {', '.join(names.loc[a, 'adm_nm'] for a in top.index)}")
r.p(f"- 중장년 남성은 시민생활 1인가구 비율이 여성과 비슷한데도({sr['남']:.1%} vs {sr['여']:.1%}) 소통이 적은 비율이 훨씬 높습니다. 고독사 통계(50·60대 남성이 절반 이상)와 같은 방향입니다.")
ok1 = lo > 1.3 and n_male_higher >= n_dong - 4
status.append(("I-2 중장년 남성 1인가구 소통 결핍", "✅ 재현" if ok1 else "⚠️ 부분 재현",
               f"남/여 {t.loc['중장년(40~64)', '남/여']:.2f}배, CI {lo:.2f}~{hi:.2f}, {n_male_higher}/{n_dong}개 동 (n=21). "
               f"원본 0 포함 n=22: {t22.loc['중장년(40~64)', '남/여']:.2f}배, CI {lo22:.2f}~{hi22:.2f}, {n_male22}/{n_dong22}", "계산 재현"))
fig, ax = plt.subplots(figsize=(7, 4))
x = np.arange(len(t))
ax.bar(x - 0.2, t["남"] * 100, 0.4, label="남", color="#2b6cb0")
ax.bar(x + 0.2, t["여"] * 100, 0.4, label="여", color="#d53f8c")
ax.set_xticks(x, t.index)
ax.set_ylabel("1인가구 중 소통 적은 비율 (%)")
ax.set_title("강남: 중장년 남성 1인가구의 소통 결핍이 가장 크다")
ax.legend()
r.fig(fig, "f01_midlife_men.png", "중장년 남성")
r.p(f"**재현 결과: {status[-1][1]}** ({status[-1][2]})")

# ============================================================ 2. 청년 남성
r.h("2. 청년 남성의 외출·소통 결핍 (강남)")
r.p("`외출-커뮤니케이션이 모두 적은 집단(전체)`을 총인구로 나눈 비율입니다(성·5세 연령별).")
r.p()
r.p("> **연령끼리 비교하지 않습니다.** '(전체)' 집단은 고립 확률 모형의 값을 **서울 연령대별로 상위 2%** 를 잘라 센 값입니다(매뉴얼 22쪽, [external/README.md](../../external/README.md)). "
    "그래서 연령대마다 상위 2%가 정해져 있고, 연령대 사이의 수준 차이(예: 20대가 70대보다 높다)는 정의상 의미가 없습니다. 이 표로는 **같은 연령 칸 안의 남녀 비교**와 '서울 같은 연령대 대비 강남의 상대 위치'만 말할 수 있습니다.")
r.p()
c = sl.groupby(["sex", "age"])[["iso_all", "pop"]].sum()
c["비율"] = c["iso_all"] / c["pop"]
cell = c["비율"].unstack("sex")
cell.index = [f"{a}~{a + 4}세" if a < 75 else "75세+" for a in cell.index]
cell.index.name = "연령"
r.table(cell, {"남": "{:.1%}", "여": "{:.1%}"})
y20 = sl[sl["age"].between(20, 29)].groupby("sex")[["iso_all", "pop"]].sum()
m20 = y20.loc["남", "iso_all"] / y20.loc["남", "pop"]
f20 = y20.loc["여", "iso_all"] / y20.loc["여", "pop"]
n_cell_m = int((cell["남"] > cell["여"]).sum())
r.p(f"- 같은 연령(20~29세) 안에서 남 {m20:.1%}, 여 {f20:.1%}로 남성이 여성보다 높습니다(남/여 {m20 / f20:.2f}배).")
r.p(f"- 5세 칸 {len(cell)}개 중 {n_cell_m}개에서 남성이 여성보다 높습니다. 남녀 차이는 20대에서 크고 70대 이상에서는 거의 없습니다(같은 칸 안 비교).")
r.p("- 값은 SKT 모형이 추정한 비율이고 모형이 성별을 입력으로 쓸 수 있어, 남녀 차이도 모형 특성일 수 있습니다.")
ok2 = m20 / f20 > 1.2
status.append(("I-7 청년 남성 외출·소통 결핍 (같은 연령 안 남녀 비교)", "✅ 재현" if ok2 else "⚠️ 부분 재현",
               f"같은 연령(20~29세) 안에서 남 {m20:.1%} vs 여 {f20:.1%} (남/여 {m20 / f20:.2f}배). '(전체)' 집단은 서울 연령대별 상위 2%라 연령 간 비교는 하지 않음 (n=22, 이 지표는 품질 의심 없음)", "계산 재현"))
r.p()
r.p(f"**재현 결과: {status[-1][1]}** ({status[-1][2]})")

# ============================================================ 3. 연휴 공백
r.h("3. 연휴 공백: 깊이·준비·회복 (카드 데이터2, 추석)")
r.p("카드 연령 집단은 **가맹점 도시 × 고객 거주 시도** 기준입니다. 예: '춘천 80대+' = 춘천 가맹점의 강원 거주 80대+ 고객, '강남 80대+' = 강남 가맹점의 서울 거주 80대+ 고객. '춘천 거주'라고 말할 수 없습니다.")
r.p("추석 연휴(10/3~10/9)를 같은 요일 평소 값과 비교합니다. 평소 값은 **9/1~9/28과 10/20~11/9의 공휴일 아닌 날**로 만들었습니다. "
    "이전 판은 10/13~11/9를 썼는데, 회복 구간(10/10~10/19)과 7일이 겹쳐 회복이 늦을수록 평소 값도 같이 낮아지는 문제가 있어 회복 구간을 평소 값에서 뺐습니다(아래 '평소 값 구간 수정' 참고).")
r.p("- **깊이**: 연휴 7일 동안 병원·의원 결제가 평소의 몇 %였나")
r.p("- **준비**: 연휴 직전 며칠 동안 병원·의원·약국 결제가 평소보다 얼마나 늘었나. '평소 하루치의 몇 일분'으로 셉니다(+1.0 = 하루치만큼 더 씀)")
r.p("- **회복**: 연휴 뒤 10일(10/10~10/19) 생필품 결제가 평소의 몇 %였나")
c2 = load_card2()
clinic = ["일반병원", "치과병원", "한의원", "기타의료"]
c2["grp"] = np.select([c2["MCT_RY_CD"].isin(clinic), c2["MCT_RY_CD"] == "약국", c2["cat"] == "생필품"], ["병원·의원", "약국", "생필품"], "기타")
dd = daily(c2[c2["grp"] != "기타"], ["city", "age_g", "grp"])
dd["dow"] = dd["date"].dt.dayofweek


def base_window(frame, late_start):
    return (((frame["date"] >= "2025-09-01") & (frame["date"] <= "2025-09-28")) | ((frame["date"] >= late_start) & (frame["date"] <= "2025-11-09"))) & ~frame["date"].isin(HOL)


# 이전 판 평소 값(10/13~11/9, 회복 구간과 7일 겹침)으로 본 회복: 비교용
old_mask = base_window(dd, "2025-10-13")
old_exp = dd[old_mask].groupby(["city", "age_g", "grp", "dow"])["USE_CNT"].mean().rename("exp_old")
_o = dd.join(old_exp, on=["city", "age_g", "grp", "dow"])
_o = _o[(_o["date"] >= "2025-10-10") & (_o["date"] <= "2025-10-19") & (_o["grp"] == "생필품")].groupby(["city", "age_g"])[["USE_CNT", "exp_old"]].sum()
rec_old = (_o["USE_CNT"] / _o["exp_old"]).unstack("city").reindex(AGE_ORDER)
base_mask = base_window(dd, "2025-10-20")  # 회복 구간(10/10~10/19)을 평소 값에서 뺌
base = dd[base_mask].groupby(["city", "age_g", "grp", "dow"])["USE_CNT"].mean().rename("exp")
dd = dd.join(base, on=["city", "age_g", "grp", "dow"])
daily_exp = dd[base_mask].groupby(["city", "age_g", "grp"])["USE_CNT"].mean()


def window(start, end):
    return dd[(dd["date"] >= start) & (dd["date"] <= end)].groupby(["city", "age_g", "grp"])[["USE_CNT", "exp"]].sum()


def prep_days(start, end, grps=("병원·의원", "약국")):
    w = window(start, end)
    obs = sum(w.xs(k, level="grp")["USE_CNT"] for k in grps)
    exp = sum(w.xs(k, level="grp")["exp"] for k in grps)
    de = sum(daily_exp.xs(k, level="grp") for k in grps)
    return ((obs - exp) / de).unstack("city").reindex(AGE_ORDER)


hol_w = window("2025-10-03", "2025-10-09")
post_w = window("2025-10-10", "2025-10-19")
depth = (hol_w["USE_CNT"] / hol_w["exp"]).xs("병원·의원", level="grp").unstack("city").reindex(AGE_ORDER)
prep3 = prep_days("2025-09-30", "2025-10-02")
prep_g = prep_days("2025-09-30", "2025-10-02", ("생필품",))
rec = (post_w["USE_CNT"] / post_w["exp"]).xs("생필품", level="grp").unstack("city").reindex(AGE_ORDER)
t = pd.concat({"깊이: 병원·의원": depth, "준비(3일): 의료": prep3, "준비(3일): 생필품": prep_g, "회복: 생필품": rec}, axis=1)
t.columns = [f"{a} {b}" for a, b in t.columns]
t.index.name = "연령"
r.table(t, {c_: ("{:.0%}" if ("깊이" in c_ or "회복" in c_) else "{:+.2f}") for c_ in t.columns})

r.h3("평소 값 구간 수정 (회복 구간과 겹침 제거)")
rq = pd.DataFrame({"이전 평소 값(10/13~11/9 포함)": rec_old.stack(), "새 평소 값(10/20~11/9)": rec.stack()})
rq = rq.unstack("city")
rq.columns = [f"{c_} {k}" for k, c_ in rq.columns]
rq = rq[[f"{c_} {k}" for c_ in CITIES for k in ["이전 평소 값(10/13~11/9 포함)", "새 평소 값(10/20~11/9)"]]].reindex(AGE_ORDER)
rq.index.name = "연령"
r.p("연휴 뒤 10일 생필품 회복(평소 대비)을 두 평소 값 구간으로 나란히 적었습니다.")
r.table(rq, {c_: "{:.0%}" for c_ in rq.columns})
r.p(f"- {CARD80['춘천']}의 회복: 이전 {rec_old.loc['80대+', '춘천']:.0%} → 새 기준 {rec.loc['80대+', '춘천']:.0%}. 강남 80대+: 이전 {rec_old.loc['80대+', '강남']:.0%} → {rec.loc['80대+', '강남']:.0%}.")
r.p("- 이 표의 값은 7연령 × 2도시 × 3지표 중 하나의 칸입니다. 가장 극단인 칸을 골라 인용하면 우연으로 생긴 값을 과대평가할 수 있습니다.")

r.h3("기준을 바꿔도 유지되나")
end = pd.Timestamp("2025-10-02")
sens = {}
for n_day in [2, 3, 4, 5]:
    pr = prep_days(end - pd.Timedelta(days=n_day - 1), end)
    for cty in CITIES:
        sens[(f"{n_day}일", cty)] = {"80대+": pr.loc["80대+", cty], "40대": pr.loc["40대", cty]}
sens = pd.DataFrame(sens).T
sens.index.names = ["연휴 전 구간", "도시"]
sens = sens.reset_index().set_index("연휴 전 구간")
r.p("연휴 전 의료 '준비' 구간 길이를 2~5일로 바꿔 본 결과입니다(평소 하루치 기준 일수).")
r.table(sens, {"도시": "{}", "80대+": "{:+.2f}", "40대": "{:+.2f}"})

# 평소 구간의 흔들림
normal_days = dd.loc[base_mask, "date"].drop_duplicates().sort_values()
rec_null, prep_null = {c_: [] for c_ in CITIES}, {c_: [] for c_ in CITIES}
for s0 in normal_days:
    p_end = s0 + pd.Timedelta(days=2)
    r_end = s0 + pd.Timedelta(days=9)
    if not normal_days.isin(pd.date_range(s0, r_end)).sum() == 10:
        continue
    w3 = prep_days(s0, p_end)
    w10 = window(s0, r_end).xs("생필품", level="grp")
    rr = (w10["USE_CNT"] / w10["exp"]).unstack("city").reindex(AGE_ORDER)
    for cty in CITIES:
        prep_null[cty].append(w3.loc["80대+", cty])
        rec_null[cty].append(rr.loc["80대+", cty])
rank = {}
for cty in CITIES:
    rank[cty] = np.mean(np.array(rec_null[cty]) <= rec.loc["80대+", cty])
    sd = np.std(prep_null[cty])
    pos = "평소 어느 10일 구간보다도 낮음" if rank[cty] == 0 else f"평소 10일 구간 중 하위 {rank[cty]:.0%} 위치"
    r.p(f"- {CARD80[cty]}: 연휴 뒤 장보기 {rec.loc['80대+', cty]:.0%}는 {pos}. 평소 3일 구간 의료 '준비' 값의 흔들림(표준편차) {sd:.2f}일")
r.p()
deep = all(depth.loc[["70대", "80대+"], c_].max() < depth.loc[["30대", "40대", "50대"], c_].min() for c_ in CITIES)
slow = {c_: rank[c_] < 0.05 and rec.loc["80대+", c_] < rec.loc["40대", c_] - 0.05 for c_ in CITIES}
slow_cc, slow_gn = slow["춘천"], slow["강남"]
prep_robust = all(sens.loc[sens["도시"] == "춘천", "80대+"] < sens.loc[sens["도시"] == "춘천", "40대"] - 0.3)
r.p(f"- **깊이**: 70대 이상의 병원·의원 이용이 30~50대보다 더 깊이 떨어짐 → 두 도시 모두 {'예' if deep else '아니오'}")
r.p(f"- **회복**(평소 구간의 하위 5%보다 낮고 40대보다 5%p 이상 낮으면 지연): 춘천 {'지연 확인' if slow_cc else '확인 안 됨'}, 강남 {'지연' if slow_gn else '지연 아님'}")
r.p(f"- **준비**: 3일 구간에서는 {CARD80['춘천']}이 진료를 거의 앞당기지 않았지만, 구간을 4~5일로 늘리면 차이가 크게 줄어듭니다 → {'기준을 바꿔도 유지' if prep_robust else '근거 약함(구간 길이에 따라 달라짐)'}. 생필품은 고령층이 연휴 전에 더 많이 사 둡니다.")
r.p("- 주의: 연휴 효과는 **추석 한 번**만 관측했습니다. 가족과 함께 있어서 결제가 줄었는지, 정말 공백인지는 구분할 수 없습니다.")
fig, axes = plt.subplots(1, 3, figsize=(15, 4))
for ax, (col, title) in zip(axes, [(depth, "연휴 중 병원·의원 (평소 대비)"), (prep3, "연휴 전 3일 의료 앞당김 (평소 하루치 기준 일수)"), (rec, "연휴 후 10일 생필품 (평소 대비)")]):
    for cty in CITIES:
        ax.plot(AGE_ORDER, col[cty], marker="o", label=cty, color=CITY_COLOR[cty])
    ax.set_title(title)
axes[0].legend()
r.fig(fig, "f02_holiday_gap.png", "연휴 공백")
res4 = "✅ 재현" if (deep and slow_cc and slow_gn and prep_robust) else ("⚠️ 부분 재현" if deep else "❌ 재현 안 됨")
status.append(("I-4 연휴: 고령층 진료 단절 깊이·준비 부족·회복 지연", res4,
               f"깊이 {'두 도시 확인' if deep else '미확인'}, 회복 지연 {'춘천 가맹점의 강원 거주 80대+ 고객만' if slow_cc and not slow_gn else ('두 도시' if slow_cc and slow_gn else '미확인')}, 준비 부족 {'유지' if prep_robust else '근거 약함'} (추석 1회)", "계산 재현"))
r.p(f"**재현 결과: {status[-1][1]}** ({status[-1][2]})")

# ============================================================ 4. 겨울 감소
r.h("4. 춘천 읍·면 주민 거주지의 겨울 감소 (통신)")
r.p("읍·면 감소에는 여름 방문객·농사철 활동이 빠진 효과가 섞여 있습니다. 그래서 250m 격자 중 **주민이 사는 곳으로 보이는 격자**(밤에도 사람이 있고, 주말과 평일 차이가 작은 곳)만 따로 봅니다.")
r.p("- 여름 = 7·9월 평균(8월은 휴가철 방문객이 많아 뺌), 겨울 = 11·12월 평균")
age = pd.read_parquet(D / "tel_age.parquet")
tim = pd.read_parquet(D / "tel_time.parquet")
wk = pd.read_parquet(D / "tel_wkdy.parquet")
for d_ in (age, tim, wk):
    d_["g"] = grid_id(d_["X_COORD"], d_["Y_COORD"])
cc = age[age["city"] == "춘천"].copy()
cc["s60"] = cc[age_cols("60GU")].sum(axis=1)
tt = tim[tim["city"] == "춘천"].copy()
tt["night"] = tt[[f"TMST_{h:02d}" for h in range(0, 6)]].sum(axis=1)
tt["day"] = tt[[f"TMST_{h:02d}" for h in range(11, 16)]].sum(axis=1)
ww = wk[wk["city"] == "춘천"].copy()
ww["wd"] = ww[WKDY_COLS[:5]].mean(axis=1)
ww["we"] = ww[WKDY_COLS[5:]].mean(axis=1)
gt = tt.groupby("g")[["night", "day"]].sum()
gw = ww.groupby("g")[["wd", "we"]].sum()
gm_all = cc.groupby(["g", "STD_YM"])["s60"].sum().unstack().fillna(0)
gtype = cc.groupby("g")["adm_type"].agg(lambda s: s.mode().iloc[0])
info_all = pd.DataFrame({"nd": gt["night"] / gt["day"], "ww": gw["we"] / gw["wd"], "type": gtype, "s60": gm_all.mean(axis=1)}).dropna()


def core_change(min_s60=2, ww_lo=0.8, ww_hi=1.25, q=0.5, summer=("202507", "202509")):
    info = info_all[info_all["s60"] >= min_s60].copy()
    cut = info.loc[info["type"] != "동", "nd"].quantile(q)
    info["core"] = (info["nd"] >= cut) & info["ww"].between(ww_lo, ww_hi)
    gm = gm_all.loc[info.index]
    sm = gm[list(summer)].mean(axis=1)
    wt = gm[["202511", "202512"]].mean(axis=1)
    out = {}
    for typ, sel in [("읍·면", info["type"].isin(["읍", "면"])), ("동지역", info["type"].eq("동"))]:
        for core, cl in [(True, "주민형 격자"), (False, "그 밖")]:
            m = sel & info["core"].eq(core)
            out[(typ, cl)] = (int(m.sum()), wt[m].sum() / sm[m].sum() - 1)
    return out, info, gm


main, info, gm = core_change()
t = pd.DataFrame([(a, b, n, v) for (a, b), (n, v) in main.items()], columns=["지역", "격자", "격자 수", "60대+ 체류 변화"]).set_index("지역")
r.table(t, {"격자 수": "{:,}", "60대+ 체류 변화": "{:+.1%}"})
rc_core = main[("읍·면", "주민형 격자")][1]
rc_dong = main[("동지역", "주민형 격자")][1]

r.h3("기준을 바꿔도 유지되나")
variants = [
    ("기본", {}),
    ("60대+ 하루 1 이상", {"min_s60": 1}),
    ("60대+ 하루 5 이상", {"min_s60": 5}),
    ("주말/평일 0.9~1.1", {"ww_lo": 0.9, "ww_hi": 1.1}),
    ("주말/평일 0.7~1.3", {"ww_lo": 0.7, "ww_hi": 1.3}),
    ("심야/낮 상위 40%", {"q": 0.6}),
    ("여름 = 7월만", {"summer": ("202507",)}),
    ("여름 = 7~9월", {"summer": ("202507", "202508", "202509")}),
]
vrows = []
for name, kw in variants:
    o, _, _ = core_change(**kw)
    vrows.append((name, o[("읍·면", "주민형 격자")][1], o[("동지역", "주민형 격자")][1]))
vt = pd.DataFrame(vrows, columns=["기준", "읍·면 주민형", "동지역 주민형"]).set_index("기준")
r.table(vt, {"읍·면 주민형": "{:+.1%}", "동지역 주민형": "{:+.1%}"})
gap_min = (vt["동지역 주민형"] - vt["읍·면 주민형"]).min()
fig, ax = plt.subplots(figsize=(8, 4))
yy = np.arange(len(vt))
ax.barh(yy - 0.2, vt["읍·면 주민형"] * 100, 0.4, label="읍·면 주민형 격자", color="#805ad5")
ax.barh(yy + 0.2, vt["동지역 주민형"] * 100, 0.4, label="동지역 주민형 격자", color="#dd6b20")
ax.set_yticks(yy, vt.index)
ax.axvline(0, color="#4a5568", lw=0.8)
ax.invert_yaxis()
ax.set_xlabel("여름 → 겨울 60대+ 체류 변화 (%)")
ax.set_title("기준을 바꿔도 읍·면 주민 거주지만 겨울에 줄어든다")
ax.legend()
r.fig(fig, "f03_winter_sensitivity.png", "겨울 감소 민감도")

core_r = info["core"] & info["type"].isin(["읍", "면"])
c1 = gm.loc[core_r, "202511"] / gm.loc[core_r, "202507"] - 1
c2_ = gm.loc[core_r, "202512"] / gm.loc[core_r, "202509"] - 1
r.p(f"- 읍·면 주민형 격자의 60대+ 체류 변화는 기준을 바꿔도 {vt['읍·면 주민형'].min():+.1%}~{vt['읍·면 주민형'].max():+.1%}이고, 동지역 주민형은 {vt['동지역 주민형'].min():+.1%}~{vt['동지역 주민형'].max():+.1%}입니다.")


def persist(c1_, c2_):
    both = ((c1_ <= -0.15) & (c2_ <= -0.15)).mean()
    indep = (c1_ <= -0.15).mean() * (c2_ <= -0.15).mean()
    return c1_.corr(c2_, method="spearman"), both, indep


corr2, both_dn, exp_dn = persist(c1, c2_)
both_up, exp_up = ((c1 >= 0.15) & (c2_ >= 0.15)).mean(), (c1 >= 0.15).mean() * (c2_ >= 0.15).mean()
r.p(f"- 격자별로 두 기간(7→11월, 9→12월) 변화의 순위상관은 {corr2:.2f}입니다. 두 기간 모두 15% 이상 줄어든 격자는 {both_dn:.0%}, 모두 15% 이상 늘어난 격자는 {both_up:.0%}입니다.")
r.p(f"  - 비교값 {exp_dn:.0%}·{exp_up:.0%}는 **두 기간의 변화가 서로 독립이라고 가정했을 때의 값**입니다(두 비율을 곱한 값). 이웃 격자끼리 함께 움직이면 이 가정이 맞지 않습니다.")
gdong = cc.groupby("g")["adm_cd"].agg(lambda s: s.mode().iloc[0])
gxy = cc.groupby("g")[["X_COORD", "Y_COORD"]].mean()
blk = (gxy["X_COORD"] // 2000).astype(int).astype(str) + "_" + (gxy["Y_COORD"] // 2000).astype(int).astype(str)
cl = pd.DataFrame({"c1": c1, "c2": c2_, "dong": gdong.reindex(c1.index), "blk": blk.reindex(c1.index)})
rs_rows = []
for unit, label in [("dong", "행정동"), ("blk", "2km 공간 블록")]:
    groups = {k: v for k, v in cl.groupby(unit)}
    keys_ = list(groups)
    vals = []
    for _ in range(1000):
        pick = pd.concat([groups[k] for k in rng_new.choice(keys_, len(keys_), replace=True)])
        vals.append(persist(pick["c1"], pick["c2"]))
    vals = np.array(vals)
    lo_, hi_ = np.percentile(vals, [2.5, 97.5], axis=0)
    diff_ = vals[:, 1] - vals[:, 2]
    rs_rows.append((f"{label} 재표집 ({len(keys_)}개)", f"{lo_[0]:.2f}~{hi_[0]:.2f}", f"{lo_[1]:.0%}~{hi_[1]:.0%}", f"{np.percentile(diff_, 2.5):+.0%}~{np.percentile(diff_, 97.5):+.0%}"))
rs = pd.DataFrame(rs_rows, columns=["재표집 단위", "순위상관 95% 범위", "두 기간 모두 감소 95% 범위", "모두 감소 − 독립 가정 값"]).set_index("재표집 단위")
r.p("격자를 하나씩 독립으로 보지 않고 **행정동 또는 2km 공간 블록 단위로 통째로** 다시 뽑아 범위를 봤습니다(1,000번).")
r.table(rs, {c_: "{}" for c_ in rs.columns})
r.p("- 묶음 단위로 뽑아도 순위상관이 0보다 크면, 격자별 변화가 이어진다는 해석은 공간 의존을 고려해도 유지됩니다. 범위가 0을 걸치면 그 해석은 약합니다.")
r.p("- 6개월 자료라 매년 반복되는 계절 변화인지는 알 수 없습니다. 그래서 '이상 탐지'가 아니라 **겨울철에 미리 배치하는 점검**으로 씁니다.")

r.h3("고령만 더 줄었나: 같은 격자 안에서 고령 변화 − 전 연령 변화")
r.p("같은 성·연령 표(통신 성연령 표) 안에서 60대+ 변화와 전 연령 합 변화를 비교합니다. 시간대 표와 곱하지 않습니다(두 표는 서로 결합할 수 없음).")
cc["all"] = cc[AGE_COLS].sum(axis=1)
gm_tot = cc.groupby(["g", "STD_YM"])["all"].sum().unstack().fillna(0)


def rel_change(mask, summer=("202507", "202509")):
    sel = info.index[mask]
    o = gm.loc[sel]
    a_ = gm_tot.loc[sel]
    ch60 = o[["202511", "202512"]].mean(axis=1).sum() / o[list(summer)].mean(axis=1).sum() - 1
    chall = a_[["202511", "202512"]].mean(axis=1).sum() / a_[list(summer)].mean(axis=1).sum() - 1
    return int(mask.sum()), ch60, chall, ch60 - chall


# 관광형 동: 주민 수 대비 여름(8월) 체류가 큰 읍·면

res_pop = resident_population()
res8 = res_pop[res_pop["month"] == 8].groupby("adm_cd")["pop"].sum()
flow8 = cc[cc["STD_YM"] == "202508"].groupby("adm_cd")["all"].sum()
tour = pd.DataFrame({"8월 전 연령 체류": flow8, "주민등록 인구(8월)": res8}).dropna()
tour["체류/주민"] = tour["8월 전 연령 체류"] / tour["주민등록 인구(8월)"]
tour["유형"] = names["adm_type"].reindex(tour.index)
tour["동"] = names["adm_nm"].reindex(tour.index)
em = tour["유형"].isin(["읍", "면"])
cut_tour = tour.loc[em, "체류/주민"].median() * 1.5
tour["관광형"] = em & (tour["체류/주민"] >= cut_tour)
tour_dongs = set(tour.index[tour["관광형"]])
info["dong"] = gdong.reindex(info.index)
emr = info["core"] & info["type"].isin(["읍", "면"])
rows5 = []
for label, m in [("읍·면 주민형 격자", emr), ("읍·면 주민형 (관광형 동 제외)", emr & ~info["dong"].isin(tour_dongs)),
                 ("읍·면 주민형 (관광형 동만)", emr & info["dong"].isin(tour_dongs)), ("동지역 주민형 격자", info["core"] & info["type"].eq("동"))]:
    rows5.append((label, *rel_change(m)))
t5 = pd.DataFrame(rows5, columns=["격자", "격자 수", "60대+ 변화", "전 연령 변화", "차이(60대+ − 전 연령)"]).set_index("격자")
r.table(t5, {"격자 수": "{:,.0f}", "60대+ 변화": "{:+.1%}", "전 연령 변화": "{:+.1%}", "차이(60대+ − 전 연령)": "{:+.1%}p"})
r.p(f"- 관광형 동 = 춘천 읍·면 중 '8월 전 연령 체류 ÷ 주민등록 인구'가 읍·면 중앙값의 1.5배 이상인 곳(운영상 규칙): {', '.join(tour.loc[tour['관광형'], '동']) or '없음'}")
d_rel = t5.loc["읍·면 주민형 격자", "차이(60대+ − 전 연령)"]
d_rel_nt = t5.loc["읍·면 주민형 (관광형 동 제외)", "차이(60대+ − 전 연령)"]
older_more = d_rel < -0.02 and d_rel_nt < -0.02
r.p(f"- 읍·면 주민형 격자에서 60대+는 {t5.loc['읍·면 주민형 격자', '60대+ 변화']:+.1%}, 전 연령은 {t5.loc['읍·면 주민형 격자', '전 연령 변화']:+.1%}로 차이는 {d_rel * 100:+.1f}%p입니다.")
if older_more:
    r.p("  - 점추정으로는 같은 격자 안에서도 고령이 전 연령보다 더 줄었습니다. 이 차이가 읍·면을 바꿔 뽑아도 남는지는 아래 '읍·면 단위 불확실성'에서 봅니다.")
else:
    r.p("  - 같은 격자 안에서 고령과 전 연령의 변화 차이가 작습니다(2%p 미만이거나 관광형 동을 빼면 사라짐). 겨울 감소는 **고령만의 변화라기보다 전 연령이 함께 줄어든 것**에 가깝습니다.")
r.p("- 여름 방문객이 빠진 효과(방문객 이탈)는 **가능한 설명**입니다. 통신 자료로는 방문객과 주민을 구분할 수 없어 확인하지 못했습니다.")

# 동(읍·면) 단위 재표집: 격자는 동 안에서 서로 닮았으므로 읍·면을 통째로 다시 뽑는다
r.h3("읍·면 단위 불확실성")
emr_idx = info.index[emr]
by_dong = {k_: v.index for k_, v in info.loc[emr_idx].groupby("dong")}
dk = list(by_dong)


def ch_sel(sel):
    o = gm.loc[sel]
    a_ = gm_tot.loc[sel]
    c60 = o[["202511", "202512"]].mean(axis=1).sum() / o[["202507", "202509"]].mean(axis=1).sum() - 1
    call = a_[["202511", "202512"]].mean(axis=1).sum() / a_[["202507", "202509"]].mean(axis=1).sum() - 1
    return c60, call


bs60, bsd = [], []
for _ in range(4000):
    sel = np.concatenate([by_dong[k_] for k_ in rng_rev.choice(dk, len(dk), replace=True)])
    a60, aall = ch_sel(sel)
    bs60.append(a60)
    bsd.append(a60 - aall)
ci60 = np.percentile(bs60, [2.5, 97.5])
cid = np.percentile(bsd, [2.5, 97.5])
per_dong = pd.DataFrame({k_: ch_sel(v) for k_, v in by_dong.items()}, index=["60대+", "전 연령"]).T
per_dong["차이"] = per_dong["60대+"] - per_dong["전 연령"]
per_dong["격자 수"] = pd.Series({k_: len(v) for k_, v in by_dong.items()})
per_dong.index = [names["adm_nm"].get(k_, k_) for k_ in per_dong.index]
per_dong.index.name = "읍·면"
n_more = int((per_dong["차이"] < 0).sum())
r.p(f"읍·면 주민형 격자가 있는 읍·면 {len(dk)}곳을 단위로 통째로 다시 뽑았습니다(4,000번). 같은 읍·면 안 격자끼리 닮아서, 격자를 하나씩 독립으로 보면 범위가 너무 좁아지기 때문입니다.")
r.p()
r.p("| 값 | 점추정 | 읍·면 단위 95% CI |")
r.p("|---|---:|---|")
r.p(f"| 60대+ 체류 변화 | {t5.loc['읍·면 주민형 격자', '60대+ 변화']:+.1%} | {ci60[0]:+.1%}~{ci60[1]:+.1%} |")
r.p(f"| 차이(60대+ − 전 연령) | {d_rel * 100:+.1f}%p | {cid[0] * 100:+.1f}~{cid[1] * 100:+.1f}%p |")
r.p()
r.table(per_dong.sort_values("차이"), {"60대+": "{:+.1%}", "전 연령": "{:+.1%}", "차이": "{:+.1%}", "격자 수": "{:,.0f}"})
gap_unsure = cid[0] < 0 < cid[1]
r.p(f"- 고령이 전 연령보다 더 줄어든 읍·면: **{n_more}/{len(dk)}곳**.")
if gap_unsure:
    r.p(f"- 60대+ 감소({t5.loc['읍·면 주민형 격자', '60대+ 변화']:+.1%})는 읍·면 단위 CI에서도 0보다 낮아 **유지**됩니다. 그러나 '고령이 전 연령보다 더 줄었다'({d_rel * 100:+.1f}%p)는 CI가 0을 포함해 **고령만 더 줄었는지 불확실**합니다.")
else:
    r.p(f"- 60대+ 감소와 '고령이 전 연령보다 더 줄었다'({d_rel * 100:+.1f}%p) 모두 읍·면 단위 CI가 0을 넘지 않습니다.")
older_more = older_more and not gap_unsure
ok4 = gap_min > 0.05
res5 = ("✅ 재현" if (ok4 and older_more) else "⚠️ 부분 재현")
status.append(("I-5 춘천 읍·면 주민 거주지 겨울 감소", res5,
               f"주민형 격자 {rc_core:+.1%} vs 동지역 {rc_dong:+.1%}, 기준 8가지에서 {vt['읍·면 주민형'].min():+.1%}~{vt['읍·면 주민형'].max():+.1%}. "
               f"읍·면 단위 CI {ci60[0]:+.1%}~{ci60[1]:+.1%}. 같은 격자 전 연령 대비 {d_rel * 100:+.1f}%p (읍·면 단위 CI {cid[0] * 100:+.1f}~{cid[1] * 100:+.1f}%p, 고령이 더 줄어든 읍·면 {n_more}/{len(dk)}곳"
               + (" → 고령만 더 줄었는지 불확실" if gap_unsure else "") + f", 관광형 동 제외 {d_rel_nt * 100:+.1f}%p)",
               "계산 재현 (원인은 가설)"))
r.p()
r.p(f"**재현 결과: {status[-1][1]}** ({status[-1][2]})")

# ============================================================ 5. 폭염
r.h("5. 폭염과 고령층 생필품 (기상청 ASOS)")
r.p("일 최고기온 33℃ 이상인 날(7~8월)에 생필품 결제가 같은 요일 평소(7~8월, 33℃ 미만, 공휴일 제외)보다 얼마나 달랐는지 봅니다. 날씨는 **기상청 ASOS 관측값**(서울 108 = 강남 대표, 춘천 101)입니다. 관측소 한 곳의 값이라 동별 날씨는 아닙니다.")
wx = load_weather()
temp = {c_: wx[wx["city"] == c_].set_index("date")["tmax"] for c_ in CITIES}
rainmm = {c_: wx[wx["city"] == c_].set_index("date")["rain"] for c_ in CITIES}
gd = dd[(dd["grp"] == "생필품") & (dd["date"] < "2025-09-01") & ~dd["date"].isin(HOL)].copy()
gd["tmax"] = [temp[c_].get(d_, np.nan) for c_, d_ in zip(gd["city"], gd["date"])]
n_tmax_na = gd.loc[gd["tmax"].isna(), ["city", "date"]].drop_duplicates().groupby("city").size()
gd = gd[gd["tmax"].notna()].copy()  # 최고기온 결측일은 폭염/평소 어느 쪽에도 넣지 않음
gd["hot"] = gd["tmax"] >= 33
gd["rainy"] = [rainmm[c_].get(d_, 0) >= 1 for c_, d_ in zip(gd["city"], gd["date"])]
gd["month"] = gd["date"].dt.month


def heat_gap(frame, hot_col="hot", by=("dow",)):
    keys_ = ["city", "age_g", *by]
    b = frame[~frame[hot_col]].groupby(keys_)["USE_CNT"].mean().rename("b")
    f = frame.join(b, on=keys_)
    f["lr"] = np.log(f["USE_CNT"] / f["b"])
    h_ = f[f[hot_col]].groupby(["city", "age_g"])["lr"].mean().unstack("city")
    return h_, {c_: np.exp(h_.loc["80대+", c_]) / np.exp(h_.loc[["40대", "50대"], c_].mean()) - 1 for c_ in h_.columns}


hot, diff = heat_gap(gd)
t = (np.exp(hot.reindex(AGE_ORDER)) - 1)
t.index.name = "연령"
r.table(t, {c_: "{:+.1%}" for c_ in t.columns})
n_hot = gd[gd["hot"]].groupby("city")["date"].nunique()
r.p(f"- 폭염일 수(7~8월, 공휴일 제외): 강남 {n_hot.get('강남', 0)}일, 춘천 {n_hot.get('춘천', 0)}일. 최고기온 결측일은 비교에서 뺐습니다(강남 {n_tmax_na.get('강남', 0)}일, 춘천 {n_tmax_na.get('춘천', 0)}일).")
r.p(f"- 80대+가 40·50대보다 상대적으로 얼마나 더 줄었나(폭염일/평소 비율의 상대 차이): 강남 {diff['강남']:+.1%}, 춘천 {diff['춘천']:+.1%}")
om = {}
for cty, f in [("강남", "seoul"), ("춘천", "chuncheon")]:
    w = json.load(open(ROOT / "external" / "weather" / f"openmeteo_{f}_2025-07_12.json", encoding="utf-8"))["daily"]
    om[cty] = pd.Series(w["temperature_2m_max"], index=pd.to_datetime(w["time"]))
gd_om = gd.copy()
gd_om["hot"] = [om[c_].get(d_, np.nan) >= 33 for c_, d_ in zip(gd_om["city"], gd_om["date"])]
_, diff_om = heat_gap(gd_om)
r.p(f"- 참고: 예전 탐색에 쓴 Open-Meteo 재분석 기준이면 강남 {diff_om['강남']:+.1%}, 춘천 {diff_om['춘천']:+.1%} (폭염일 정의가 달라 값이 다름)")
r.p()

# 날짜 블록 부트스트랩: 이웃한 날은 서로 닮았으므로 7일 묶음 단위로 다시 뽑는다
r.h3("신뢰구간과 민감도")
BLOCK = 7
heat_days = np.array(sorted(gd["date"].unique()))
boot = {c_: [] for c_ in CITIES}
for _ in range(1000):
    starts = rng_new.integers(0, len(heat_days) - BLOCK + 1, int(np.ceil(len(heat_days) / BLOCK)))
    pick = np.concatenate([heat_days[s:s + BLOCK] for s in starts])[: len(heat_days)]
    fr = pd.concat([gd[gd["date"] == d_] for d_ in pick])
    try:
        _, dg = heat_gap(fr)
    except KeyError:
        continue
    for c_ in CITIES:
        if c_ in dg:
            boot[c_].append(dg[c_])
ci = {c_: np.nanpercentile(boot[c_], [2.5, 97.5]) for c_ in CITIES}
_, diff_dry = heat_gap(gd[~gd["rainy"]])
_, diff_mon = heat_gap(gd, by=("dow", "month"))
n_dry = gd[gd["hot"] & ~gd["rainy"]].groupby("city")["date"].nunique()
hs = pd.DataFrame({
    "기본": diff,
    "날짜 7일 블록 부트스트랩 95% CI": {c_: f"{ci[c_][0]:+.1%}~{ci[c_][1]:+.1%}" for c_ in CITIES},
    "비 온 날(1mm 이상) 제외": diff_dry,
    "같은 달 평소와 비교(월내 추세 보정)": diff_mon,
}).reindex(CITIES)
hs.index.name = "도시"
r.table(hs, {"기본": "{:+.1%}", "날짜 7일 블록 부트스트랩 95% CI": "{}", "비 온 날(1mm 이상) 제외": "{:+.1%}", "같은 달 평소와 비교(월내 추세 보정)": "{:+.1%}"})
r.p(f"- 값은 80대+의 '폭염일/평소' 비율을 40·50대의 같은 비율로 나눈 상대 차이(%)입니다. %p(퍼센트포인트) 차이가 아닙니다. 비 온 날을 빼면 폭염일이 강남 {n_dry.get('강남', 0)}일, 춘천 {n_dry.get('춘천', 0)}일로 줄어듭니다.")
r.p("- 날짜 블록 부트스트랩은 7일 연속 구간을 통째로 다시 뽑습니다. 이어진 날들이 서로 닮아서 날짜를 하나씩 독립으로 뽑으면 범위가 너무 좁아지기 때문입니다.")

# 우연인지 확인: 폭염일을 무작위 날짜로 바꾸기, 폭염일 묶음을 통째로 옮기기
days_all = np.array(sorted(gd["date"].unique()))
pvals, nulls, pcount = {}, {}, {}
for cty in CITIES:
    sub = gd[gd["city"] == cty].copy()
    hot_days = set(sub.loc[sub["hot"], "date"])
    k = len(hot_days)
    null_rand = []
    for _ in range(1000):
        pick = set(rng.choice(days_all, k, replace=False))
        sub["fake"] = sub["date"].isin(pick)
        null_rand.append(heat_gap(sub, "fake")[1][cty])
    null_shift = []
    for sh in range(1, len(days_all)):
        moved = {days_all[(np.searchsorted(days_all, d_) + sh) % len(days_all)] for d_ in hot_days}
        sub["fake"] = sub["date"].isin(moved)
        null_shift.append(heat_gap(sub, "fake")[1][cty])
    k_r = int(np.sum(np.array(null_rand) <= diff[cty]))
    k_s = int(np.sum(np.array(null_shift) <= diff[cty]))
    pvals[cty] = (perm_p(k_r, len(null_rand)), perm_p(k_s, len(null_shift)))
    pcount[cty] = ((k_r, len(null_rand)), (k_s, len(null_shift)))
    nulls[cty] = null_rand
r.p("우연인지 확인했습니다. 폭염일 대신 같은 수의 날짜를 무작위로 뽑거나(1,000번), 폭염일 묶음을 날짜만 통째로 옮겼을 때(휴가철 같은 시기 효과 확인) 지금만큼 큰 차이가 나올 확률입니다.")
r.p("p는 (귀무에서 실제만큼 극단인 횟수 + 1) / (시도 수 + 1)로 적습니다. 실제 배치도 귀무의 한 경우이므로 p는 0이 될 수 없습니다.")
r.p()
r.p("| 도시 | 무작위 날짜 p | 묶음 옮기기 p |")
r.p("|---|---:|---:|")
for cty in CITIES:
    r.p(f"| {cty} | {fmt_perm_p(*pcount[cty][0], '무작위')} | {fmt_perm_p(*pcount[cty][1], '이동')} |")
r.p()
r.p(f"- **묶음 옮기기는 가능한 이동이 {len(days_all) - 1}회뿐**이라 가장 작은 p가 1/{len(days_all)} ≈ {1 / len(days_all):.3f}입니다. 이전 판의 'p<0.001'은 이 검정으로 나올 수 없는 값이었습니다.")
r.p("- **무작위 날짜 검정은 날짜 묶음 구조를 무시합니다.** 실제 폭염일은 며칠씩 이어지는데 무작위 날짜는 흩어져 뽑히므로, 이 p는 낙관적(작게 나옴)입니다. 이어진 날의 구조를 지키는 '묶음 옮기기'와 함께 읽습니다.")
fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=True)
for ax, cty in zip(axes, CITIES):
    ax.hist(np.array(nulls[cty]) * 100, bins=40, color="#cbd5e0")
    ax.axvline(diff[cty] * 100, color=CITY_COLOR[cty], lw=2, label="실제 폭염일")
    ax.set_title(f"{cty}: 무작위 날짜 1,000번 vs 실제 폭염일")
    ax.set_xlabel("80대+와 40·50대의 장보기 차이 (%)")
    ax.legend()
axes[0].set_ylabel("횟수")
r.fig(fig, "f04_heat_null.png", "폭염 우연 검정")
sig = {c_: diff[c_] < 0 and ci[c_][1] < 0 and pvals[c_][0] < 0.05 for c_ in CITIES}
robust = {c_: sig[c_] and diff_dry[c_] < 0 and diff_mon[c_] < 0 for c_ in CITIES}
for c_ in CITIES:
    if robust[c_]:
        msg = "CI가 0보다 낮고 무작위 날짜와도 구분되며, 비 온 날 제외·월내 추세 보정에서도 방향이 같습니다"
    elif sig[c_]:
        msg = "CI와 무작위 날짜 검정으로는 우연과 구분되지만, 민감도 분석 중 하나에서 방향이 바뀝니다"
    else:
        msg = "CI가 0을 걸치거나 무작위 날짜와 구분되지 않습니다"
    r.p(f"- {c_}: {msg}. 묶음 옮기기 {fmt_perm_p(*pcount[c_][1], '이동')}")
r.p("- 폭염일이 7월 초·7월 말~8월 초에 몰려 휴가철과 겹칩니다. 휴가철 효과는 '묶음 옮기기'와 '같은 달 평소와 비교'로 일부만 확인할 수 있습니다.")
r.p()

# ---- 분석 선택 민감도
r.h3("비교 기준 연령·업종 위약·폭염 묶음 수")
REFS = [("20·30대", ["20대", "30대"]), ("40·50대 (기본)", ["40대", "50대"]), ("60대", ["60대"]), ("70대", ["70대"])]


def rel80(h_, ref):
    return {c_: np.exp(h_.loc["80대+", c_]) / np.exp(h_.loc[ref, c_].mean()) - 1 for c_ in h_.columns}


sens_ref = pd.DataFrame({lab: rel80(hot, ref) for lab, ref in REFS}).T.reindex(columns=CITIES)
sens_ref.index.name = "80대+와 비교한 연령"
sens_rng = {c_: (sens_ref[c_].min(), sens_ref[c_].max()) for c_ in CITIES}
r.p("80대+의 '폭염일/평소' 비율을 어느 연령과 비교하느냐에 따라 값이 달라집니다(생필품, ASOS).")
r.table(sens_ref, {c_: "{:+.1%}" for c_ in CITIES})
r.p()
r.p(f"- 비교 연령에 따라 강남 {sens_rng['강남'][0]:+.1%}~{sens_rng['강남'][1]:+.1%}, 춘천 {sens_rng['춘천'][0]:+.1%}~{sens_rng['춘천'][1]:+.1%}입니다. 방학·휴가로 40·50대 장보기가 늘어나는 효과도 섞일 수 있습니다.")

# 업종 위약: 생필품이 아닌 업종에서도 80대+가 덜 썼다면 '장보기'만의 효과가 아님
c2_plc = c2[c2["grp"] == "기타"]
dd_plc = daily(c2_plc, ["city", "age_g"])
dd_plc["grp"] = "기타"


def heat_frame(frame):
    f = frame[(frame["date"] < "2025-09-01") & ~frame["date"].isin(HOL)].copy()
    f["dow"] = f["date"].dt.dayofweek
    f["tmax"] = [temp[c_].get(d_, np.nan) for c_, d_ in zip(f["city"], f["date"])]
    f = f[f["tmax"].notna()].copy()
    f["hot"] = f["tmax"] >= 33
    return f


plc = {"생필품 (기본)": diff}
for lab, frame in [("기타 업종 (위약)", dd_plc)]:
    plc[lab] = heat_gap(heat_frame(frame))[1]
plc_t = pd.DataFrame(plc).T.reindex(columns=CITIES)
plc_t.index.name = "업종 (80대+ vs 40·50대)"
r.p()
r.p("**업종 위약(placebo)**: 생필품·의료가 아닌 업종(외식·여가·생활서비스 등)에서도 80대+가 폭염일에 상대적으로 덜 썼다면, 이 차이는 '장보기'만의 효과가 아니라 고령층의 전반적인 외출 감소일 수 있습니다.")
r.table(plc_t, {c_: "{:+.1%}" for c_ in CITIES})

# 독립 폭염 묶음 수: 이어진 폭염일은 한 사건
n_spell = {}
for c_ in CITIES:
    hd = pd.to_datetime(sorted(gd.loc[(gd["city"] == c_) & gd["hot"], "date"].unique()))
    n_spell[c_] = int(1 + (np.diff(hd.values).astype("timedelta64[D]").astype(int) > 1).sum()) if len(hd) else 0
r.p(f"- **독립 폭염 묶음**(하루 이상 끊기지 않고 이어진 폭염일을 한 사건으로 셈, 공휴일 제외 자료): 강남 {n_spell['강남']}개({n_hot.get('강남', 0)}일), 춘천 {n_spell['춘천']}개({n_hot.get('춘천', 0)}일). "
    f"폭염일 수보다 이 묶음 수가 실제 독립 관측 수에 가깝습니다. 춘천은 7~8월 비교 기간 {gd[gd['city'] == '춘천']['date'].nunique()}일 중 {n_hot.get('춘천', 0)}일이 폭염일이라 '평소'가 얇습니다.")
r.p("- 결론: 폭염일에 80대+ 결제가 다른 연령보다 몇 % 덜했다는 방향은 보이지만, 크기는 비교 연령에 따라 달라지고 생필품이 아닌 업종에서도 비슷하게 나타납니다. 여름 한 번, 휴가·방학과 겹친 관측이라 **탐색적 결과**로만 씁니다.")
r.p()
res6 = "⚠️ 탐색적 방향 재현" if any(sig.values()) else "❌ 재현 안 됨"
status.append(("I-6 폭염 시 고령 생필품 감소", res6,
               f"ASOS 기준 40·50대 대비 강남 {diff['강남']:+.1%}(CI {ci['강남'][0]:+.1%}~{ci['강남'][1]:+.1%}, 무작위 {fmt_perm_p(*pcount['강남'][0], '무작위')}, 이동 {fmt_perm_p(*pcount['강남'][1], '이동')}), "
               f"춘천 {diff['춘천']:+.1%}(CI {ci['춘천'][0]:+.1%}~{ci['춘천'][1]:+.1%}, 무작위 {fmt_perm_p(*pcount['춘천'][0], '무작위')}, 이동 {fmt_perm_p(*pcount['춘천'][1], '이동')}). "
               f"비교 연령에 따라 강남 {sens_rng['강남'][0]:+.1%}~{sens_rng['강남'][1]:+.1%}, 춘천 {sens_rng['춘천'][0]:+.1%}~{sens_rng['춘천'][1]:+.1%}. "
               f"생필품 아닌 업종도 강남 {plc['기타 업종 (위약)']['강남']:+.1%}, 춘천 {plc['기타 업종 (위약)']['춘천']:+.1%}. 독립 폭염 묶음 강남 {n_spell['강남']}개, 춘천 {n_spell['춘천']}개. 여름 한 번 관측",
               "계산 재현 (탐색적)"))
r.p(f"**재현 결과: {status[-1][1]}** ({status[-1][2]})")

# ============================================================ 6. 경보 규칙 성능
r.h("6. 시점 경보 규칙의 성능 (가짜 하락 주입 실험, 사후 계산)")
r.p("**주의: 이 절은 6개월 전체 자료로 평소 값과 흔들림(MAD)을 만든 사후 계산입니다.** 날짜 t의 판정에 t 뒤의 자료가 들어가므로 사전 경보 성능이 아닙니다. 과거 자료만 쓰는 재평가는 [EDA-6](../eda6_사전경보재평가/README.md)에 있습니다. 두 결과는 방법이 달라 개선·악화로 비교하지 않습니다.")
r.p("규칙: 연령대·성별 일별 결제에서 ① 요일 평균을 빼고 ② 같은 날 도시 전체에 공통인 변동을 뺀 뒤 ③ 남은 값이 robust z −2.5 이하인 날이 **7일 중 3일 이상**이면 경보. 경보 구간이 공휴일 앞뒤 1일에 걸리면 **반증 단계에서 기각**합니다.")
r.p("실험: 공휴일이 끼지 않은 14일 구간에 결제를 일부러 10/15/20% 줄여 넣고, **그 14일 안에서** 경보가 잡히는지 봅니다. 연령대·성별 집단(28개)마다 시작일 30곳씩 시험했습니다. 무주입 경보(배경 경보율)는 아무것도 넣지 않았을 때 같은 규칙으로 경보가 울리는 비율이며, 분모는 모든 집단 × 모든 14일 시작일(평가창)입니다. 실제 자료라 그 기간이 정상이었는지 모르므로 '오경보'라고 부르지 않습니다.")
base3 = daily(c2, ["city", "age_g", "SEX_CCD"])
keys = ["city", "age_g", "SEX_CCD"]
wide = base3.pivot_table(index="date", columns=keys, values="USE_CNT")
dates = wide.index
is_hol = dates.isin(list(HOL))
near_hol = np.array([any(abs((d_ - h_).days) <= 1 for h_ in HOL) for d_ in dates])
dow = dates.dayofweek.to_numpy()
cols = list(wide.columns)


def own_z(W):
    L = np.log(W + 1)
    res = L.copy()
    for d_ in range(7):
        m = (dow == d_) & ~is_hol
        res.loc[dow == d_] = L.loc[dow == d_] - L.loc[m].mean()
    out = []
    for cty in CITIES:
        sub = res.xs(cty, axis=1, level="city", drop_level=False)
        out.append(sub.sub(sub.median(axis=1), axis=0))
    own = pd.concat(out, axis=1)[W.columns]
    med = own[~is_hol].median()
    mad = (own[~is_hol] - med).abs().median() * 1.4826
    return (own - med) / mad


def alarm_in(zcol, start, length=14):
    """구간 [start, start+length) 안의 7일 창에서 3일 이상 z<-2.5 이고 공휴일 앞뒤가 아니면 경보."""
    flag = (np.asarray(zcol) < -2.5)
    for s in range(start, start + length - 6):
        if flag[s:s + 7].sum() >= 3 and not near_hol[s:s + 7].any():
            return True
    return False


cand = [i for i in range(7, len(dates) - 14) if not near_hol[i:i + 14].any()]
rng_alarm = np.random.default_rng(0)  # 시작일 추첨은 다른 절과 따로 (다른 절을 고쳐도 값이 안 바뀌게)
Z0 = own_z(wide)
fp_col = {col: np.mean([alarm_in(Z0[col], s) for s in cand]) for col in cols}
det = {drop: {} for drop in [0.10, 0.15, 0.20]}
for col in cols:
    starts = rng_alarm.choice(cand, 30, replace=False)
    for drop in det:
        hits = []
        for s in starts:
            W = wide.copy()
            W.iloc[s:s + 14, cols.index(col)] *= (1 - drop)
            hits.append(alarm_in(own_z(W)[col], s))
        det[drop][col] = np.mean(hits)
dt = pd.DataFrame({f"{int(d * 100)}% 하락 탐지": pd.Series(v) for d, v in det.items()})
dt["무주입 경보(배경 경보율)"] = pd.Series(fp_col)
dt.index = pd.MultiIndex.from_tuples(dt.index, names=keys)
by_age = dt.groupby(level="age_g").mean().reindex(AGE_ORDER)
by_age.index.name = "연령"
r.table(by_age, {c_: "{:.0%}" for c_ in by_age.columns})
r.p("(도시·성별 평균)")
r.p("- 시작일을 다시 추첨하면 연령별 값이 ±7%p 정도 변합니다(예: 80대+ 55%↔52%). 사후 계산 값이라 성능으로 인용하지 않습니다.")
by_age.reset_index().to_csv(r.dir / "경보_사후평가.csv", index=False, encoding="utf-8-sig")
fig, ax = plt.subplots(figsize=(7.5, 4))
for col_, color in [("10% 하락 탐지", "#90cdf4"), ("15% 하락 탐지", "#3182ce"), ("20% 하락 탐지", "#1a365d")]:
    ax.plot(AGE_ORDER, by_age[col_] * 100, marker="o", label=col_, color=color)
ax.plot(AGE_ORDER, by_age["무주입 경보(배경 경보율)"] * 100, marker="x", ls="--", label="무주입 경보(배경 경보율)", color="#c53030")
ax.set_ylabel("%")
ax.set_ylim(-3, 105)
ax.set_title("경보 탐지율 (사후 계산): 30~70대는 높고 80대+·20대는 낮다")
ax.legend()
r.fig(fig, "f05_alarm_by_age.png", "연령별 경보 탐지율")
weak = dt[dt["15% 하락 탐지"] < 0.5]
r.p(f"- 전체 평균: 15% 하락 탐지 {dt['15% 하락 탐지'].mean():.0%}, 무주입 경보(배경 경보율) {dt['무주입 경보(배경 경보율)'].mean():.1%}")
r.p(f"- 15% 하락을 절반도 못 잡는 집단: {', '.join(f'{a} {b} {c_}' for a, b, c_ in weak.index) if len(weak) else '없음'}")
noisy = dt["무주입 경보(배경 경보율)"].sort_values(ascending=False)
noisy = noisy[noisy > 0].head(3)
r.p(f"- 무주입 경보가 많은 집단: {', '.join(f'{a} {b} {c_} {v:.0%}' for (a, b, c_), v in noisy.items())}")
r.p("- **30~70대는 잘 잡히지만, 80대+와 춘천 20대(대학생, 학사일정 영향)는 약합니다.** 80대+는 표본이 작아 하루하루 흔들림이 크기 때문입니다. 80대+는 하락 폭이 클 때만 믿을 수 있고, 연휴·폭염·겨울 같은 '예정된 시점' 경보로 보완해야 합니다.")
real = []
for col in cols:
    z = np.asarray(Z0[col])
    i = 0
    while i < len(z) - 6:
        if (z[i:i + 7] < -2.5).sum() >= 3:
            real.append((col, dates[i], near_hol[i:i + 7].any()))
            i += 7
        else:
            i += 1
n_real, n_rej = len(real), sum(1 for x in real if x[2])
r.p(f"- 실제 데이터에서 울린 경보 {n_real}건 중 {n_rej}건은 공휴일 앞뒤였습니다(이 절의 옛 규칙은 공휴일 앞뒤를 바로 기각). EDA-6은 공휴일 인접을 '반증 검토 시작'으로만 보고 공휴일 기대값·공통요인·회복으로 나눠 판정합니다.")
det15_old = by_age.loc["80대+", "15% 하락 탐지"]
ok6 = dt["15% 하락 탐지"].mean() >= 0.6 and dt["무주입 경보(배경 경보율)"].mean() <= 0.05 and det15_old >= 0.6
status.append(("시점 경보 탐지 성능 (사후 계산, 사전 경보 성능 아님)", "✅ 충분" if ok6 else "⚠️ 집단별 차이 큼",
               f"15% 하락 탐지 전체 {dt['15% 하락 탐지'].mean():.0%} / 80대+ {det15_old:.0%}, 무주입 경보(배경 경보율) {dt['무주입 경보(배경 경보율)'].mean():.1%}, 실제 경보 {n_real}건 중 공휴일 앞뒤 {n_rej}건. 사전 경보 값은 EDA-6",
               "계산 재현 (사후)"))
r.p()
r.p(f"**판정: {status[-1][1]}** ({status[-1][2]})")

# ============================================================ 정리
r.h("정리: 인사이트 재현 결과")
j4 = pd.read_csv(ROOT / "reports" / "eda4_외부데이터결합" / "행정동_결합.csv", dtype={"adm_cd": str})
gn = j4[j4["city"] == "강남"]
rep1 = spearman_report(gn["1인세대 비율"], gn["고령(60+) 고립률"])
rep3 = spearman_report(gn["1인세대 비율"], gn["가장 가까운 거점(km)"])
ok_i1 = rep1["p_perm"] < 0.05 and rep1["rho"] > 0.5 and rep1["loo_min"] > 0.5
res_i1 = "✅ 재현" if ok_i1 else ("⚠️ 부분 재현" if rep1["p_perm"] < 0.05 and rep1["rho"] > 0 else "❌ 재현 안 됨")
res_i3 = "✅ 재현" if (rep3["p_perm"] < 0.05 and rep3["rho"] > 0) else "❌ 재현 안 됨"
r.p("EDA-4 결과(I-1, I-3)는 [EDA-4 결과 표](../eda4_외부데이터결합/행정동_결합.csv)에서 다시 계산했습니다. I-1은 순열 p<0.05, ρ>0.5, 하나씩 빼기 최솟값>0.5이면 ✅입니다. I-3은 1인세대 비율과 거점 거리가 유의한 양의 관계일 때만 ✅입니다.")
r.p()
r.p("해석 상태: 직접 확인(원본·문서에서 바로 확인) / 계산 재현(스크립트로 다시 계산됨) / 가설(가능한 설명) / 미확인")
r.p()
r.p("| 인사이트 | 결과 | 근거 | 해석 상태 |")
r.p("|---|---|---|---|")
r.p(f"| I-1 1인세대 밀집 동일수록 고령 고립률 높음(강남) | {res_i1} | ρ={rep1['rho']:.2f}({fmt_p(rep1['p_perm'])}, n={rep1['n']}), 하나씩 빼기 {rep1['loo_min']:.2f}~{rep1['loo_max']:.2f}. 이유(구성·맥락)는 미확정 | 계산 재현 (이유는 미확인) |")
r.p(f"| I-3 강남 1인세대 밀집 동 = 서비스 공백 | {res_i3} | 거점 거리와 ρ={rep3['rho']:.2f}({fmt_p(rep3['p_perm'])}). 춘천 면은 관측된 거점까지 직선거리가 큼(실제 공급 부족과는 다른 주장) | 계산 재현 |")
for name, res_, why, how in status:
    r.p(f"| {name} | {res_} | {why} | {how} |")
r.save()
print("done")
