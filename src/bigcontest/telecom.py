"""통신 데이터 공통 함수."""

import numpy as np
import pandas as pd

from bigcontest.config import ROOT

SEXES = {"MAN": "남", "WMAN": "여"}
AGES = {"10G": "10대", "20G": "20대", "30G": "30대", "40G": "40대", "50G": "50대", "60GU": "60대+"}
AGE_COLS = [f"{s}_FLOW_POP_CNT_{a}" for s in SEXES for a in AGES]
TIME_COLS = [f"TMST_{h:02d}" for h in range(24)]
DAYS = ["MON", "TUS", "WED", "THU", "FRI", "SAT", "SUN"]
WKDY_COLS = [f"FLOW_POP_CNT_{d}" for d in DAYS]
KEY = ["STD_YM", "BLOCK_CD", "X_COORD", "Y_COORD"]


def age_cols(age: str) -> list[str]:
    """예: age_cols('60GU') → 남녀 60대+ 두 컬럼"""
    return [f"{s}_FLOW_POP_CNT_{age}" for s in SEXES]


def weekday_weights(ym: str) -> np.ndarray:
    """그달의 월~일 요일 수 (요일 값 가중평균용)."""
    days = pd.date_range(f"{ym[:4]}-{ym[4:]}-01", periods=pd.Period(ym, "M").days_in_month)
    return np.bincount(days.dayofweek, minlength=7)


def adm_names() -> pd.DataFrame:
    return pd.read_csv(ROOT / "ref" / "adm_cd_names.csv", dtype=str)


def grid_id(x: pd.Series, y: pd.Series, size: int = 250) -> pd.Series:
    """50m 칸 좌표를 size(m) 격자 번호로 묶는다."""
    gx = (x // size).astype(int)
    gy = (y // size).astype(int)
    return gx.astype(str) + "_" + gy.astype(str)
