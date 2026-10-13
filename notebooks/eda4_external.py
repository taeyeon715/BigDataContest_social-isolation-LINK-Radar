"""EDA-4 외부 데이터 결합

실행: uv run python notebooks/eda4_external.py   (EDA-2를 먼저 실행해야 함)
결과: reports/eda4_외부데이터결합/ (README.md, figs/, 행정동_결합.csv)
"""

import numpy as np
import pandas as pd

from bigcontest.config import ROOT
from bigcontest.external import (
    STATE_LABEL, haversine_km, map_codes, resident_households, resident_population, resources, seoul_living,
    seoul_living_quality, std_rate,
)
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report
from bigcontest.stats import fmt_p, fmt_report, partial_spearman, spearman_report
from bigcontest.telecom import adm_names

r = Report("eda4_외부데이터결합", "EDA-4 외부 데이터 결합")
r.p("주민등록, 서울 시민생활 데이터, 복지자원 원장을 행정동 단위로 붙였습니다. 원본과 출처는 [external/README.md](../../external/README.md)에 있습니다.")
r.p()
r.p("**자료의 역할**(같은 자료를 입력과 검증에 동시에 쓰지 않습니다)")
r.p()
r.p("| 자료 | 역할 |")
r.p("|---|---|")
r.p("| 주민등록 1인세대 비율 | 입력 (구조 취약성) |")
r.p("| 서울 시민생활 데이터(강남) | 탐색적 검증 참고 |")
r.p("| 복지자원 원장 | 서비스 공백 |")

names = adm_names().set_index("adm_cd")
names["lat"] = names["lat"].astype(float)
names["lon"] = names["lon"].astype(float)
tel = pd.read_csv(ROOT / "reports" / "eda2_통신기초" / "행정동_프로필.csv", dtype={"adm_cd": str}).set_index("adm_cd")

# ------------------------------------------------------------ 1. 주민등록
r.h("1. 주민등록: 행정동 구조")
pop = resident_population()
hh = resident_households()
pp = pop.groupby(["adm_cd", "month"]).apply(
    lambda g: pd.Series({
        "총인구": g["pop"].sum(),
        "60대+": g.loc[g["age"] >= 60, "pop"].sum() / g["pop"].sum(),
        "65세+": g.loc[g["age"] >= 65, "pop"].sum() / g["pop"].sum(),
        "중장년 남성(40~64)": g.loc[(g["age"].between(40, 64)) & (g["sex"] == "남"), "pop"].sum() / g["pop"].sum(),
    }),
    include_groups=False,
).groupby("adm_cd").mean()
pp["1인세대 비율"] = hh.groupby("adm_cd")["one_person_rate"].mean()
pp["city"] = names["city"]
pp["동"] = names["adm_nm"]
pp["유형"] = names["adm_type"]
r.p("2025년 7~12월 월말 값의 평균입니다.")
for c in ["강남", "춘천"]:
    t = pp[pp["city"] == c].sort_values("1인세대 비율", ascending=False).set_index("동")
    r.h3(f"{c} (1인세대 비율 순, 상위 8개)")
    r.table(t[["유형", "총인구", "1인세대 비율", "65세+", "중장년 남성(40~64)"]].head(8),
            {"총인구": "{:,.0f}", "1인세대 비율": "{:.0%}", "65세+": "{:.1%}", "중장년 남성(40~64)": "{:.1%}"})
r.p("- 강남은 역삼·논현처럼 1인세대가 70% 안팎인 동이 있습니다. 춘천은 대학가(교동·효자)와 구도심의 1인세대 비율이 높습니다.")
r.p("- 주민등록 '1인세대'는 실제 '1인가구'와 다릅니다(주소만 따로 둔 경우 등 포함).")

