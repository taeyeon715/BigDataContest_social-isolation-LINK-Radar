"""EDA-3 카드 기초

실행: uv run python notebooks/eda3_card_basics.py
결과: reports/eda3_카드기초/ (README.md, figs/)
"""

import numpy as np
import pandas as pd

from bigcontest.card import AGE_ORDER, daily, load_card1, load_card2
from bigcontest.config import load_config
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report

HOL = {pd.Timestamp(k): v for k, v in load_config()["holidays"].items()}
CITIES = ["강남", "춘천"]
AGE_COLOR = dict(zip(AGE_ORDER, plt.cm.viridis(np.linspace(0, 0.9, len(AGE_ORDER)))))

r = Report("eda3_카드기초", "EDA-3 카드 기초")
r.p("신한카드 **데이터2(주제2용)** 로 연령대별 소비 흐름을 봅니다. 시간대는 **데이터1(주제1용, 보조)** 로 봅니다.")
r.p()
r.p("공통 기준")
r.p("- 가맹점과 같은 시도에 사는 고객만(강남 = 서울 거주, 춘천 = 강원 거주). 동네 주민에 가까운 신호를 보기 위해서입니다.")
r.p("- 본사등록 업종·법인 제외, 10대 제외(결제수단 효과), 80대·90대는 '80대+'로 묶음")
r.p("- 결제건수는 연령별 배율이 곱해진 값이라 **같은 연령 안에서의 비율·변화**로만 봅니다.")

c2 = load_card2()

# ------------------------------------------------------------ 1. 소비 구성
r.h("1. 연령대별 소비 구성 (건수 비중)")
cats = ["생필품", "외식", "의료", "여가문화", "주점유흥", "생활서비스", "기타소매서비스"]
mix = c2.groupby(["city", "age_g", "cat"])["USE_CNT"].sum().unstack("cat")[cats]
mix = mix.div(mix.sum(axis=1), axis=0)
fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)
colors = plt.cm.Set2(np.arange(len(cats)))
for ax, c in zip(axes, CITIES):
    m = mix.loc[c].reindex(AGE_ORDER)
    bottom = np.zeros(len(m))
    for k, col in zip(cats, colors):
        ax.bar(m.index, m[k] * 100, bottom=bottom, label=k, color=col)
        bottom += m[k].to_numpy() * 100
    ax.set_title(c)
axes[0].set_ylabel("비중 (%)")
axes[1].legend(bbox_to_anchor=(1.01, 1), loc="upper left")
r.fig(fig, "f01_mix.png", "소비 구성")
t = mix[["의료", "외식", "생필품"]].unstack(0).reindex(AGE_ORDER)
t.columns = [f"{c} {k}" for k, c in t.columns]
t.index.name = "연령"
r.table(t[["강남 의료", "춘천 의료", "강남 외식", "춘천 외식", "강남 생필품", "춘천 생필품"]], {c: "{:.0%}" for c in t.columns})
r.p("- 나이가 많을수록 **의료** 비중이 커집니다(춘천 80대+는 결제 4건 중 1건 이상이 의료).")
r.p("- 이런 차이는 대부분 '나이'에서 오는 것이라 그 자체로 고립 신호가 아닙니다. 같은 연령 안에서 평소와 달라지는지를 봐야 합니다.")

# ------------------------------------------------------------ 2. 일별 흐름
r.h("2. 연령대별 일별 흐름")
d = daily(c2, ["city", "age_g"])
d["idx"] = d.groupby(["city", "age_g"])["USE_CNT"].transform(lambda s: s / s.median() * 100)
fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
for ax, c in zip(axes, CITIES):
    for a in ["30대", "50대", "70대", "80대+"]:
        s = d[(d["city"] == c) & (d["age_g"] == a)].set_index("date")["idx"].rolling(7, center=True).mean()
        ax.plot(s.index, s, label=a, color=AGE_COLOR[a])
    for day in HOL:
        ax.axvline(day, color="#cbd5e0", lw=0.8, ls="--")
    ax.set_title(f"{c} (7일 이동평균, 중앙값 = 100)")
    ax.set_ylabel("지수")
