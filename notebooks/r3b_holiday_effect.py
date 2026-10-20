"""R-3b 공휴일 효과 1차 분석 (reports/r3_규칙개정/변경계획.md 9절에 결과 전에 고정).

연휴에 어느 성·연령의 결제가 평소보다, 그리고 40·50대보다 더 줄었는지 본다. 추석 한 번이라 탐색 결과다.
- 지표: log(기간 결제 / 같은 요일 평소 값). 상대 차이 = 그 값 − 같은 도시·성 40·50대 평균.
- 우연과 구분: 공휴일 아닌 같은 길이 연속 구간으로 만든 가짜 연휴 분포의 하위 5%.
- 동으로 내리기: 연휴 주의 코호트(60대+ 묶음)의 470칸 중 v2.0 근거 축이 선 칸.
결과: reports/r3_규칙개정/공휴일_*.csv
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bigcontest.card import AGE_ORDER, daily, load_card2
from bigcontest.config import ROOT, load_config

OUT = ROOT / "reports" / "r3_규칙개정"
HOL = pd.DatetimeIndex(sorted(pd.Timestamp(k) for k in load_config()["holidays"]))
GAP = pd.date_range("2025-09-29", "2025-10-19")  # 추석 앞뒤: 평소 값·가짜 연휴에서 뺌
BASE = pd.date_range("2025-09-01", "2025-09-28").append(pd.date_range("2025-10-20", "2025-11-09"))
BASE = BASE[~BASE.isin(HOL)]
PLACEBO_SPAN = pd.date_range("2025-07-01", "2025-11-30")
PERIODS = {"추석 연휴": ("2025-10-03", "2025-10-09"), "연휴 뒤": ("2025-10-10", "2025-10-19")}
SINGLE = ["2025-08-15", "2025-12-25"]
REF = ["40대", "50대"]
CLINIC = ["일반병원", "치과병원", "한의원", "기타의료", "약국"]
P_CUT, MIN_GAP = 0.05, -0.05


def load_daily() -> pd.DataFrame:
    """날짜 × 도시 × 성 × 연령(7) × 업종 결제건수. 업종 = 의료·생필품·전체."""
    c = load_card2()
    c["sex"] = c["SEX_CCD"].str[0]
    parts = []
    for name, m in {"의료": c["MCT_RY_CD"].isin(CLINIC), "생필품": c["cat"] == "생필품", "전체": slice(None)}.items():
        d = daily(c[m], ["city", "sex", "age_g"])
        parts.append(d.assign(grp=name))
    d = pd.concat(parts)
    d60 = d[d["age_g"].isin(["60대", "70대", "80대+"])].groupby(["date", "city", "sex", "grp"], as_index=False)["USE_CNT"].sum()
    d = pd.concat([d, d60.assign(age_g="60대+")])
    return d.pivot_table(index="date", columns=["city", "sex", "age_g", "grp"], values="USE_CNT").sort_index()


def log_ratio(Y: pd.DataFrame, days: pd.DatetimeIndex, base: pd.DatetimeIndex) -> pd.Series:
    """기간 합 / 같은 요일 평소 값 합의 로그. 평소 값에서 기간 날짜는 뺀다."""
    base = base[~base.isin(days)]
    exp_dow = Y.loc[base].groupby(base.dayofweek).mean()
    exp = exp_dow.loc[days.dayofweek].sum()
    return np.log(Y.loc[days].sum() / exp)


def relative(lr: pd.Series) -> pd.Series:
    """같은 도시·성·업종 40·50대 평균을 뺀 값."""
    df = lr.rename("lr").reset_index()
    ref = df[df["age_g"].isin(REF)].groupby(["city", "sex", "grp"])["lr"].mean().rename("ref")
    df = df.join(ref, on=["city", "sex", "grp"])
    return df.set_index(["city", "sex", "age_g", "grp"])["lr"] - df.set_index(["city", "sex", "age_g", "grp"])["ref"]


def placebo_windows(n: int) -> list[pd.DatetimeIndex]:
    bad = HOL.append(GAP)
    out = []
    for s in PLACEBO_SPAN:
        w = pd.date_range(s, periods=n)
        if w[-1] <= PLACEBO_SPAN[-1] and not w.isin(bad).any():
            out.append(w)
    return out


def local_base(day: pd.Timestamp) -> pd.DatetimeIndex:
    b = pd.date_range(day - pd.Timedelta(days=28), day + pd.Timedelta(days=28), freq="7D")
    b = b[(b != day) & ~b.isin(HOL) & ~b.isin(GAP)]
    return b[(b >= "2025-07-01") & (b <= "2025-12-31")]


def period_table(Y: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, (a, b) in PERIODS.items():
        days = pd.date_range(a, b)
        lr = log_ratio(Y, days, BASE)
        rel = relative(lr)
        pl = pd.concat([relative(log_ratio(Y, w, BASE)) for w in placebo_windows(len(days))], axis=1)
        p = (pl.le(rel, axis=0).sum(axis=1) + 1) / (pl.shape[1] + 1)
        rows.append(pd.DataFrame({"기간": name, "평소 대비": np.expm1(lr.reindex(rel.index)), "40·50대 대비": np.expm1(rel),
                                  "가짜 연휴 하위 5%": np.expm1(pl.quantile(0.05, axis=1)), "가짜 연휴 p": p,
                                  "가짜 연휴 수": pl.shape[1]}))
    return pd.concat(rows).reset_index()


def single_table(Y: pd.DataFrame) -> pd.DataFrame:
    ok = [d for d in PLACEBO_SPAN if d not in HOL and d not in GAP]
    pl = pd.concat([relative(log_ratio(Y, pd.DatetimeIndex([d]), local_base(d))) for d in ok], axis=1)
    rows = []
    for d in SINGLE:
        day = pd.Timestamp(d)
        lr = log_ratio(Y, pd.DatetimeIndex([day]), local_base(day))
        rel = relative(lr)
        same = pl.iloc[:, [i for i, x in enumerate(ok) if x.dayofweek == day.dayofweek]]
        p = (same.le(rel, axis=0).sum(axis=1) + 1) / (same.shape[1] + 1)
        rows.append(pd.DataFrame({"기간": f"{d} ({load_config()['holidays'][d]})", "평소 대비": np.expm1(lr.reindex(rel.index)),
                                  "40·50대 대비": np.expm1(rel), "가짜 연휴 하위 5%": np.expm1(same.quantile(0.05, axis=1)),
                                  "가짜 연휴 p": p, "가짜 연휴 수": same.shape[1]}))
    return pd.concat(rows).reset_index()


def flag(t: pd.DataFrame) -> pd.Series:
    return t["grp"].isin(["의료", "생필품"]) & (t["가짜 연휴 p"] < P_CUT) & (t["40·50대 대비"] <= MIN_GAP)


def to_cells(t: pd.DataFrame) -> pd.DataFrame:
    """연휴 주의 코호트(60대+ 묶음 포함 5연령)의 470칸 중 근거 축이 선 칸."""
    cells = pd.read_csv(OUT / "칸별_등급표.csv", dtype={"adm_cd": str})
    f = t[t["주의"] & t["age_g"].isin(["20대", "30대", "40대", "50대", "60대+"])]
    why = f.groupby(["city", "sex", "age_g"]).apply(
        lambda d: "; ".join(f"{r['기간']} {r['grp']} {r['40·50대 대비']:+.0%}" for _, r in d.iterrows()), include_groups=False)
    out = []
    for (city, sex, age), w in why.items():
        c = cells[(cells["도시"] == city) & (cells["성"] == sex) & (cells["연령"] == age) & (cells["기준일"] == "추석 D-7")]
        c = c[c["선 축"].fillna("-") != "-"].copy()
        c["연휴 근거"] = w
        out.append(c)
    if not out:
        return pd.DataFrame()
    o = pd.concat(out)
    o["축 수"] = o["선 축"].str.count(r"[SHVA]")
    cols = ["도시", "동", "동 유형", "성", "연령", "칸 인구", "선 축", "축 수", "위험 유형", "등급", "근거 신뢰도", "연휴 근거"]
    return o.sort_values(["도시", "축 수", "칸 인구"], ascending=[True, False, False])[cols]


def main():
    Y = load_daily()
    pt, st = period_table(Y), single_table(Y)
    pt["주의"], st["주의"] = flag(pt), False  # 하루짜리는 보조 확인
    st["보조: 기준 만족"] = flag(st)
    order = {a: i for i, a in enumerate(AGE_ORDER + ["60대+"])}
    for t in (pt, st):
        t.sort_values(["기간", "city", "grp", "sex", "age_g"], key=lambda s: s.map(order) if s.name == "age_g" else s, inplace=True)
    pt.round(4).to_csv(OUT / "공휴일_추석_코호트별.csv", index=False, encoding="utf-8-sig")
    st.round(4).to_csv(OUT / "공휴일_하루_코호트별.csv", index=False, encoding="utf-8-sig")
    cells = to_cells(pt)
    cells.to_csv(OUT / "공휴일_먼저볼_칸.csv", index=False, encoding="utf-8-sig")
    tests = pt[pt["grp"].isin(["의료", "생필품"]) & pt["age_g"].isin(AGE_ORDER) & ~pt["age_g"].isin(REF)]
    print(f"검정 수(7연령, 40·50대 제외) {len(tests)}, 주의 {int(flag(tests).sum())}, 우연 기대 약 {len(tests) * P_CUT:.1f}")
    show = pt[pt["주의"]][["기간", "city", "sex", "age_g", "grp", "평소 대비", "40·50대 대비", "가짜 연휴 p"]]
    print(show.round(3).to_string(index=False))
    print(st[st["보조: 기준 만족"]][["기간", "city", "sex", "age_g", "grp", "40·50대 대비", "가짜 연휴 p"]].round(3).to_string(index=False))
    print(cells.groupby(["도시", "연령", "성"]).size())


if __name__ == "__main__":
    main()