# ------------------------------------------------------------ 2. 통신 vs 주민
r.h("2. 통신 유동인구는 주민 구성을 얼마나 반영하나")
j = pp.join(tel[["60대+", "20·30대", "심야/낮"]].add_prefix("통신 "))
fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
for ax, c in zip(axes, ["강남", "춘천"]):
    s = j[j["city"] == c]
    ax.scatter(s["60대+"] * 100, s["통신 60대+"] * 100, color=CITY_COLOR[c])
    for adm, row in s.iterrows():
        ax.annotate(row["동"], (row["60대+"] * 100, row["통신 60대+"] * 100), fontsize=7, alpha=0.8)
    rep = spearman_report(s["60대+"], s["통신 60대+"])
    ax.set_title(f"{c}: ρ = {rep['rho']:.2f}")
    ax.set_xlabel("주민등록 60대+ 비중 (%)")
    ax.set_ylabel("통신 60대+ 체류 비중 (%)")
    r.p(f"- {c}: 주민등록 60대+ 비중 ↔ 통신 60대+ 비중 {fmt_report(rep)}")
r.fig(fig, "f01_resident_vs_telecom.png", "통신 vs 주민")
r.p("- 통신의 고령 비중은 주민 구성을 **순위 수준에서는** 꽤 반영합니다. 다만 값의 폭은 좁아집니다(출퇴근·방문 인구가 섞이기 때문).")
r.p("- 강남에서 크게 벗어나는 동(주민은 고령인데 유동은 젊은 동)은 업무·상업 유입이 많은 곳입니다. 이런 동은 '통신 신호 품질 확인' 표시로만 씁니다.")

# ------------------------------------------------------------ 3. 시민생활 (강남 검증 기준)
r.h("3. 서울 시민생활 데이터 (강남, 탐색적 검증 참고)")
r.p("시민생활 데이터의 `외출-커뮤니케이션이 모두 적은 집단(전체)`은 **전체 인구** 중 외출과 소통이 모두 적은 사람 수입니다. 그래서 총인구로 나눠 비율을 냅니다. 동마다 연령 구성이 달라서, 강남 전체 60대+ 성·연령 구성으로 맞춘 **표준화 비율**도 함께 봅니다.")
sl = seoul_living()
sl_raw0 = seoul_living(quality_na=False)  # 품질 의심 칸도 원본 0 그대로 둔 판 (n=22 비교용)

r.h3("품질 점검: 동별·지표별 0값 비율")
q = seoul_living_quality()
sus = q[q["품질 의심"]]
r.p("동마다 지표마다 0값 비율을 자동으로 셉니다. 그 동에서 6개월 모든 행이 0인데 강남 전체에서는 0이 드문(20% 미만) 지표를 **품질 의심**으로 표시합니다.")
if len(sus):
    t_ = sus.groupby(["adm_cd7", "원본 동명"]).agg(지표_수=("지표", "size"), 지표=("지표", lambda s: ", ".join(s))).reset_index()
    t_["adm_cd"] = map_codes(t_["adm_cd7"], "seoul_living_adm7")
    t_["현재 동명"] = t_["adm_cd"].map(names["adm_nm"])
    r.table(t_.set_index("현재 동명")[["원본 동명", "지표_수", "지표"]], {"원본 동명": "{}", "지표_수": "{}", "지표": "{}"})
    r.p("- 원본 파일은 개포3동(코드 1123074)을 옛 이름 일원2동으로 적습니다(2022-12-23 이름만 바뀜, `ref/adm_code_map.csv`).")
    r.p("- 0이 된 원인은 확인하지 못했습니다(공개 원본 XLSX도 같은 0). 그래서 **그 지표만** 결측(품질 의심)으로 두고, 다른 지표는 22개 동을 그대로 씁니다. 원본 0, 비공개(`*`), 결측, 품질 의심은 다른 상태로 보존합니다.")
states_q = pd.concat({c: sl[f"{c}_state"].value_counts() for c in ["iso_all", "out_low_all", "comm_low_single"]}, axis=1).fillna(0).astype(int)
st = states_q.rename(index=STATE_LABEL)
st.index.name = "값 상태 (행 수)"
r.table(st, {c: "{:,}" for c in st.columns})
r.p("- 지금 강남 원본에는 비공개(`*`)와 결측이 없습니다. 0은 원본 0입니다.")