axes[0].legend(ncol=4)
r.fig(fig, "f02_daily.png", "일별 흐름")
r.p("- 큰 흐름은 연령과 상관없이 비슷하게 움직입니다(추석 연휴에 함께 떨어짐). 연령별 차이는 '얼마나 깊게 떨어지나'에서 납니다.")

# ------------------------------------------------------------ 3. 요일 효과
r.h("3. 요일 효과")
nh = d[~d["date"].isin(HOL)].copy()
nh["dow"] = nh["date"].dt.dayofweek
dw = nh.groupby(["city", "age_g", "dow"])["USE_CNT"].mean().unstack("dow")
wkday = dw[[0, 1, 2, 3, 4]].mean(axis=1)
sat = dw[5] / wkday
sun = dw[6] / wkday
t = pd.DataFrame({f"{c} 토/평일": sat.loc[c] for c in CITIES} | {f"{c} 일/평일": sun.loc[c] for c in CITIES}).reindex(AGE_ORDER)
t.index.name = "연령"
r.table(t, {c: "{:.2f}" for c in t.columns})
fig, ax = plt.subplots(figsize=(7, 4))
for c in CITIES:
    ax.plot(AGE_ORDER, sun.loc[c].reindex(AGE_ORDER), marker="o", label=f"{c} 일요일", color=CITY_COLOR[c])
    ax.plot(AGE_ORDER, sat.loc[c].reindex(AGE_ORDER), marker="s", ls="--", label=f"{c} 토요일", color=CITY_COLOR[c], alpha=0.6)
ax.axhline(1, color="#a0aec0", lw=0.8)
ax.set_ylabel("평일 대비")
ax.set_title("나이가 많을수록 주말, 특히 일요일 활동이 적다")
ax.legend()
r.fig(fig, "f03_weekday.png", "요일 효과")
r.p("- 고령층은 일요일 결제가 평일의 절반 수준입니다. 병원이 쉬는 영향이 크지만, 이는 **매주 반복되는 정상 패턴**입니다.")
r.p("- 그래서 기대값을 만들 때 **연령별 요일 효과**를 반드시 넣어야 합니다. 그러지 않으면 매주 일요일마다 고령층 경보가 울립니다.")

# ------------------------------------------------------------ 4. 공휴일
r.h("4. 공휴일 효과")
r.p("공휴일 결제를 '같은 요일의 평소 값(앞뒤 3주, 공휴일 제외)'과 비교한 비율입니다. 1보다 작을수록 평소보다 적게 쓴 것입니다.")


def holiday_ratio(frame, keys):
    out = []
    for day, name in HOL.items():
        win = frame[(frame["date"] - day).abs() <= pd.Timedelta(days=21)]
        base = win[(win["date"].dt.dayofweek == day.dayofweek) & ~win["date"].isin(HOL)].groupby(keys)["USE_CNT"].mean()
        obs = frame[frame["date"] == day].set_index(keys)["USE_CNT"]
        out.append((obs / base).rename(f"{day:%m/%d} {name}"))
    return pd.concat(out, axis=1)


hr = holiday_ratio(d, ["city", "age_g"])
for c in CITIES:
    r.h3(c)
    t = hr.loc[c].reindex(AGE_ORDER)
    t.index.name = "연령"
    r.table(t, {k: "{:.2f}" for k in t.columns})
chu = hr["10/06 추석"].unstack(0).reindex(AGE_ORDER)
r.p("- 추석 전날(10/5, 일요일) 60대 이상은 평소 일요일보다 오히려 많이 씁니다(1.1~1.4배). 연휴 전에 미리 장을 보는 모습입니다.")
r.p(f"- 추석 당일 활동은 평소의 강남 {chu['강남'].min():.0%}~{chu['강남'].max():.0%}, 춘천 {chu['춘천'].min():.0%}~{chu['춘천'].max():.0%} 수준입니다. 나이가 많을수록 더 깊게 떨어집니다.")

