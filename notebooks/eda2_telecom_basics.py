"""EDA-2 통신 기초

실행: uv run python notebooks/eda2_telecom_basics.py
결과: reports/eda2_통신기초/ (README.md, figs/, 행정동_프로필.csv)
"""

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from bigcontest.config import ROOT, load_config
from bigcontest.plot import CITY_COLOR, plt, save
from bigcontest.telecom import (
    AGE_COLS, AGES, SEXES, TIME_COLS, WKDY_COLS, adm_names, age_cols, grid_id,
)

D = load_config()["interim_dir"]
OUT = ROOT / "reports" / "eda2_통신기초"
FIG = OUT / "figs"
md = []


def h(t):
    md.append(f"\n## {t}\n")


def p(t=""):
    md.append(t)


def fig_link(name, cap):
    md.append(f"\n![{cap}](figs/{name})\n")


def table(df, fmt):
    cols = list(df.columns)
    lines = ["| " + " | ".join([df.index.name or ""] + cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for i, r in df.iterrows():
        lines.append("| " + " | ".join([str(i)] + [fmt.get(c, "{}").format(r[c]) for c in cols]) + " |")
    return "\n".join(lines)


age = pd.read_parquet(D / "tel_age.parquet")
tim = pd.read_parquet(D / "tel_time.parquet")
wk = pd.read_parquet(D / "tel_wkdy.parquet")
names = adm_names().set_index("adm_cd")

age["total"] = age[AGE_COLS].sum(axis=1)
age["s60"] = age[age_cols("60GU")].sum(axis=1)
age["s2030"] = age[age_cols("20G") + age_cols("30G")].sum(axis=1)
age["f60"] = age["WMAN_FLOW_POP_CNT_60GU"]
age["m60"] = age["MAN_FLOW_POP_CNT_60GU"]
tim["total"] = tim[TIME_COLS].sum(axis=1)
tim["night"] = tim[[f"TMST_{h:02d}" for h in range(0, 6)]].sum(axis=1)
tim["day"] = tim[[f"TMST_{h:02d}" for h in range(11, 16)]].sum(axis=1)
tim["morning"] = tim[[f"TMST_{h:02d}" for h in range(6, 10)]].sum(axis=1)
wk["weekday"] = wk[WKDY_COLS[:5]].mean(axis=1)
wk["weekend"] = wk[WKDY_COLS[5:]].mean(axis=1)

md.append("# EDA-2 통신 기초\n")
md.append("[← 전체 리포트 목록](../README.md)\n")
md.append("SK텔레콤 유동인구로 강남·춘천의 **동네 구조**를 봅니다. 값은 하루 평균 체류량(보정된 추정값)이고, 6개월 평균 기준입니다.\n")
md.append("> 유동인구는 그곳에 머문 사람이지 주민이 아닙니다. 특히 강남은 출퇴근 인구가 많습니다.\n")

# ------------------------------------------------------------ 1. 도시 구성
h("1. 도시별 성·연령 구성")
comp = age.groupby("city")[AGE_COLS].sum()
comp = comp.div(comp.sum(axis=1), axis=0)
fig, ax = plt.subplots(figsize=(8, 4))
labels = [f"{SEXES[c.split('_')[0]]} {AGES[c.split('_')[-1]]}" for c in AGE_COLS]
x = np.arange(len(AGE_COLS))
for i, c in enumerate(["강남", "춘천"]):
    ax.bar(x + (i - 0.5) * 0.4, comp.loc[c] * 100, width=0.4, label=c, color=CITY_COLOR[c])
ax.set_xticks(x, labels, rotation=45)
ax.set_ylabel("비중 (%)")
ax.set_title("성·연령 구성: 강남은 30~50대, 춘천은 60대+가 두껍다")
ax.legend()
save(fig, FIG / "f01_city_composition.png")
fig_link("f01_city_composition.png", "도시 구성")
s60 = comp[age_cols("60GU")].sum(axis=1)
s2030 = comp[age_cols("20G") + age_cols("30G")].sum(axis=1)
p(f"- 60대+ 비중: 강남 {s60['강남']:.1%}, 춘천 {s60['춘천']:.1%}")
p(f"- 20·30대 비중: 강남 {s2030['강남']:.1%}, 춘천 {s2030['춘천']:.1%}")
p("- 두 도시는 성격이 많이 다르므로 지표는 **도시 안에서 순위나 표준화**로 비교합니다.")

# ------------------------------------------------------------ 2. 행정동 프로필
h("2. 행정동 프로필")
a_d = age.groupby(["city", "adm_cd", "STD_YM"])[["total", "s60", "s2030", "f60", "m60"]].sum()
a_avg = a_d.groupby(["city", "adm_cd"]).mean()
t_d = tim.groupby(["adm_cd"])[["total", "night", "day", "morning"] + TIME_COLS].sum()
w_d = wk.groupby(["adm_cd"])[["weekday", "weekend"]].sum()
prof = a_avg.reset_index().set_index("adm_cd")
# 원천 체류량(SKT 값)은 레포에 올리지 않음: 같은 도시 행정동 평균을 100으로 둔 지수로만 남김
prof["체류 규모(도시 평균=100)"] = prof["total"] / prof.groupby("city")["total"].transform("mean") * 100
prof["60대+"] = prof["s60"] / prof["total"]
prof["20·30대"] = prof["s2030"] / prof["total"]
prof["고령 여/남"] = prof["f60"] / prof["m60"]
prof["심야(0~5시)"] = t_d["night"] / t_d["total"]
prof["아침(6~9시)"] = t_d["morning"] / t_d["total"]
prof["심야/낮"] = t_d["night"] / t_d["day"]
peak = t_d[TIME_COLS].max(axis=1)
prof["활동시간폭"] = (t_d[TIME_COLS].ge(peak * 0.5, axis=0)).sum(axis=1)
prof["주말/평일"] = w_d["weekend"] / w_d["weekday"]
jul = a_d.xs("202507", level="STD_YM")["total"].droplevel(0)
dec = a_d.xs("202512", level="STD_YM")["total"].droplevel(0)
prof["7→12월"] = dec / jul - 1
prof["동"] = names["adm_nm"].reindex(prof.index)
prof["유형"] = names["adm_type"].reindex(prof.index)
COLS = ["체류 규모(도시 평균=100)", "60대+", "20·30대", "고령 여/남", "심야(0~5시)", "아침(6~9시)", "심야/낮", "활동시간폭", "주말/평일", "7→12월"]
FMT = {"체류 규모(도시 평균=100)": "{:.0f}", "60대+": "{:.1%}", "20·30대": "{:.1%}", "고령 여/남": "{:.2f}", "심야(0~5시)": "{:.1%}",
       "아침(6~9시)": "{:.1%}", "심야/낮": "{:.2f}", "활동시간폭": "{:.0f}", "주말/평일": "{:.2f}", "7→12월": "{:+.0%}"}
prof[["city", "동", "유형"] + COLS].to_csv(OUT / "행정동_프로필.csv", encoding="utf-8-sig")
p("지표 설명")
p()
p("| 지표 | 뜻 |")
p("|---|---|")
p("| 체류 규모(도시 평균=100) | 하루 평균 체류량을 같은 도시 행정동 평균 = 100으로 나타낸 지수. 원천 체류량은 재배포하지 않으려고 싣지 않음 |")
p("| 60대+, 20·30대 | 체류량 중 해당 연령 비중 |")
p("| 고령 여/남 | 60대+ 여성 ÷ 남성 |")
p("| 심야, 아침 | 24시간 중 해당 시간대 비중 |")
p("| 심야/낮 | 0~5시 ÷ 11~15시. 높을수록 사람이 사는 곳(주거형), 낮을수록 일하러 오는 곳(업무형) |")
p("| 활동시간폭 | 하루 중 가장 붐비는 시간의 절반 이상인 시간 수 |")
p("| 주말/평일 | 주말 하루 평균 ÷ 평일 하루 평균 |")
p("| 7→12월 | 7월 대비 12월 체류량 변화 |")
for c in ["강남", "춘천"]:
    t = prof[prof["city"] == c].sort_values("60대+", ascending=False)
    t = t.set_index("동")[["유형"] + COLS]
    t.index.name = "동"
    p(f"\n### {c} (60대+ 비중 순)\n")
    p(table(t, FMT))

for c in ["강남", "춘천"]:
    t = prof[prof["city"] == c].set_index("동")[COLS].drop(columns=["체류 규모(도시 평균=100)"])
    z = (t - t.median()) / t.std()
    z = z.loc[t.sort_values("60대+", ascending=False).index]
    fig, ax = plt.subplots(figsize=(8, 0.32 * len(z) + 1.5))
    im = ax.imshow(z.to_numpy(), cmap="RdBu_r", vmin=-2.5, vmax=2.5, aspect="auto")
    ax.set_xticks(range(len(z.columns)), z.columns, rotation=40, ha="right")
    ax.set_yticks(range(len(z)), z.index)
    ax.set_title(f"{c} 행정동 지표 (도시 안에서 표준화, 빨강 = 높음)")
    fig.colorbar(im, ax=ax, shrink=0.6)
    save(fig, FIG / f"f02_profile_{c}.png")
    fig_link(f"f02_profile_{c}.png", f"{c} 행정동 지표")

# ------------------------------------------------------------ 3. 250m 지도
h("3. 250m 격자 지도")
p("50m 칸은 값이 작고 흔들려서 250m 격자로 묶었습니다. 하루 체류량 20 미만 격자는 비중 지도에서 뺐습니다.")
age["g"] = grid_id(age["X_COORD"], age["Y_COORD"])
tim["g"] = grid_id(tim["X_COORD"], tim["Y_COORD"])
g_a = age.groupby(["city", "g", "STD_YM"])[["total", "s60", "s2030"]].sum().groupby(["city", "g"]).mean()
g_t = tim.groupby(["city", "g"])[["night", "day", "total"]].sum()
g = g_a.join(g_t, rsuffix="_t")
g["60대+"] = g["s60"] / g["total"]
g["20·30대"] = g["s2030"] / g["total"]
g["심야/낮"] = g["night"] / g["day"]
xy = g.index.get_level_values("g").str.split("_", expand=True)
g["gx"] = xy.get_level_values(0).astype(int).to_numpy() * 250 + 125
g["gy"] = xy.get_level_values(1).astype(int).to_numpy() * 250 + 125
cent = age.groupby("adm_cd")[["X_COORD", "Y_COORD"]].median()

for c in ["강남", "춘천"]:
    gg = g.loc[c]
    gg_ok = gg[gg["total"] >= 20]
    fig, axes = plt.subplots(1, 4, figsize=(20, 5.5) if c == "강남" else (20, 6))
    specs = [
        ("total", "하루 체류량 (log)", "viridis", np.log10(gg["total"].clip(lower=1)), gg),
        ("60대+", "60대+ 비중", "YlOrRd", gg_ok["60대+"], gg_ok),
        ("20·30대", "20·30대 비중", "PuBu", gg_ok["20·30대"], gg_ok),
        ("심야/낮", "심야/낮 (높음 = 주거형)", "PRGn", np.log2(gg_ok["심야/낮"].clip(0.05, 20)), gg_ok),
    ]
    for ax, (_, title, cmap, val, src) in zip(axes, specs):
        sc = ax.scatter(src["gx"], src["gy"], c=val, cmap=cmap, s=6 if c == "춘천" else 18, marker="s", linewidths=0)
        ax.set_title(title)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(sc, ax=ax, shrink=0.6)
        if c == "강남":
            for adm, r in cent[cent.index.str.startswith("11230")].iterrows():
                if adm in names.index:
                    ax.text(r["X_COORD"], r["Y_COORD"], names.loc[adm, "adm_nm"], fontsize=6, ha="center", color="#1a202c")
    fig.suptitle(f"{c} 250m 격자")
    save(fig, FIG / f"f03_map_{c}.png")
    fig_link(f"f03_map_{c}.png", f"{c} 지도")

# ------------------------------------------------------------ 4. 주거형 vs 낮 활동형
h("4. 주거형 vs 낮 활동형 격자")
p("심야/낮 비율로 격자를 셋으로 나눴습니다(도시마다 상위 1/3 = 주거형, 하위 1/3 = 낮 활동형). 강남의 낮 활동형은 대부분 업무·상업지이고, 춘천의 낮 활동형은 농경지·관광지·도로처럼 낮에만 사람이 있는 곳입니다.")
p()
rows = []
for c in ["강남", "춘천"]:
    gg = g.loc[c]
    gg = gg[gg["total"] >= 20].copy()
    q = gg["심야/낮"].quantile([1 / 3, 2 / 3])
    gg["형"] = np.where(gg["심야/낮"] >= q.iloc[1], "주거형", np.where(gg["심야/낮"] <= q.iloc[0], "낮 활동형", "혼합"))
    for t in ["주거형", "혼합", "낮 활동형"]:
        s = gg[gg["형"] == t]
        rows.append((c, t, len(s), s["total"].sum() / gg["total"].sum(), s["s60"].sum() / s["total"].sum(), s["s2030"].sum() / s["total"].sum()))
    corr = gg["심야/낮"].corr(gg["60대+"], method="spearman")
    rows.append((c, "상관(심야/낮 ↔ 60대+)", "", "", corr, ""))
p("| 도시 | 형 | 격자 수 | 체류량 비중 | 60대+ 비중 | 20·30대 비중 |")
p("|---|---|---:|---:|---:|---:|")
for c, t, n, share, a60, a2030 in rows:
    if t.startswith("상관"):
        p(f"| {c} | {t} | | | ρ = {a60:.2f} | |")
    else:
        p(f"| {c} | {t} | {n:,} | {share:.0%} | {a60:.1%} | {a2030:.1%} |")
p()
p("- 강남은 주거형 격자에 60대+ 비중이 높고 낮 활동형(업무·상업지)에 낮습니다. **강남에서 고령 관련 지표는 주거형 격자 위주로 보는 것이 출퇴근 인구를 덜 섞는 방법**입니다.")
p("- 춘천은 이 구분이 고령 비중과 잘 연결되지 않습니다. 춘천은 동지역과 읍·면을 먼저 나눠서 봅니다.")

# ------------------------------------------------------------ 5. 하루 리듬
h("5. 하루 리듬")
tim_t = tim.merge(names[["adm_type"]], left_on="adm_cd", right_index=True, how="left", suffixes=("", "_n"))
tim_t["grp"] = tim_t["city"] + " " + tim_t["adm_type"]
prof24 = tim_t.groupby(["grp", "STD_YM"])[TIME_COLS].sum()
fig, axes = plt.subplots(1, 4, figsize=(18, 4), sharey=True)
for ax, grp in zip(axes, ["강남 동", "춘천 동", "춘천 읍", "춘천 면"]):
    for ym, color in [("202507", "#dd6b20"), ("202512", "#3182ce")]:
        v = prof24.loc[(grp, ym)]
        ax.plot(range(24), v / v.sum() * 100, label=f"{ym[4:]}월", color=color)
    ax.set_title(grp)
    ax.set_xlabel("시")
axes[0].set_ylabel("시간대 비중 (%)")
axes[0].legend()
fig.suptitle("하루 리듬: 7월 vs 12월")
save(fig, FIG / "f04_daily_rhythm.png")
fig_link("f04_daily_rhythm.png", "하루 리듬")
p("- 강남은 낮 시간이 두꺼운 업무지구 모양, 춘천 읍·면은 아침이 이르고 저녁이 일찍 끝납니다.")
p("- 12월에는 모든 지역에서 아침이 늦어지고 저녁이 짧아집니다. 이런 변화는 계절 효과라서 '활동이 줄었다'는 신호로 쓰지 않습니다.")


def name_clusters(city, m):
    """지표 값으로 군집 이름을 정한다(k=3 가정, 실행마다 순서가 바뀌어도 같은 이름)."""
    names = {}
    young = m["20·30대"].idxmax()
    names[young] = "업무·청년형" if city == "강남" else "대학가 청년형"
    rest = [i for i in m.index if i != young]
    if city == "강남":
        hi, lo = sorted(rest, key=lambda i: m.loc[i, "주말/평일"], reverse=True)
        names[hi] = "고령 주거형 (주말 활발)"
        names[lo] = "고령 주거형 (주말 한산)"
    else:
        old, mid = sorted(rest, key=lambda i: m.loc[i, "60대+"], reverse=True)
        names[old] = "읍·면 농촌형"
        names[mid] = "도심 주거형"
    return [names.get(i, f"유형 {i + 1}") for i in m.index]


# ------------------------------------------------------------ 6. 동네 유형
h("6. 동네 유형 (군집)")
FEATS = ["60대+", "20·30대", "심야/낮", "아침(6~9시)", "활동시간폭", "주말/평일"]
p(f"도시마다 행정동을 {', '.join(FEATS)} 기준으로 3개로 묶었습니다(KMeans, 지표는 도시 안에서 표준화). 동이 22·25개뿐이라 **설명용 라벨**로만 쓰고 판단 규칙에는 넣지 않습니다.")
cl_rows = []
for c in ["강남", "춘천"]:
    t = prof[prof["city"] == c].copy()
    X = StandardScaler().fit_transform(np.column_stack([t[f] if f != "심야/낮" else np.log(t[f]) for f in FEATS]))
    best = 3  # 동이 적어 3개로 고정(설명용)
    lab = KMeans(best, n_init=20, random_state=0).fit_predict(X)
    sil = silhouette_score(X, lab)
    t["군집"] = lab
    p(f"\n### {c} (k={best}, 실루엣 {sil:.2f})\n")
    m = t.groupby("군집")[FEATS].mean()
    m["동"] = t.groupby("군집")["동"].apply(lambda s: ", ".join(s))
    m.index = name_clusters(c, m)
    m.index.name = "군집"
    prof.loc[t.index, "군집"] = [m.index[i] for i in lab]
    p(table(m, {**FMT, "동": "{}"}))
prof[["city", "동", "유형", "군집"] + COLS].to_csv(OUT / "행정동_프로필.csv", encoding="utf-8-sig")
p()
p("- 강남은 고령 주거형 동(개포·일원·수서·세곡·도곡2·대치1)과 업무·청년형 동(역삼·논현·삼성·신사·청담 등)으로 크게 나뉩니다.")
p("- 춘천은 읍·면 농촌형, 대학가 청년형(교동·효자2·효자3), 나머지 도심 주거형으로 나뉩니다.")

# ------------------------------------------------------------ 7. 월별 변화
h("7. 7월 → 12월 변화 (행정동 단위)")
grp = prof.groupby(["city", "유형"])
chg = a_d.groupby(["city", "adm_cd", "STD_YM"])[["total", "s60"]].sum().reset_index()
chg["유형"] = chg["adm_cd"].map(names["adm_type"])
mon = chg.groupby(["city", "유형", "STD_YM"])[["total", "s60"]].sum()
fig, ax = plt.subplots(figsize=(7, 4))
for (c, t), color in [(("강남", "동"), "#2b6cb0"), (("춘천", "동"), "#dd6b20"), (("춘천", "읍"), "#38a169"), (("춘천", "면"), "#805ad5")]:
    v = mon.loc[(c, t), "total"]
    ax.plot([m[4:] + "월" for m in v.index], v / v.iloc[0] * 100, marker="o", label=f"{c} {t}", color=color)
ax.set_ylabel("7월 = 100")
ax.set_title("월별 체류량: 춘천 읍·면은 겨울에 크게 줄어든다")
ax.legend()
save(fig, FIG / "f05_monthly.png")
fig_link("f05_monthly.png", "월별 변화")
p("| 지역 | 체류량 7→12월 | 60대+ 체류량 7→12월 |")
p("|---|---:|---:|")
for c, t in [("강남", "동"), ("춘천", "동"), ("춘천", "읍"), ("춘천", "면")]:
    v = mon.loc[(c, t)]
    p(f"| {c} {t} | {v['total'].iloc[-1] / v['total'].iloc[0] - 1:+.1%} | {v['s60'].iloc[-1] / v['s60'].iloc[0] - 1:+.1%} |")
cc = prof[prof["city"] == "춘천"]
p()
p(f"- 춘천 행정동에서 60대+ 비중과 7→12월 변화의 순위상관: ρ = {cc['60대+'].corr(cc['7→12월'], method='spearman'):.2f} (고령 비중이 높은 동일수록 겨울에 더 많이 줄어듦)")
p("- 다만 읍·면의 감소에는 여름 관광·농사철 방문객이 빠진 효과가 섞여 있습니다. 주민 활동이 얼마나 줄었는지는 EDA-5에서 따로 나눠 봅니다.")

# ------------------------------------------------------------ 정리
h("정리")
p("| 발견 | 설계에 주는 의미 |")
p("|---|---|")
p("| 두 도시의 성·연령 구성이 크게 다름 | 지표는 도시 안에서 순위·표준화 |")
p("| 강남은 주거형 격자에 고령 비중이 높음 | 강남 고령 지표는 주거형 격자 위주 |")
p("| 춘천은 주거형 구분이 고령과 무관, 읍·면 구분이 중요 | 춘천은 동/읍·면 먼저 분리 |")
p("| 12월 하루 리듬 변화는 모든 지역 공통 | 리듬 변화는 신호가 아니라 계절 효과 |")
p("| 춘천 읍·면은 겨울에 체류량이 크게 줄어듦 | 계절 공동화 후보. 방문객 효과 분리 필요 |")
p("| 고령 비중 자체는 위험 신호가 아님(탐색 EDA 결론) | 이 리포트의 60대+ 비중은 '구성' 설명용 |")

(OUT / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print("done")
from bigcontest.gallery import build  # noqa: E402

build()