def iso_rate(df, lo, hi, standardized=True, col="iso_all"):
    """성·연령 직접 표준화 비율. 가중치 커버리지가 100%가 아니면 std_rate가 경고한다."""
    sub = df[df["age"].between(lo, hi)]
    if not standardized:
        return sub.groupby("adm_cd")[col].sum() / sub.groupby("adm_cd")["pop"].sum()
    out = std_rate(sub, col, "pop")
    coverage[(col, lo, hi)] = out["coverage"].min()
    return out["rate"]


coverage = {}
iso = pd.DataFrame({
    "고령(60+) 고립률": iso_rate(sl, 60, 99),
    "중장년(40~59) 고립률": iso_rate(sl, 40, 59),
    "청년(20~39) 고립률": iso_rate(sl, 20, 39),
})
g = pp[pp["city"] == "강남"].join(iso).join(tel[["20·30대", "60대+"]].add_prefix("통신 "))
t = g.sort_values("고령(60+) 고립률", ascending=False).set_index("동")
r.table(t[["1인세대 비율", "고령(60+) 고립률", "중장년(40~59) 고립률", "청년(20~39) 고립률"]].head(10),
        {"1인세대 비율": "{:.0%}", "고령(60+) 고립률": "{:.2%}", "중장년(40~59) 고립률": "{:.2%}", "청년(20~39) 고립률": "{:.2%}"})
r.p("(고령 고립률 상위 10개 동, 성·연령 표준화)")
r.p(f"- 표준화 가중치 커버리지(동마다 실제 값이 있는 성·연령 칸의 가중치 합) 최솟값: {min(coverage.values()):.0%}. 100%가 아니면 코드가 경고합니다.")

# ------------------------------------------------------------ 4. 구조 취약성 검증
r.h("4. 구조 취약성 검증: 1인세대 비율 ↔ 고령 고립률 (강남 22개 동)")
rows = []
for col, label in [("1인세대 비율", "주민등록 1인세대 비율"), ("통신 20·30대", "통신 20·30대 비중"), ("통신 60대+", "통신 60대+ 비중"), ("65세+", "주민등록 65세+ 비중")]:
    rep = spearman_report(g[col], g["고령(60+) 고립률"])
    rows.append((label, rep))
    r.p(f"- {label} ↔ 고령 고립률: {fmt_report(rep)}")