dm = daily(c2[c2["cat"].isin(["의료", "생필품"])], ["city", "age_g", "cat"])
hm = holiday_ratio(dm, ["city", "age_g", "cat"])["10/06 추석"].unstack(["city", "cat"]).reindex(AGE_ORDER)
fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
for ax, k in zip(axes, ["의료", "생필품"]):
    for c in CITIES:
        ax.plot(AGE_ORDER, hm[(c, k)], marker="o", label=c, color=CITY_COLOR[c])
    ax.set_title(f"추석 당일 {k} (평소 대비)")
    ax.axhline(1, color="#a0aec0", lw=0.8)
axes[0].legend()
r.fig(fig, "f04_chuseok_category.png", "추석 업종별")
med = hm.xs("의료", axis=1, level=1)
grc = hm.xs("생필품", axis=1, level=1)
r.p(f"- 추석 당일 **의료는 모든 연령에서 거의 멈춥니다**(평소의 {med.min().min():.0%}~{med.max().max():.0%}). 고령층일수록 더 낮습니다.")
r.p(f"- 생필품은 의료보다 덜 줄지만 고령층에서 더 많이 줄어듭니다(80대+: 강남 {grc.loc['80대+', '강남']:.0%}, 춘천 {grc.loc['80대+', '춘천']:.0%} / 40대: 강남 {grc.loc['40대', '강남']:.0%}, 춘천 {grc.loc['40대', '춘천']:.0%}).")
r.p("- 고령층은 평소 의료 비중이 크기 때문에, 연휴에 의료가 멈추면 생활 전체가 더 크게 줄어듭니다.")

# ------------------------------------------------------------ 5. 도시 공통 변동
r.h("5. 도시 전체가 함께 움직이는 부분")
r.p("연령대·성별 일별 결제에서 '그 집단의 요일 평균'을 빼고 남은 변동(잔차)을 봅니다. 이 잔차 중 **같은 날 도시 전체가 함께 움직인 변화**가 얼마나 되는지 계산했습니다(공휴일 제외). 원인(날씨·달력 등)은 이 계산으로 정하지 않습니다.")
r.p("- 공통 변동 = 같은 날 같은 도시 다른 연령·성 집단 잔차의 중앙값. **대상 집단 자신은 빼고**(leave-target-out) 계산합니다. 자기 자신을 넣으면 자기 값이 중앙값을 끌어당겨 공통 비중이 부풀려집니다.")
r.p("- 공통 비중 = 1 − Var(잔차 − 공통 변동) / Var(잔차). 연령 값은 남녀 두 집단의 평균입니다.")
d2 = daily(c2, ["city", "age_g", "SEX_CCD"])
d2 = d2[~d2["date"].isin(HOL)].copy()
d2["dow"] = d2["date"].dt.dayofweek
d2["ly"] = np.log(d2["USE_CNT"] + 1)
d2["res"] = d2["ly"] - d2.groupby(["city", "age_g", "SEX_CCD", "dow"])["ly"].transform("mean")
d2["common"] = d2.groupby(["city", "date"])["res"].transform("median")  # 이전 방식(자기 포함): 비교용
P = d2.pivot_table(index=["city", "date"], columns=["age_g", "SEX_CCD"], values="res")
lto = []
for c in CITIES:
    M = P.loc[c].dropna(axis=1, how="all")
    for k in M.columns:
        lto.append(pd.DataFrame({"city": c, "date": M.index, "age_g": k[0], "SEX_CCD": k[1], "common_lto": M.drop(columns=[k]).median(axis=1).to_numpy()}))
