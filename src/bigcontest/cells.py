"""rules-v2.0 분석 단위와 S축 (reports/r3_규칙개정/변경계획.md 1·2·7절).

- 칸 = 행정동 × 성(남·여) × 연령(20대·30대·40대·50대·60대 이상). 강남 22 + 춘천 25개 동 → 470칸.
- S 혼자 사는 구조 = 칸의 1인세대 수 ÷ 칸의 주민등록 인구 (같은 동·같은 달).
  작은 칸의 흔들림은 경험적 베이즈(베타-이항, 적률법)로 같은 도시·성·연령 칸들 쪽으로 줄인다.
- S가 '섬' = 같은 도시·성·연령 안 상위 1/3(통계청 사회적 고립도 33.0%에 맞춘 고정값) 그리고
  그 도시 같은 성·연령 전체 비율 이상.

비교는 언제나 같은 도시 안 같은 성·연령 칸끼리 한다. 연령 사이 수준 차이는 이 축에 넣지 않는다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bigcontest.config import ROOT

AGE5 = ["20대", "30대", "40대", "50대", "60대+"]
SEXES = ["남", "여"]
TOP_SHARE = 1 / 3  # 통계청 사회적 고립도 2025 33.0% (국가지표체계). 설계 5절, 결과 전 고정
SMALL_CELL = 100  # 칸 인구가 이보다 작으면 근거 신뢰도 낮음 (설계 3절)


def age5(age: int | float) -> str | None:
    """1세 또는 구간 시작 나이 → 칸 연령. 20세 미만은 칸 밖(None)."""
    if age is None or (isinstance(age, float) and np.isnan(age)) or age < 20:
        return None
    return "60대+" if age >= 60 else f"{int(age) // 10 * 10}대"


def city_of() -> dict[str, str]:
    names = pd.read_csv(ROOT / "ref" / "adm_cd_names.csv", dtype=str)
    return dict(zip(names["adm_cd"], names["city"]))


def cell_index() -> pd.DataFrame:
    """470칸 (city, adm_cd, sex, age5)."""
    names = pd.read_csv(ROOT / "ref" / "adm_cd_names.csv", dtype=str)
    idx = pd.MultiIndex.from_product([names["adm_cd"], SEXES, AGE5], names=["adm_cd", "sex", "age5"]).to_frame(index=False)
    idx["city"] = idx["adm_cd"].map(dict(zip(names["adm_cd"], names["city"])))
    return idx[["city", "adm_cd", "sex", "age5"]]


def cell_population(pop: pd.DataFrame, month: int) -> pd.DataFrame:
    """주민등록 성·연령 인구(1세, external.resident_population) → 칸 인구 (그 달)."""
    p = pop[pop["month"] == month].copy()
    p["age5"] = p["age"].map(age5)
    p = p.dropna(subset=["age5"])
    return p.groupby(["adm_cd", "sex", "age5"], as_index=False)["pop"].sum(min_count=1)


def cell_one_person(one: pd.DataFrame, month: int) -> pd.DataFrame:
    """성·연령별 1인세대(5세 구간, external.resident_single_by_age) → 칸 1인세대 수 (그 달)."""
    o = one[one["month"] == month].copy()
    o["age5"] = o["age_lo"].map(age5)
    o = o.dropna(subset=["age5"]).rename(columns={"n": "one_person"})
    return o.groupby(["adm_cd", "sex", "age5"], as_index=False)["one_person"].sum(min_count=1)


def eb_beta_prior(k: pd.Series, n: pd.Series) -> tuple[float, float]:
    """베타-이항 적률법: 칸 비율의 관측 분산에서 이항 잡음을 빼 참 분산을 구하고 (α, β)를 정한다.

    참 분산이 0 이하(칸 차이가 잡음보다 작음)면 사전 강도 α+β = 칸 인구 중앙값으로 둔다
    (칸 값이 도시 평균과 칸 관측을 반반 섞는 정도). 결과를 보지 않는 고정 규칙."""
    ok = n > 0
    k, n = k[ok].astype(float), n[ok].astype(float)
    if len(k) < 2:
        return float("nan"), float("nan")
    m = float(k.sum() / n.sum())
    r = k / n
    w = n / n.sum()
    var_obs = float((w * (r - m) ** 2).sum())
    var_noise = float((w * m * (1 - m) / n).sum())
    var_true = var_obs - var_noise
    strength = m * (1 - m) / var_true - 1 if var_true > 0 else float(np.median(n))
    strength = max(strength, 1e-9)
    return m * strength, (1 - m) * strength


def s_axis(pop: pd.DataFrame, one: pd.DataFrame, month: int, top_share: float = TOP_SHARE) -> pd.DataFrame:
    """칸마다 S축: 관측 비율, 수축 비율, 도시·성·연령 안 순위, 섬 여부, 작은 칸 표시.

    열: city, adm_cd, sex, age5, pop, one_person, s_raw, s_eb, s_city_rate, s_rank, s_n, s_top, s_small, s_state
    s_state: observed / missing (분모 0·결측)."""
    cells = cell_index()
    df = (cells.merge(cell_population(pop, month), on=["adm_cd", "sex", "age5"], how="left")
               .merge(cell_one_person(one, month), on=["adm_cd", "sex", "age5"], how="left"))
    df["s_raw"] = df["one_person"] / df["pop"].where(df["pop"] > 0)
    df["s_eb"] = np.nan
    df["s_city_rate"] = np.nan
    for (city, sex, a), g in df.groupby(["city", "sex", "age5"]):
        ok = g["pop"].gt(0) & g["one_person"].notna()
        al, be = eb_beta_prior(g.loc[ok, "one_person"], g.loc[ok, "pop"])
        df.loc[g.index[ok], "s_eb"] = (g.loc[ok, "one_person"] + al) / (g.loc[ok, "pop"] + al + be)
        df.loc[g.index, "s_city_rate"] = g.loc[ok, "one_person"].sum() / g.loc[ok, "pop"].sum()
    df["s_state"] = np.where(df["s_eb"].notna(), "observed", "missing")
    grp = df.groupby(["city", "sex", "age5"])["s_eb"]
    df["s_rank"] = grp.rank(ascending=False, method="first")
    df["s_n"] = grp.transform("count")
    cut = np.ceil(df["s_n"] * top_share)
    df["s_top"] = (df["s_rank"] <= cut) & (df["s_eb"] >= df["s_city_rate"]) & df["s_eb"].notna()
    df["s_small"] = df["pop"].fillna(0) < SMALL_CELL
    return df