r.p()
pc1 = partial_spearman(g["통신 20·30대"], g["고령(60+) 고립률"], g["1인세대 비율"])
pc2 = partial_spearman(g["1인세대 비율"], g["고령(60+) 고립률"], g["통신 20·30대"])
r.p(f"- 서로 통제한 편상관: 1인세대 비율 {pc2:.2f}, 통신 20·30대 비중 {pc1:.2f}")
r.p()
r.p("**맥락 효과인가, 구성 효과인가?** 1인세대가 많은 동에서 고령 고립률이 높은 이유는 두 가지일 수 있습니다.")
r.p("- 구성 효과: 그 동에 혼자 사는 고령자가 많아서")
r.p("- 맥락 효과: 혼자 사는지와 상관없이 1인세대가 많은 동네 분위기 자체 때문에")
old60 = sl[sl["age"] >= 60].groupby("adm_cd")[["single", "pop"]].sum()
g["고령 1인가구율"] = old60["single"] / old60["pop"]  # 분자·분모 모두 시민생활, 60세 이상 전체 인구 기준
pc_ctx = partial_spearman(g["1인세대 비율"], g["고령(60+) 고립률"], g["고령 1인가구율"])
# 분자·분모 모두 60세 이상 1인가구(시민생활). 1인가구의 성·연령 구성으로 표준화. 개포3동은 이 지표가 품질 의심이라 빠짐
g["고령 1인가구 소통 적음"] = std_rate(sl[sl["age"] >= 60], "comm_low_single", "single", warn=False)["rate"]
rep_comp = spearman_report(g["1인세대 비율"], g["고령 1인가구 소통 적음"])
rep_comp22 = spearman_report(g["1인세대 비율"], std_rate(sl_raw0[sl_raw0["age"] >= 60], "comm_low_single", "single")["rate"])
r.p(f"- 고령 본인의 1인가구율(시민생활 1인가구 추정 ÷ 시민생활 60세 이상 총인구)을 통제한 편상관: {pc_ctx:.2f}")
r.p("- 예전 표의 '고립 인원 ÷ 고령 1인가구 수'는 **지웠습니다.** 분자(`(전체)` 집단)는 전체 인구 기준이고 분모는 1인가구라서 서로 다른 집단을 나눈 값이었습니다.")
r.p(f"- 대신 분자·분모가 같은 집단인 지표로 봅니다: 60세 이상 **1인가구 중** '커뮤니케이션이 적은 집단' 비율(1인가구 성·연령 표준화) ↔ 1인세대 비율 {fmt_report(rep_comp)}. 개포3동은 이 지표가 품질 의심이라 빠집니다(n=21). 원본 0을 그대로 넣으면(n=22) ρ = {rep_comp22['rho']:.2f}({fmt_p(rep_comp22['p_perm'])}).")
same_dir = rep_comp["p_perm"] < 0.05 and rep_comp["rho"] > 0
r.p(f"  - 혼자 사는 고령자끼리 비교해도 1인세대가 많은 동에서 소통이 적은 비율이 {'높습니다 → 맥락 효과 쪽 근거' if same_dir else '뚜렷하게 높지 않습니다 → 맥락 효과의 근거는 없음'}.")
r.p("  - 다만 이 지표는 '외출·소통 모두 적음'이 아니라 '소통 적음'이고 정의(1인가구 중 하위 10%)도 달라서 **보조 근거**입니다.")
r.p("- 그래서 '1인세대가 많은 동에서 고령층 고립률이 높다'까지만 주장하고, 그 이유(구성인지 맥락인지)는 단정하지 않습니다.")
r.p()
fig, ax = plt.subplots(figsize=(6.5, 5))
ax.scatter(g["1인세대 비율"] * 100, g["고령(60+) 고립률"] * 100, color=CITY_COLOR["강남"])
for adm, row in g.iterrows():
    ax.annotate(row["동"], (row["1인세대 비율"] * 100, row["고령(60+) 고립률"] * 100), fontsize=7)
ax.set_xlabel("주민등록 1인세대 비율 (%)")
ax.set_ylabel("60대+ 외출·소통 모두 적은 비율 (%, 표준화)")
ax.set_title(f"강남: 1인세대가 많은 동일수록 고령층 고립률이 높다 (ρ = {rows[0][1]['rho']:.2f})")
r.fig(fig, "f02_single_vs_isolation.png", "1인세대 vs 고립률")
r.p("**해석**")
r.p("- 1인세대가 많은 동일수록 고령층의 외출·소통 결핍 비율이 높습니다. 동 하나를 빼도 관계가 유지됩니다.")
r.p("- 통신 20·30대 비중도 관련이 있어 보이지만, 1인세대 비율을 통제하면 사라집니다. 즉 청년 비중은 1인세대 밀집을 대신 보여줄 뿐입니다.")
r.p("- 고령 비중 자체(주민등록·통신 모두)는 고립률과 뚜렷한 관계가 없습니다. **'고령자가 많은 동 = 위험'이 아닙니다.**")
r.p("- 한계: 강남 22개 동뿐이고, 시민생활 데이터도 SKT 추정치라 완전히 독립된 출처는 아닙니다. 춘천에는 같은 자료가 없습니다.")

# ------------------------------------------------------------ 5. 서비스 공백
r.h("5. 서비스 공백: 거점 자원까지의 거리")
res = resources()
res = res.dropna(subset=["lat", "lon"])
res["lat"] = res["lat"].astype(float)
res["lon"] = res["lon"].astype(float)
hubs = res[res["hub"].astype(str) == "True"]
r.p(f"복지자원 원장 {len(res):,}건 중 종합사회복지관·정신건강복지센터 같은 **거점 기관** {len(hubs)}곳(강남 {sum(hubs['city'] == '강남')}, 춘천 {sum(hubs['city'] == '춘천')})까지의 거리를 행정동 중심점에서 쟀습니다(직선거리).")
r.p()
gap = {}
for adm, row in names.iterrows():
    h = hubs[hubs["city"] == row["city"]]
    dist = haversine_km(row["lat"], row["lon"], h["lat"].to_numpy(), h["lon"].to_numpy())
    mh = h[(h["type"] == "상담/정신건강") & ~h["name"].str.contains("청소년", na=False)]  # 성인 대상만
    dist_m = haversine_km(row["lat"], row["lon"], mh["lat"].to_numpy(), mh["lon"].to_numpy())
    n_all = (res["adm_cd"] == adm).sum()
    gap[adm] = {"가장 가까운 거점(km)": dist.min(), "가장 가까운 성인 상담·정신건강 거점(km)": dist_m.min(), "동 안 자원 수": n_all}