d2 = d2.merge(pd.concat(lto), on=["city", "date", "age_g", "SEX_CCD"], how="left")
d2["own"] = d2["res"] - d2["common_lto"]
d2["own_incl"] = d2["res"] - d2["common"]


def common_share(own_col):
    per = d2.groupby(["city", "age_g", "SEX_CCD"]).apply(lambda g: 1 - g[own_col].var() / g["res"].var(), include_groups=False)
    return per.groupby(level=["city", "age_g"]).mean().unstack(0).reindex(AGE_ORDER)


share = common_share("own")
# 이전 판 계산 그대로(자기 포함 중앙값, 남녀 행을 합쳐 분산 계산): 이전 값과 비교용
share_old = d2.groupby(["city", "age_g"]).apply(lambda g: 1 - g["own_incl"].var() / g["res"].var(), include_groups=False).unstack(0).reindex(AGE_ORDER)
st = pd.concat({"자기 제외(새 값)": share, "자기 포함(이전 방식)": share_old}, axis=1)
st.columns = [f"{c} {k}" for k, c in st.columns]
st = st[[f"{c} {k}" for c in CITIES for k in ["자기 제외(새 값)", "자기 포함(이전 방식)"]]]
st.index.name = "연령"
r.table(st, {c: "{:.0%}" for c in st.columns})
mid = share.loc[["30대", "40대", "50대", "60대"]]
mid_old = share_old.loc[["30대", "40대", "50대", "60대"]]
r.p(f"- 30~60대 일별 변동의 {mid.min().min():.0%}~{mid.max().max():.0%}는 **같은 날 도시 전체가 함께 움직인 변화**입니다(이전 자기 포함 계산 {mid_old.min().min():.0%}~{mid_old.max().max():.0%}). "
    f"전 연령으로 넓히면 {share.min().min():.0%}~{share.max().max():.0%}이고, 80대+는 강남 {share.loc['80대+', '강남']:.0%} · 춘천 {share.loc['80대+', '춘천']:.0%}입니다.")
r.p("- 그래서 특정 연령의 이상 신호를 찾을 때는 **도시 공통 변동을 먼저 빼야** 합니다. 80대+와 춘천 20대(대학생)는 고유한 흔들림이 더 큽니다. 80대+는 표본이 작은 탓도 있습니다.")
r.p("- 이 계산은 '같은 날 함께 움직였다'까지만 보여 줍니다. 그 원인이 날씨·달력인지는 따로 확인하지 않았습니다.")

ex = d2[(d2["city"] == "춘천") & (d2["SEX_CCD"] == "여성") & (d2["age_g"] == "70대")].set_index("date")
fig, ax = plt.subplots(figsize=(11, 3.5))
ax.plot(ex.index, ex["res"], lw=1, color="#a0aec0", label="잔차 (요일 효과 뺀 값)")
ax.plot(ex.index, ex["own"], lw=1, color="#c05621", label="고유 변동 (도시 공통까지 뺀 값)")
ax.axhline(0, color="#4a5568", lw=0.6)
ax.set_title("예: 춘천 70대 여성: 도시 공통 변동을 빼면 흔들림이 줄어든다")
ax.legend()
r.fig(fig, "f05_common_factor.png", "공통 변동")

# ------------------------------------------------------------ 6. 월별 추세
r.h("6. 월별 추세 (도시 전체 대비)")
mo = c2.groupby(["city", "age_g", c2["date"].dt.month])["USE_CNT"].sum()
city_mo = c2.groupby(["city", c2["date"].dt.month])["USE_CNT"].sum()
rel = (mo / city_mo).unstack()
rel = rel.div(rel[7], axis=0) * 100
t = rel[[9, 12]].unstack(0)
t.columns = [f"{c} {m}월" for m, c in t.columns]
t = t.reindex(AGE_ORDER)[["강남 9월", "강남 12월", "춘천 9월", "춘천 12월"]]
t.index.name = "연령"
r.p("도시 전체 결제 대비 각 연령의 비중을 7월 = 100으로 놓은 값입니다.")
r.p()
r.table(t, {c: "{:.0f}" for c in t.columns})
r.p("- 6개월 동안 특정 연령의 결제가 도시 전체보다 꾸준히 줄어드는 뚜렷한 흐름은 없습니다. 고령층에서 '지속적 감소'를 찾기보다 **특정 시점의 공백**을 보는 쪽이 맞습니다.")
r.p("- 춘천 20대는 9월에 늘어납니다(대학 개강).")

