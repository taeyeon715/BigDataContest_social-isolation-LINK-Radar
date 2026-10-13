"""EDA-1 데이터 진단

실행: uv run python notebooks/eda1_data_quality.py
결과: reports/eda1_데이터진단/ (README.md, figs/)
"""

import numpy as np
import pandas as pd

from bigcontest.config import ROOT, load_config
from bigcontest.plot import CITY_COLOR, plt, save

cfg = load_config()
D = cfg["interim_dir"]
OUT = ROOT / "reports" / "eda1_데이터진단"
FIG = OUT / "figs"
AGE_COLS = [f"{s}_FLOW_POP_CNT_{a}" for s in ["MAN", "WMAN"] for a in ["10G", "20G", "30G", "40G", "50G", "60GU"]]
TIME_COLS = [f"TMST_{h:02d}" for h in range(24)]
DAYS = ["MON", "TUS", "WED", "THU", "FRI", "SAT", "SUN"]
WKDY_COLS = [f"FLOW_POP_CNT_{d}" for d in DAYS]
KEY = ["STD_YM", "BLOCK_CD", "X_COORD", "Y_COORD"]

md = []  # 리포트 본문


def h(text):
    md.append(f"\n## {text}\n")


def p(text=""):
    md.append(text)


def fig_link(name, caption):
    md.append(f"\n![{caption}](figs/{name})\n")


age = pd.read_parquet(D / "tel_age.parquet")
tim = pd.read_parquet(D / "tel_time.parquet")
wk = pd.read_parquet(D / "tel_wkdy.parquet")
c1 = pd.read_parquet(D / "card1.parquet")
c2 = pd.read_parquet(D / "card2.parquet")

md.append("# EDA-1 데이터 진단\n")
md.append("[← 전체 리포트 목록](../README.md)\n")
md.append("`uv run python notebooks/eda1_data_quality.py` 로 만든 결과입니다. 숫자는 모두 정리된 데이터(`data/interim`) 기준입니다.\n")

# ---------------------------------------------------------------- 1. 통신 규모
h("1. 통신: 규모와 빈칸")
age["cell"] = age["BLOCK_CD"] + "_" + age["X_COORD"].astype(str) + "_" + age["Y_COORD"].astype(str)
cells = age.groupby("city")["cell"].nunique()
months_per_cell = age.groupby(["city", "cell"])["STD_YM"].nunique()
full = months_per_cell.eq(6).groupby("city").mean()
adm = age.groupby("city")["adm_cd"].nunique()
p("| 도시 | 50m 칸 수 | 6개월 모두 있는 칸 | 행정동 수 |")
p("|---|---:|---:|---:|")
for c in ["강남", "춘천"]:
    p(f"| {c} | {cells[c]:,} | {full[c]:.0%} | {adm[c]} |")
p()
p("- 행정동 수가 실제와 같습니다(강남 22개, 춘천 25개 읍·면·동). `BLOCK_CD` 앞 8자리로 행정동을 정할 수 있습니다.")
p("- 어떤 달에 행이 없는 칸은 사람이 거의 없는 칸입니다(아래 3번 참고).")

# ---------------------------------------------------------------- 2. 세 표 정합성
h("2. 통신: 세 파일은 같은 총량을 나눈 것")
a_sum = age.set_index(KEY)[AGE_COLS].sum(axis=1).rename("age")
t_sum = tim.set_index(KEY)[TIME_COLS].sum(axis=1).rename("time")
w = wk.set_index(KEY)[WKDY_COLS]


def weekday_counts(ym):
    days = pd.date_range(f"{ym[:4]}-{ym[4:]}-01", periods=pd.Period(ym, "M").days_in_month)
    return np.bincount(days.dayofweek, minlength=7)