gap = pd.DataFrame(gap).T
pp = pp.join(gap)
for c in ["강남", "춘천"]:
    t = pp[pp["city"] == c].sort_values("가장 가까운 거점(km)", ascending=False).set_index("동")
    r.h3(f"{c}: 거점이 먼 동 상위 8개")
    r.table(t[["유형", "1인세대 비율", "가장 가까운 거점(km)", "가장 가까운 성인 상담·정신건강 거점(km)", "동 안 자원 수"]].head(8),
            {"1인세대 비율": "{:.0%}", "가장 가까운 거점(km)": "{:.1f}", "가장 가까운 성인 상담·정신건강 거점(km)": "{:.1f}", "동 안 자원 수": "{:.0f}"})
gn = pp[pp["city"] == "강남"].copy()
nonk = res[(res["city"] == "강남") & ~res["subtype"].fillna("").str.contains("경로당")]
gn["자원/1인세대 1천"] = nonk.groupby("adm_cd").size().reindex(gn.index, fill_value=0) / hh.groupby("adm_cd")["1인세대"].mean().reindex(gn.index) * 1000
top_single = set(gn.nlargest(6, "1인세대 비율").index)
top_gap = set(gn.nlargest(6, "가장 가까운 거점(km)").index)
both = [names.loc[a, "adm_nm"] for a in top_single & top_gap]
rep_d = spearman_report(gn["1인세대 비율"], gn["가장 가까운 거점(km)"])
rep_p = spearman_report(gn["1인세대 비율"], gn["자원/1인세대 1천"])
r.h3("강남: 1인세대 밀집 동은 자원이 부족한가")
r.p(f"- 거점까지 거리: 1인세대 비율과 {fmt_report(rep_d)}. 1인세대 상위 6개 동과 거리 상위 6개 동의 겹침: {', '.join(both) if both else '없음'}")
r.p(f"- 1인세대 1,000곳당 자원 수(경로당 제외): 1인세대 비율과 {fmt_report(rep_p)}")
t = gn.sort_values("자원/1인세대 1천").set_index("동")[["1인세대 비율", "자원/1인세대 1천", "가장 가까운 거점(km)"]].head(6)
r.table(t, {"1인세대 비율": "{:.0%}", "자원/1인세대 1천": "{:.1f}", "가장 가까운 거점(km)": "{:.1f}"})
r.p("- **강남은 동 사이 거리 차이가 작아(모두 2km 이내) 거리로는 공백이 드러나지 않습니다.**")
r.p("- 1인세대 대비 자원 수로 보면 1인세대가 많은 역삼1·논현1도 하위권에 있지만, 1인세대가 많아 분모가 커서 생기는 효과가 섞여 있고 상관도 유의하지 않습니다.")
r.p("- 결론: 강남에서 '1인세대 밀집 동 = 서비스 공백'은 **아직 근거가 약합니다.** 자원의 정원·운영시간·대상 자격(예: 중장년 1인가구 대상 프로그램이 있는지)을 반영해야 판단할 수 있습니다.")
r.h3("춘천")
r.p("- 춘천 면 지역은 가장 가까운 거점까지 5~19km로, 동지역(대부분 2km 이내)과 차이가 뚜렷합니다. **관측된 거점까지 직선거리가 큽니다.** 실제 서비스 공급이 부족한지는 정원·이동 수단·방문 서비스를 봐야 알 수 있습니다.")
r.p("- 두 도시의 자원 목록은 구성이 달라(강남은 경로당 목록 포함, 춘천은 행정복지센터 포함) **도시끼리 직접 비교하지 않습니다.**")