# ------------------------------------------------------------ 7. 시간대 (보조)
r.h("7. 시간대 (보조: 데이터1)")
r.p("데이터1은 주제1용 파일이라 보조로만 씁니다. 거주지 정보가 없어 외지인이 섞여 있으므로, 동네 손님 비중이 높은 고령층 위주로 해석합니다. LPG가스(자동결제)는 뺐습니다.")
c1 = load_card1()
tg = c1.groupby(["city", "age_g", "TIME_GB"])["USE_CNT"].sum().unstack("TIME_GB")
tg = tg.div(tg.sum(axis=1), axis=0)
t = pd.DataFrame({f"{c} 저녁(18~23시)": tg.loc[c, "18_23"] for c in CITIES} | {f"{c} 심야(0~5시)": tg.loc[c, "00_05"] for c in CITIES}).reindex(AGE_ORDER)
t.index.name = "연령"
r.table(t, {c: "{:.1%}" for c in t.columns})
mon = c1.groupby(["city", "age_g", c1["date"].dt.month, "TIME_GB"])["USE_CNT"].sum().unstack("TIME_GB")
ev = (mon["18_23"] / mon.sum(axis=1)).unstack("date")
fig, ax = plt.subplots(figsize=(7, 4))
for a in ["30대", "50대", "70대", "80대+"]:
    v = ev.loc[("춘천", a)]
    ax.plot([f"{m}월" for m in v.index], v * 100, marker="o", label=a, color=AGE_COLOR[a])
ax.set_ylabel("저녁(18~23시) 비중 (%)")
ax.set_title("춘천 저녁 결제 비중: 겨울에 줄어든다 (계절 효과)")
ax.legend()
r.fig(fig, "f06_evening.png", "저녁 비중")
r.p("- 나이가 많을수록 저녁·심야 결제가 적습니다.")
r.p("- 겨울로 갈수록 저녁 비중이 줄고 고령층에서 더 크게 줄지만, 해가 짧아지는 계절 효과라서 **보정 없이 신호로 쓰지 않습니다**(전년 자료가 없어 계절 보정이 어려움).")

# ------------------------------------------------------------ 정리
r.h("정리")
r.p("| 발견 | 설계에 주는 의미 |")
r.p("|---|---|")
r.p("| 소비 구성 차이는 대부분 나이 차이 | 같은 연령 안의 변화·이탈만 신호로 씀 |")
r.p("| 고령층 일요일 활동은 평일의 절반 수준(매주 반복) | 기대값에 연령별 요일 효과를 넣음 |")
r.p("| 추석 당일 의료는 거의 멈추고, 고령일수록 전체 활동이 더 깊게 떨어짐 | '언제' 축(연휴 공백)의 근거 |")
r.p(f"| 30~60대 하루하루 흔들림의 {mid.min().min():.0%}~{mid.max().max():.0%}가 같은 날 도시 전체 변화(80대+는 그보다 작음) | 이상 신호는 도시 공통 변동을 뺀 뒤 판단 |")
r.p("| 고령층의 꾸준한 감소 추세는 없음 | 지속 감소보다 특정 시점의 공백에 초점 |")
r.p("| 저녁 비중 감소는 계절 효과 | 보정 없이 신호로 쓰지 않음 |")

r.save()
print("done")