wts = {ym: weekday_counts(ym) for ym in age["STD_YM"].unique()}
w_ym = w.index.get_level_values("STD_YM")
weights = np.vstack([wts[ym] for ym in w_ym])
w_weighted = pd.Series((w.to_numpy() * weights).sum(axis=1) / weights.sum(axis=1), index=w.index, name="wkdy_w")
w_simple = w.mean(axis=1).rename("wkdy_s")
cmp = pd.concat([a_sum, t_sum, w_weighted, w_simple], axis=1, join="inner")
cmp = cmp[cmp["age"] >= 10]
err = {k: np.log(cmp[k] / cmp["age"]).abs() for k in ["time", "wkdy_w", "wkdy_s"]}
p("같은 칸·같은 달에서 각 파일의 합을 비교했습니다(하루 10 이상인 칸). 값은 성연령 합과의 차이입니다.")
p()
p("| 비교 | 평균 차이 | 상위 10% 차이 |")
p("|---|---:|---:|")
p(f"| 시간대 24칸 합 | {err['time'].mean():.1%} | {err['time'].quantile(0.9):.1%} |")
p(f"| 요일 값 **요일 수로 가중평균** | {err['wkdy_w'].mean():.1%} | {err['wkdy_w'].quantile(0.9):.1%} |")
p(f"| 요일 값 단순평균 | {err['wkdy_s'].mean():.1%} | {err['wkdy_s'].quantile(0.9):.1%} |")
p()
p("- 세 파일은 같은 하루 총량을 성연령·시간대·요일로 나눈 것입니다.")
p("- 요일 값은 그달에 월요일이 몇 번, 일요일이 몇 번 있었는지로 가중평균하면 총량과 거의 정확히 맞습니다. 단순평균은 몇 % 어긋납니다.")
p("- 그래도 **세 파일을 칸 단위로 곱하거나 합쳐서 '60대의 밤 활동' 같은 값을 만들 수는 없습니다.**")

fig, ax = plt.subplots(figsize=(6, 4))
ax.hist(np.log2(cmp["time"] / cmp["age"]).clip(-1, 1), bins=80, color="#4a5568")
ax.set_xlabel("log2(시간대 합 ÷ 성연령 합)")
ax.set_ylabel("칸·월 수")
ax.set_title("시간대 합과 성연령 합은 거의 같다")
save(fig, FIG / "f01_tables_consistency.png")
fig_link("f01_tables_consistency.png", "세 파일 정합성")

# ---------------------------------------------------------------- 3. 통신 값 단위
h("3. 통신: 값의 단위와 0")
vals = age[AGE_COLS].to_numpy().ravel()
pos = vals[vals > 0]
small = np.sort(np.unique(np.round(pos[pos < 0.4], 2)))
zero_share = (vals == 0).mean()
cell_total = age.groupby(["city", "cell"])[AGE_COLS].sum().sum(axis=1) / months_per_cell
p(f"- 0인 값의 비율: {zero_share:.0%} (성연령 12칸 기준)")
p(f"- 가장 작은 값들: {', '.join(f'{v:.2f}' for v in small[:10])} … → 약 0.025 간격의 계단 모양. 정수 인원에 보정 배율을 곱한 값으로 보입니다.")
p(f"- 칸의 하루 평균 체류량 중앙값: 강남 {cell_total['강남'].median():.1f}, 춘천 {cell_total['춘천'].median():.1f}")
p("- 춘천은 산·농지 칸이 많아 값이 아주 작은 칸이 대부분입니다.")

fig, ax = plt.subplots(figsize=(6, 4))
for c in ["강남", "춘천"]:
    ax.hist(np.log10(cell_total[c] + 0.01), bins=60, alpha=0.6, label=c, color=CITY_COLOR[c])
ax.set_xlabel("log10(칸의 하루 평균 체류량)")
ax.set_ylabel("칸 수")
ax.set_title("칸별 체류량 분포")
ax.legend()
save(fig, FIG / "f02_cell_size.png")
fig_link("f02_cell_size.png", "칸별 체류량")

# ---------------------------------------------------------------- 4. 칸 단위 변화는 잡음
h("4. 통신: 작은 단위의 월별 변화는 잡음")
age["total"] = age[AGE_COLS].sum(axis=1)