fig, axes = plt.subplots(1, 2, figsize=(12, 5))
for ax, c in zip(axes, ["강남", "춘천"]):
    s = pp[pp["city"] == c]
    ax.scatter(s["1인세대 비율"] * 100, s["가장 가까운 거점(km)"], color=CITY_COLOR[c])
    for adm, row in s.iterrows():
        ax.annotate(row["동"], (row["1인세대 비율"] * 100, row["가장 가까운 거점(km)"]), fontsize=7)
    ax.set_xlabel("1인세대 비율 (%)")
    ax.set_ylabel("가장 가까운 거점까지 (km)")
    ax.set_title(f"{c}: 구조 취약성 × 서비스 공백")
r.fig(fig, "f03_single_vs_gap.png", "1인세대 vs 거점 거리")

# ------------------------------------------------------------ 6. 춘천 보조 자료
r.h("6. 춘천 보조 자료 점검")
r.p("**춘천 독거노인 현황**: 일부 읍·면에서 독거노인 수가 그 동의 1인세대 수보다 많아 정의가 불분명합니다. 순위로만 참고합니다.")
r.p()
r.p("**2025 춘천시 사회조사**(2025.8.13~9.2, 1,100가구, 49개 항목, 2026-04-30 공표): 보고서 PDF 256쪽을 확인했습니다.")
r.p("- 사회관계망(도움받을 사람), 외로움, 사회적 교류를 직접 묻는 문항은 **없습니다.**")
r.p("- '노인이 겪는 가장 큰 어려움' 문항에 '외로움·소외감' 보기가 있습니다(전체 14.8%). 4개 권역별 값: 구도심 10.8%, 신도심 16.6%, 도농복합 12.2%, 기타 읍면 14.1%.")
r.p("- 노인 본인의 경험이 아니라 노인이 겪는 어려움에 대한 인식이고 권역 단위라서 **검증 기준으로는 쓰지 않고 배경 설명으로만** 씁니다.")

# ------------------------------------------------------------ 정리
r.h("정리")
r.p("해석 상태: 직접 확인(원본·문서에서 바로 확인) / 계산 재현(스크립트로 다시 계산됨) / 가설(가능한 설명) / 미확인")
r.p()
r.p("| 발견 | 설계에 주는 의미 | 해석 상태 |")
r.p("|---|---|---|")
r.p(f"| 1인세대 비율 ↔ 강남 고령 고립률이 강하게 연결됨 (ρ={rows[0][1]['rho']:.2f}, n={rows[0][1]['n']}) | 구조 취약성의 핵심 입력으로 1인세대 비율 사용 | 계산 재현 |")
r.p("| 그 이유가 구성 효과인지 맥락 효과인지 | 단정하지 않음 | 미확인 |")
r.p("| 청년 비중 효과는 1인세대로 설명됨 | 통신 청년 비중은 '동 안 어디'를 보는 지도 레이어로만 | 계산 재현 |")
r.p("| 고령 비중 자체는 고립률과 뚜렷한 관계 없음 | '고령자 많은 동 = 위험' 가정 금지 | 계산 재현 |")
r.p("| 통신 고령 비중은 주민 구성을 순위 수준에서 반영 | 통신은 보조, 주민등록이 기준 | 계산 재현 |")
r.p(f"| 개포3동 시민생활 7개 지표가 6개월 내내 0 | 그 지표만 품질 의심 결측(n=21), 원인 미확인 | 직접 확인 |")
r.p("| 춘천 면 지역은 관측된 거점까지 직선거리 5~19km | 접근성 맥락으로만. 실제 공급 부족과는 다른 주장 | 계산 재현 |")
r.p("| 강남 1인세대 밀집 동의 자원 부족 | 정원·대상 자격 반영 후 재판단 | 미확인 |")
r.p("| 춘천 사회조사에는 직접 문항 없음 | 춘천 검증은 한계로 명시 | 직접 확인 |")

pp.join(iso).to_csv(r.dir / "행정동_결합.csv", encoding="utf-8-sig")
r.save()
print("done")
