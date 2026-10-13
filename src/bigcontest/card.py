"""카드 데이터 공통 함수.

- 데이터2(주제2, 거주지 있음)가 주력, 데이터1(주제1, 시간대 있음)은 보조
- 본사등록 업종·법인 제외, 10대 제외(결제수단 효과), 80대·90대는 '80대+'로 묶음
- 결제건수는 연령별 배율이 곱해진 값 → 같은 연령 안에서만 비교
"""

import pandas as pd

from bigcontest.config import load_config

AGE_ORDER = ["20대", "30대", "40대", "50대", "60대", "70대", "80대+"]
AGE_MAP = {"20대": "20대", "30대": "30대", "40대": "40대", "50대": "50대", "60대": "60대", "70대": "70대", "80대": "80대+", "90대": "80대+"}


def load_card2(local_only: bool = True) -> pd.DataFrame:
    """주력 카드 데이터. 기본은 같은 시도 거주 고객만(강남=서울, 춘천=강원)."""
    d = pd.read_parquet(load_config()["interim_dir"] / "card2.parquet")
    d = d[~d["hq"]]
    if local_only:
        d = d[d["local"]]
    d = d[d["age"].isin(AGE_MAP)].copy()
    d["age_g"] = d["age"].map(AGE_MAP)
    return d


def load_card1() -> pd.DataFrame:
    """보조 카드 데이터(시간대). 법인·본사등록·LPG가스(자동결제) 제외."""
    d = pd.read_parquet(load_config()["interim_dir"] / "card1.parquet")
    d = d[~d["hq"] & ~d["corp"] & (d["MCT_RY_CD"] != "LPG가스")]
    d = d[d["age"].isin(AGE_MAP)].copy()
    d["age_g"] = d["age"].map(AGE_MAP)
    return d


def daily(d: pd.DataFrame, by: list[str], value: str = "USE_CNT") -> pd.DataFrame:
    """날짜 × by 집계. 결제가 없는 날은 0으로 채운다."""
    s = d.groupby(["date"] + by)[value].sum()
    full = pd.MultiIndex.from_product(
        [pd.date_range("2025-07-01", "2025-12-31")] + [sorted(d[b].unique()) for b in by],
        names=["date"] + by,
    )
    return s.reindex(full, fill_value=0).rename(value).reset_index()