age["g250"] = (age["X_COORD"] // 250).astype(int).astype(str) + "_" + (age["Y_COORD"] // 250).astype(int).astype(str)
rows = []
for level, name in [("cell", "50m 칸"), ("g250", "250m 격자"), ("oa13", "집계구(코드 앞 13자리)"), ("adm_cd", "행정동")]:
    for c in ["강남", "춘천"]:
        sub = age[age["city"] == c]
        g = sub.groupby([level, "STD_YM"])["total"].sum().unstack()
        g = g[(g > 0).all(axis=1) & (g.mean(axis=1) >= 5)]
        r = np.log(g["202509"] / g["202507"]).corr(np.log(g["202512"] / g["202510"]), method="spearman")
        rows.append((name, c, len(g), r))
p("앞 기간(7→9월) 변화와 뒤 기간(10→12월) 변화의 순위상관입니다. 진짜 추세라면 두 기간 변화가 비슷해야 합니다.")
p()
p("| 단위 | 도시 | 단위 수 | 상관 |")
p("|---|---|---:|---:|")
for name, c, n, r in rows:
    p(f"| {name} | {c} | {n:,} | {r:.2f} |")
p()
p("- 50m 칸·250m 격자·집계구 단위에서는 두 기간 변화의 관련이 약합니다. **이 단위의 월별 변화를 그대로 신호로 쓰지 않습니다.**")
p("- 상관이 낮은 이유는 두 가지일 수 있습니다: 값이 흔들리는 잡음이거나, 애초에 변화가 거의 없거나. 강남 행정동(0.07)은 후자에 가깝습니다(행정동 변화 폭 자체가 작음).")
p("- 일관된 변화는 춘천 행정동(읍·면의 겨울 감소)에서 보입니다. 그래서 **구성(연령 비중 등)은 250m 격자로, 변화는 행정동이나 목적에 맞게 고른 격자 묶음으로** 봅니다(EDA-5에서 춘천 읍·면 주민형 격자는 두 기간 상관 0.63).")

# ---------------------------------------------------------------- 5. 카드 구성
h("5. 카드: 법인과 본사등록 업종")
p("카드 데이터1(시간대 있음)은 주제 1용, 데이터2(거주지 있음)는 주제 2용입니다. 우리는 데이터2를 주력으로, 데이터1을 보조로 씁니다.")
p()
corp_cnt = c1.loc[c1["corp"], "USE_CNT"].sum() / c1["USE_CNT"].sum()
corp_amt = c1.loc[c1["corp"], "TS_AT"].sum() / c1["TS_AT"].sum()
p(f"- 데이터1에는 정의서와 달리 **법인카드**가 있습니다: 건수의 {corp_cnt:.1%}, 금액의 {corp_amt:.1%} → 개인 분석에서 제외")
p()
p("| 도시 | 본사등록 업종 비중(건수) | 본사등록 업종 비중(금액) |")
p("|---|---:|---:|")
for c in ["강남", "춘천"]:
    s = c2[c2["city"] == c]
    p(f"| {c} | {s.loc[s['hq'], 'USE_CNT'].sum() / s['USE_CNT'].sum():.0%} | {s.loc[s['hq'], 'TS_AT'].sum() / s['TS_AT'].sum():.0%} |")
p()
p("- 쇼핑몰·보험·세금 같은 업종은 회사 본사가 강남에 있어서 잡힌 것으로 보입니다. **강남은 이 업종을 빼지 않으면 분석이 크게 왜곡됩니다.**")

hq_ind = (
    c2[c2["city"] == "강남"].groupby("MCT_RY_CD")["USE_CNT"].sum().sort_values(ascending=False).head(12)
)
fig, ax = plt.subplots(figsize=(7, 4.5))
colors = ["#c53030" if c2.loc[c2["MCT_RY_CD"] == i, "hq"].iloc[0] else "#718096" for i in hq_ind.index]
ax.barh(hq_ind.index[::-1], hq_ind.values[::-1] / 1e6, color=colors[::-1])
ax.set_xlabel("결제건수 합 (백만, 배율 포함)")
ax.set_title("강남 결제건수 상위 업종 (빨강 = 본사등록 업종)")
save(fig, FIG / "f03_gangnam_hq.png")
fig_link("f03_gangnam_hq.png", "강남 본사등록 업종")

# ---------------------------------------------------------------- 6. 카드 건수 배율
h("6. 카드: 결제건수는 '배율이 곱해진 값'")
pc = c2[~c2["hq"]]
ages = ["10대", "20대", "30대", "40대", "50대", "60대", "70대", "80대", "90대"]
p("결제건수(`USE_CNT`)의 작은 값을 세어 보면 5, 6, 11, 12, 13, 16~19 … 처럼 띄엄띄엄 나오고 7~10은 없습니다. 실제 1건이 5~6으로, 2건이 11~13으로 기록된 것으로 보입니다(추론).")
p()
rows = []
for a in ages:
    v = pc.loc[pc["age"] == a, "USE_CNT"]
    n5, n6 = (v == 5).sum(), (v == 6).sum()
    rows.append((a, n5, n6, n5 / (n5 + n6)))
p("실제 1건으로 보이는 값(5 또는 6) 중 5의 비율을 연령별로 보면 배율 차이가 드러납니다. 5가 많을수록 배율이 작습니다.")
p()
p("| 연령 | 값이 5인 행 | 값이 6인 행 | 5의 비율 |")
p("|---|---:|---:|---:|")
for a, n5, n6, r in rows:
    p(f"| {a} | {n5:,} | {n6:,} | {r:.0%} |")
p()
p("- 10대는 거의 6, 40대 이상은 대부분 5입니다. 배율이 **연령마다 다릅니다**(10~30대가 크고 40대 이상은 비슷). 그래서 '10대가 70대보다 몇 배 더 썼다' 같은 연령 간 비교는 왜곡됩니다.")
p("- **같은 연령 안에서의 변화나 비중**으로만 비교합니다. 최소값 5는 실제 1건에 배율을 곱한 값으로 **보입니다**(값 분포로 한 추론이며 제공 기관 확인은 아님). 그래서 행이 없는 조합은 0건으로 **가정**하고, 이 가정은 민감도 분석으로 확인합니다.")

fig, ax = plt.subplots(figsize=(7, 4))
for a, color in [("20대", "#2b6cb0"), ("50대", "#38a169"), ("80대", "#c05621")]:
    v = pc.loc[pc["age"] == a, "USE_CNT"]
    cnt = v[v <= 30].value_counts().reindex(range(1, 31), fill_value=0)
    ax.plot(cnt.index, cnt.values / cnt.sum(), marker="o", ms=3, label=a, color=color)
ax.set_xlabel("결제건수 값")
ax.set_ylabel("비율")
ax.set_title("결제건수 값 분포: 7~10이 비어 있음")
ax.legend()
save(fig, FIG / "f04_use_cnt_lattice.png")
fig_link("f04_use_cnt_lattice.png", "결제건수 배율")

# ---------------------------------------------------------------- 7. 두 파일 관계
h("7. 카드: 데이터1과 데이터2는 같은 결제")
d1 = c1[~c1["corp"]].groupby(["date", "city"])["TS_AT"].sum()
d2 = c2.groupby(["date", "city"])["TS_AT"].sum()
ratio = (d2 / d1).describe()
p(f"- 날짜·도시별 금액 합의 비율(데이터2 ÷ 데이터1 개인분): 중앙값 {ratio['50%']:.4f}, 최소 {ratio['min']:.4f}, 최대 {ratio['max']:.4f}")
p("- 데이터2는 데이터1의 개인 결제를 고객 거주지별로 다시 나눈 것입니다. **두 파일을 더하면 두 번 센 것이 됩니다.**")

# ---------------------------------------------------------------- 8. 거주지
h("8. 카드: 가맹점 위치 ≠ 고객 거주지")
loc = pc.groupby(["city", "age"]).apply(lambda s: s.loc[s["local"], "USE_CNT"].sum() / s["USE_CNT"].sum(), include_groups=False).unstack(0)
p("본사등록 업종을 뺀 결제 중, 가맹점과 같은 시도(강남=서울, 춘천=강원)에 사는 고객의 비중입니다.")
p()
p("| 연령 | 강남 | 춘천 |")
p("|---|---:|---:|")
for a in ages:
    p(f"| {a} | {loc.loc[a, '강남']:.0%} | {loc.loc[a, '춘천']:.0%} |")
p()
p("- 강남 가게 손님의 절반가량은 서울 밖 사람입니다. 나이가 많을수록 동네 손님 비중이 높습니다.")
p("- '서울 거주'는 강남구 주민이라는 뜻이 아닙니다(시도까지만 구분).")

fig, ax = plt.subplots(figsize=(6.5, 4))
for c in ["강남", "춘천"]:
    ax.plot(ages, loc[c].reindex(ages), marker="o", label=c, color=CITY_COLOR[c])
ax.set_ylim(0, 1)
ax.set_ylabel("같은 시도 거주 고객 비중")
ax.set_title("나이가 많을수록 동네 손님")
ax.legend()
save(fig, FIG / "f05_local_share.png")
fig_link("f05_local_share.png", "거주지 비중")

# ---------------------------------------------------------------- 9. 일별 흐름
h("9. 카드: 날짜별 흐름과 튀는 날")
daily = pc[pc["local"]].groupby(["date", "city"])["USE_CNT"].sum().unstack()
idx = daily / daily.median() * 100
fig, ax = plt.subplots(figsize=(10, 4))
for c in ["강남", "춘천"]:
    ax.plot(idx.index, idx[c], lw=1, label=c, color=CITY_COLOR[c])
for d in cfg["holidays"]:
    ax.axvline(pd.Timestamp(d), color="#a0aec0", lw=0.8, ls="--")
ax.set_ylabel("일별 결제건수 지수 (중앙값 = 100)")
ax.set_title("지역 거주 고객 일별 결제 (점선 = 공휴일)")
ax.legend()
save(fig, FIG / "f06_daily_index.png")
fig_link("f06_daily_index.png", "일별 흐름")
low = idx.stack().sort_values().head(6)
p("가장 낮은 날:")
p()
p("| 날짜 | 도시 | 지수 | 공휴일 |")
p("|---|---|---:|---|")
for (d, c), v in low.items():
    p(f"| {d:%m-%d} ({'월화수목금토일'[d.dayofweek]}) | {c} | {v:.0f} | {cfg['holidays'].get(d.strftime('%Y-%m-%d'), '')} |")
p()
p("- 크게 떨어지는 날은 추석 연휴와 일요일입니다. 이상치처럼 보이는 날 대부분은 달력으로 설명됩니다.")
pre = idx.loc["2025-09-26":"2025-10-02"].max()
p(f"- 반대로 추석 직전(9/26~10/2)에는 결제가 평소보다 많습니다(최고 지수 강남 {pre['강남']:.0f}, 춘천 {pre['춘천']:.0f}). 연휴 전에 미리 장을 보는 모습입니다.")

teen = pc[(pc["local"]) & (pc["city"] == "춘천") & (pc["age"] == "10대")].groupby(pc["date"].dt.month)["USE_CNT"].sum()
p(f"- 춘천 10대 결제는 9월부터 크게 늘어 유지됩니다(7월 대비 9월 {teen[9] / teen[7] - 1:+.0%}). 개강·결제수단 변화로 보이며 행동 신호로 쓰지 않습니다.")

# ---------------------------------------------------------------- 10. 결측 조합
h("10. 카드: 비어 있는 조합")
grid = pc[pc["local"]].groupby(["city", "age", "cat"])["date"].nunique() / pc["date"].nunique()
miss = (1 - grid).unstack("cat")
p("지역 거주 고객 기준, 도시·연령·업종 묶음 조합에서 결제가 없는 날의 비율입니다(10% 이상만).")
p()
big = miss.stack()
big = big[big >= 0.10].sort_values(ascending=False)
p("| 도시 | 연령 | 업종 묶음 | 결제 없는 날 비율 |")
p("|---|---|---|---:|")
for (c, a, k), v in big.head(12).items():
    p(f"| {c} | {a} | {k} | {v:.0%} |")
p()
p("- 비어 있는 조합은 80·90대 × 주점·여가처럼 원래 드문 조합에 몰려 있습니다. **80대와 90대는 묶어서** 봅니다.")

# ---------------------------------------------------------------- 정리
h("정리: 전처리·해석 규칙")
p("| 규칙 | 이유 |")
p("|---|---|")
p("| 통신 12월 중복 제거 | 시간대·요일 파일이 두 번씩 들어 있음 |")
p("| 통신 구성은 250m 격자 이상, 변화는 행정동·목적별 격자 묶음으로 분석 | 작은 단위 월변화는 잡음이 큼 |")
p("| 통신 세 파일을 곱하거나 합치지 않음 | 성연령 × 시간대 교차 정보가 없음 |")
p("| 요일 값은 요일 수로 가중평균 | 그래야 총량과 맞음 |")
p("| 카드 법인 제외, 본사등록 업종 제외 | 개인 생활 소비가 아님 |")
p("| 카드 데이터1·2를 더하지 않음 | 같은 결제를 다르게 나눈 것 |")
p("| 카드는 같은 연령 안에서만 비교 | 결제건수에 연령별 배율 |")
p("| 카드 80대·90대 묶음, 10대 제외 | 표본이 작고, 10대는 결제수단 효과 |")
p("| 공휴일·일요일은 이상치로 보지 않음 | 달력으로 설명됨 |")

(OUT / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print("done")
from bigcontest.gallery import build  # noqa: E402

build()
