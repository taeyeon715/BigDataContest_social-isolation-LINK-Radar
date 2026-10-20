"""T2 교차 검증: 태연 2축 위험도(H·V) ↔ LINK-Radar R2(S1·A1·등급).

계획은 reports/t2_교차검증/분석계획.md (결과 전에 고정). 원본 데이터 없이 리포트 CSV와 공개 외부 자료만 읽는다.

실행: uv run python notebooks/t2_cross_validation.py
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
from scipy.stats import rankdata, spearmanr

ROOT = Path(__file__).resolve().parents[1]
TY_CSV = ROOT / "reports/t1_2축위험도/위험도_2축_강남_춘천_동x성x연령.csv"
LR_CSV = ROOT / "reports/r2_위험도결과/동별_등급표.csv"
EXT = ROOT / "analysis/taeyeon_2axis/external"
OUT = ROOT / "reports/t2_교차검증"

CITY = {"서울 강남구": "강남", "강원 춘천시": "춘천"}
# 통합 계획 3.1 대응표: LINK-Radar 집단 → 태연 (성별, 연령5) 칸
COHORT_MAP = {
    "고령층": [("남성", "60대+"), ("여성", "60대+")],
    "중장년 남성 1인가구": [("남성", "40대"), ("남성", "50대")],
    "50~69세 남성": [("남성", "50대"), ("남성", "60대+")],
    "청년 남성": [("남성", "20대"), ("남성", "30대")],
}
COHORT_NOTE = {
    "고령층": "60~64세 섞임",
    "중장년 남성 1인가구": "60~64세 빠짐, 1인 여부 모름",
    "50~69세 남성": "70세 이상 섞임",
    "청년 남성": "35~39세 섞임, LINK-Radar는 도시 단위(동 등급은 참고)",
}
ASOF = ["추석 D-7 (09/26)", "추석 뒤 (10/13)", "동절기 (12/20)"]
FLAG_STAGES = {"심각", "경계"}
TY_BASIS = "2025.12 기준 사후 계산(기간 값)"


def level_num(s: str) -> float:
    if s == "후보 아님":
        return 0.0
    m = re.match(r"Level (\d)", str(s))
    return float(m.group(1)) if m else np.nan  # 판단 보류 등


def load_ty() -> pd.DataFrame:
    t = pd.read_csv(TY_CSV, encoding="utf-8-sig", dtype={"SGIS코드": str})
    t["도시"] = t["지역"].map(CITY)
    return t


def load_lr() -> pd.DataFrame:
    d = pd.read_csv(LR_CSV, encoding="utf-8-sig")
    d = d[d["adm_cd"].notna()].copy()  # 도시 단위(청년 남성 안내) 2행 제외
    d["SGIS코드"] = d["adm_cd"].astype("int64").astype(str)
    d["등급 수"] = d["등급"].map(level_num)
    return d


def cohort_table(ty: pd.DataFrame) -> pd.DataFrame:
    """태연 칸을 정책 집단으로 묶음 (주민 가중 평균)."""
    rows = []
    for grp, cells in COHORT_MAP.items():
        sel = ty[[(s, a) in cells for s, a in zip(ty["성별"], ty["연령5"])]]
        for (city, code), g in sel.groupby(["도시", "SGIS코드"]):
            w = g["주민"].clip(lower=1)
            flag = g["단계"].isin(FLAG_STAGES)
            rows.append({
                "도시": city, "SGIS코드": code, "동": g["읍면동명"].iloc[0], "집단": grp, "칸 수": len(g),
                "집단 주민": int(g["주민"].sum()),
                "H": np.average(g["적용 H"], weights=w), "V": np.average(g["V"], weights=w),
                "점수(참고)": np.average(g["우선순위 점수"], weights=w),
                "2축 심각·경계 칸": ", ".join(f"{s[0]}{a}({st})" for s, a, st in zip(g["성별"], g["연령5"], g["단계"]) if st in FLAG_STAGES) or "-",
                "2축 심각·경계 인구 비중": float(g.loc[flag, "주민"].sum() / max(g["주민"].sum(), 1)),
                "2축 지목": bool(flag.any()),
                "2축 유형": ", ".join(f"{s[0]}{a}:{ty_}" for s, a, ty_ in zip(g["성별"], g["연령5"], g["위험 유형"])),
            })
    return pd.DataFrame(rows)


def joined() -> pd.DataFrame:
    ty, lr = load_ty(), load_lr()
    ct = cohort_table(ty)
    wide = lr.pivot_table(index=["도시", "SGIS코드", "집단"], columns="기준일", values="등급 수", aggfunc="first")
    wide = wide.reindex(columns=ASOF)
    wide.columns = [f"등급 {c.split(' (')[0]}" for c in wide.columns]
    base = lr[lr["기준일"] == ASOF[0]].set_index(["도시", "SGIS코드", "집단"])[
        ["S1 1인세대 비율", "S1 도시 안 순위", "A1 안부·관계 거점 거리(km)", "주민등록 기준월"]]
    rid = lr.groupby(["도시", "SGIS코드", "집단"])["규칙 id"].apply(lambda s: ", ".join(sorted({x for x in s if x != "-"})) or "-")
    lrw = base.join(wide).join(rid.rename("LINK-Radar 규칙"))
    lrw["최고 등급"] = lrw[[c for c in lrw.columns if c.startswith("등급 ")]].max(axis=1)
    lrw["LINK-Radar 지목"] = lrw["최고 등급"] >= 2
    j = ct.merge(lrw.reset_index(), on=["도시", "SGIS코드", "집단"], how="outer", indicator=True)
    j["2축 기준"] = TY_BASIS
    return j


# ---------------------------------------------------------------- 1. 순위 상관
def rank_corr(j: pd.DataFrame) -> pd.DataFrame:
    targets = [("S1 1인세대 비율", "S1 1인세대 비율"), ("A1 안부·관계 거점 거리(km)", "A1 거리"),
               ("등급 추석 D-7", "등급(추석 D-7)"), ("최고 등급", "등급(세 기준일 최고)")]
    rows = []
    for (city, grp), g in j.groupby(["도시", "집단"], sort=False):
        for src, lab in [("H", "H"), ("V", "V"), ("점수(참고)", "곱셈 점수(참고)")]:
            for col, tl in targets:
                x, y = g[src], g[col]
                ok = x.notna() & y.notna()
                const = y[ok].nunique() < 2
                r, p = (np.nan, np.nan) if const else spearmanr(x[ok], y[ok])
                rows.append({"도시": city, "집단": grp, "2축 지표": lab, "LINK-Radar 지표": tl, "n": int(ok.sum()),
                             "ρ": r, "p(양측, 참고)": p, "비고": "값이 하나뿐이라 계산 안 함" if const else ""})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 2. 상위 겹침
def overlaps(j: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (city, grp), g in j.groupby(["도시", "집단"], sort=False):
        n = len(g)
        k15 = int(n * 0.15)
        s1_top = set(g.nlargest(k15, "S1 1인세대 비율")["동"])
        flagged = set(g.loc[g["LINK-Radar 지목"], "동"])
        for src, lab in [("H", "H"), ("V", "V"), ("점수(참고)", "곱셈 점수(참고)")]:
            top = set(g.nlargest(k15, src)["동"])
            rows.append({"도시": city, "집단": grp, "2축 지표": lab, "비교": "상위 15%: 2축 vs S1", "n": n, "k": k15,
                         "겹침": len(top & s1_top), "겹침(%)": len(top & s1_top) / k15 * 100 if k15 else np.nan,
                         "우연 기대": k15 * k15 / n, "겹친 동": ", ".join(sorted(top & s1_top)) or "-"})
            k = len(flagged)
            if k:
                topk = set(g.nlargest(k, src)["동"])
                rows.append({"도시": city, "집단": grp, "2축 지표": lab, "비교": "LINK-Radar Level 2+ 동 vs 2축 상위 k", "n": n, "k": k,
                             "겹침": len(topk & flagged), "겹침(%)": len(topk & flagged) / k * 100,
                             "우연 기대": k * k / n, "겹친 동": ", ".join(sorted(topk & flagged)) or "-"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 3. 함께 지목 / 불일치
def reason(r) -> str:
    s1 = f"1인세대 비율 도시 안 {int(r['S1 도시 안 순위'])}위"
    hv = f"H {r['H']:.2f}·V {r['V']:.2f}"
    if r["LINK-Radar 지목"] and r["2축 지목"]:
        return f"{s1}(동 전체 구조 상위) + 이 집단 {hv}로 심각·경계"
    if r["LINK-Radar 지목"]:
        rule = str(r["LINK-Radar 규칙"])
        if "STRUCT-ACCESS" in rule:
            why = f"1인세대 상위 + 거점 거리 {r['A1 안부·관계 거점 거리(km)']:.1f}km 조건"
        elif "ACCESS" in rule:
            why = f"거점 거리 {r['A1 안부·관계 거점 거리(km)']:.1f}km 조건(2축에는 거리 축이 없음)"
        else:
            why = "1인세대 상위 조건"
        lo = []
        if r["H"] < 0.5:
            lo.append("행동 H 낮음")
        if r["V"] < 0.5:
            lo.append("취약 V 낮음")
        return (f"LINK-Radar는 {why}으로 고름({s1}). 2축은 이 집단 {hv}"
                + (f"({', '.join(lo)})" if lo else "") + "라 심각·경계 아님"
                + (". 1인세대 비율은 전 연령 동 값이라 집단 차이를 보지 못함" if "STRUCT" in rule else ""))
    return (f"2축은 이 집단 {hv}로 심각·경계. LINK-Radar 구조 조건은 동 전체 1인세대 비율 상위 동만 보는데 {s1}라 해당 없음"
            + ("" if r["집단"] != "청년 남성" else ". 청년 남성은 LINK-Radar가 동을 고르지 않음(도시 단위)"))


def agreement(j: pd.DataFrame) -> pd.DataFrame:
    a = j[j["LINK-Radar 지목"] | j["2축 지목"]].copy()
    a["판정"] = np.select([a["LINK-Radar 지목"] & a["2축 지목"], a["LINK-Radar 지목"]],
                          ["두 방법이 함께 지목", "LINK-Radar만"], "2축만")
    a["이유"] = a.apply(reason, axis=1)
    order = {"두 방법이 함께 지목": 0, "LINK-Radar만": 1, "2축만": 2}
    a = a.sort_values(["판정", "도시", "집단", "S1 도시 안 순위"], key=lambda s: s.map(order) if s.name == "판정" else s)
    cols = ["판정", "도시", "집단", "동", "SGIS코드", "최고 등급", "LINK-Radar 규칙", "S1 1인세대 비율", "S1 도시 안 순위",
            "A1 안부·관계 거점 거리(km)", "H", "V", "2축 심각·경계 칸", "2축 심각·경계 인구 비중", "2축 기준", "이유"]
    return a[cols]


# ---------------------------------------------------------------- 4. 독립 기준
def perm(x, y, n=10000, seed=0):
    """태연 04와 같은 순열 검정 (단측, 양의 상관)."""
    rx, ry = rankdata(x), rankdata(y)
    rx = (rx - rx.mean()) / rx.std()
    ry = (ry - ry.mean()) / ry.std()
    r = (rx * ry).mean()
    rng = np.random.default_rng(seed)
    null = np.array([(rx * rng.permutation(ry)).mean() for _ in range(n)])
    return r, (np.sum(null >= r) + 1) / (n + 1)


def codes() -> pd.DataFrame:
    return pd.read_csv(EXT / "codes/읍면동_코드대응표_강남_춘천.csv", dtype=str)


def mois_total(kind: str, ymd: str, col: str) -> pd.Series:
    """태연 04 mois() 와 같은 읽기: 행정동 동 합계 칸."""
    c = codes()
    k2s = dict(zip(c["행정기관코드"], c["SGIS코드"]))
    d = pd.read_csv(EXT / f"mois/행정동_성연령별_{kind}_{ymd}.csv", encoding="cp949", dtype=str)
    d.index = d["행정구역"].str.extract(r"\((\d{10})\)")[0].map(k2s)
    d = d[d.index.notna()]
    d.columns = [x.split("_", 1)[1] if "_" in x else x for x in d.columns]
    return pd.to_numeric(d[col].str.replace(",", ""))


def chuncheon_indicators() -> pd.DataFrame:
    """태연 04 table() 과 같은 정의: 인구 이동률 = (전입+전출)/등록인구, 장애인 비율 = 장애인/등록인구 (2024.12)."""
    c = codes()
    chs = c[c["지역"] == "강원 춘천시"]
    names = set(chs["읍면동명"])

    def table(key, sheet, cols):
        f = [x for x in glob.glob(str(EXT / "chuncheon/*.xlsx")) if key in os.path.basename(x)][0]
        out = {}
        for r in openpyxl.load_workbook(f, read_only=True, data_only=True)[sheet].iter_rows(values_only=True):
            v = [x for x in r if x is not None]
            if v and isinstance(v[0], str) and v[0].strip() in names:
                out[v[0].strip()] = [v[i] for i in cols]
        return pd.DataFrame(out).T

    ch = (table("11_", "34.장애인등록", [1]).set_axis(["장애인"], axis=1)
          .join(table("03_", "02.읍면동별 세대 및 인구", [2]).set_axis(["등록인구"], axis=1))
          .join(table("03_", "06.읍면동별인구인동", [1, 4]).set_axis(["전입", "전출"], axis=1)).astype(float))
    ch.index = ch.index.map(dict(zip(chs["읍면동명"], chs["SGIS코드"])))
    ch["인구 이동률(%)"] = (ch["전입"] + ch["전출"]) / ch["등록인구"] * 100
    ch["장애인 비율(%)"] = ch["장애인"] / ch["등록인구"] * 100
    assert len(ch) == 25
    return ch[["인구 이동률(%)", "장애인 비율(%)"]]


def dong_level(ty: pd.DataFrame, lr: pd.DataFrame) -> pd.DataFrame:
    """동 단위: 2축 H·V(모든 칸 주민 가중), LINK-Radar S1(동 값)·고령층 A1, 동 인구."""
    def wavg(col):
        return ty.groupby("SGIS코드")[[col, "주민"]].apply(lambda g: np.average(g[col], weights=g["주민"].clip(lower=1)))
    d = pd.DataFrame({"2축 H(적용)": wavg("적용 H"), "2축 H(사전 등록)": wavg("H"), "2축 V": wavg("V")})
    d = d.join(ty.groupby("SGIS코드")[["도시", "읍면동명"]].first())
    s = lr[(lr["기준일"] == ASOF[0]) & (lr["집단"] == "고령층")].set_index("SGIS코드")
    d["LINK-Radar S1"] = s["S1 1인세대 비율"]
    d["LINK-Radar A1(고령층 거리)"] = s["A1 안부·관계 거점 거리(km)"]
    pop = mois_total("주민등록인구", "20251231", "계_총인구수")
    d["log 동 인구(2025.12)"] = np.log(pop.reindex(d.index).astype(float))
    one = mois_total("1인세대수", "20251231", "계_총세대수")
    d["혼자 사는 주민 비율(%)"] = one.reindex(d.index) / pop.reindex(d.index) * 100
    return d.join(chuncheon_indicators())


def criteria(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    lr_ind = [("LINK-Radar S1", "LINK-Radar")]
    ty_ind = [("2축 H(적용)", "2축"), ("2축 V", "2축"), ("2축 H(사전 등록)", "2축(참고)")]
    ch = d[d["도시"] == "춘천"]
    for crit in ["인구 이동률(%)", "장애인 비율(%)"]:
        for col, who in lr_ind + ty_ind:
            r, p = perm(ch[col], ch[crit])
            rows.append({"방법": who, "지표": col, "기준": f"춘천 {crit} (2024.12)", "도시": "춘천", "n": len(ch), "ρ": r,
                         "순열 p(단측)": p, "판정": "통과" if (r > 0 and p <= 0.05) else "미통과", "구분": "독립 기준"})
    for city in ["강남", "춘천"]:
        g = d[d["도시"] == city]
        for col, who in lr_ind + [("LINK-Radar A1(고령층 거리)", "LINK-Radar(참고)")] + ty_ind:
            r = spearmanr(g[col], g["log 동 인구(2025.12)"])[0]
            rows.append({"방법": who, "지표": col, "기준": "|ρ(지표, log 동 인구)| < 0.3", "도시": city, "n": len(g), "ρ": r,
                         "순열 p(단측)": np.nan, "판정": "통과" if abs(r) < 0.3 else "미통과", "구분": "동 규모 판별"})
    gn = d[d["도시"] == "강남"]
    for col in ["2축 H(적용)", "2축 H(사전 등록)"]:
        r, p = perm(gn[col], gn["혼자 사는 주민 비율(%)"])
        rows.append({"방법": "2축 자체 기준", "지표": col, "기준": "강남 혼자 사는 주민 비율 (2025.12)", "도시": "강남", "n": len(gn),
                     "ρ": r, "순열 p(단측)": p, "판정": "통과" if (r > 0 and p <= 0.05) else "미통과",
                     "구분": "2축 모델 자체 기준 (LINK-Radar 채점 안 함: S1과 같은 입력이라 순환)"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 5. Q4 입력
def q4(ty: pd.DataFrame, j: pd.DataFrame) -> pd.DataFrame:
    covered_dong = {c for cells in COHORT_MAP.values() for c in cells} - set(COHORT_MAP["청년 남성"])
    lr_dongs = j[j["LINK-Radar 지목"] == True].groupby("도시")["동"].apply(set).to_dict()  # noqa: E712
    rows = []
    for (city, sex, age), g in ty.groupby(["도시", "성별", "연령5"]):
        if (sex, age) in covered_dong:
            continue
        status = "동 단위 없음 (청년 남성은 도시 단위 안내)" if (sex, age) in COHORT_MAP["청년 남성"] else "LINK-Radar 정책 집단 없음"
        row = {"도시": city, "성별": sex, "연령5": age, "LINK-Radar 범위": status, "동 수": len(g), "주민": int(g["주민"].sum())}
        for col in ["단계", "생애단계 내 단계", "성별 내 단계"]:
            f = g[g[col].isin(FLAG_STAGES)]
            row[f"{col} 심각·경계 동 수"] = len(f)
            row[f"{col} 심각·경계 동"] = ", ".join(f["읍면동명"]) or "-"
        f = g[g["단계"].isin(FLAG_STAGES)]
        row["지역 안 심각·경계 중 LINK-Radar가 다른 집단으로 지목한 동 수"] = int(f["읍면동명"].isin(lr_dongs.get(city, set())).sum())
        row["이중 위험 동 수"] = int((g["위험 유형"] == "이중 위험").sum())
        rows.append(row)
    return pd.DataFrame(rows)


def fmt(x, nd=2):
    if isinstance(x, (float, np.floating)):
        return "" if np.isnan(x) else f"{x:.{nd}f}"
    return str(x)


def md_table(df: pd.DataFrame, nd=2) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(fmt(r[c], nd) for c in cols) + " |")
    return "\n".join(lines)


def run(write: bool = True) -> dict:
    ty, lr = load_ty(), load_lr()
    j = joined()
    rc, ov, ag = rank_corr(j), overlaps(j), agreement(j)
    dl = dong_level(ty, lr)
    cr = criteria(dl)
    q = q4(ty, j)
    res = {"join": j, "rank": rc, "overlap": ov, "agree": ag, "dong": dl, "criteria": cr, "q4": q}
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        keep = ["도시", "SGIS코드", "동", "집단", "칸 수", "집단 주민", "H", "V", "점수(참고)", "2축 지목", "2축 심각·경계 칸",
                "2축 심각·경계 인구 비중", "2축 유형", "2축 기준", "S1 1인세대 비율", "S1 도시 안 순위", "A1 안부·관계 거점 거리(km)",
                "주민등록 기준월", "등급 추석 D-7", "등급 추석 뒤", "등급 동절기", "최고 등급", "LINK-Radar 규칙", "LINK-Radar 지목"]
        j[keep].to_csv(OUT / "결합표_동x집단.csv", index=False, encoding="utf-8-sig")
        rc.to_csv(OUT / "순위상관.csv", index=False, encoding="utf-8-sig")
        ov.to_csv(OUT / "상위겹침.csv", index=False, encoding="utf-8-sig")
        ag.to_csv(OUT / "함께지목_불일치.csv", index=False, encoding="utf-8-sig")
        dl.reset_index(names="SGIS코드").to_csv(OUT / "동단위_지표.csv", index=False, encoding="utf-8-sig")
        cr.to_csv(OUT / "독립기준_채점.csv", index=False, encoding="utf-8-sig")
        q.to_csv(OUT / "Q4_미포함집단.csv", index=False, encoding="utf-8-sig")
    return res


if __name__ == "__main__":
    r = run()
    j = r["join"]
    print("결합:", j["_merge"].value_counts().to_dict())
    print(r["rank"][r["rank"]["LINK-Radar 지표"] == "S1 1인세대 비율"].round(3).to_string())
    print(r["overlap"].round(2).to_string())
    print(r["agree"]["판정"].value_counts().to_dict())
    print(r["criteria"].round(3).to_string())
    print(r["q4"].to_string())
