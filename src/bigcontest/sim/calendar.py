"""달력·날씨 (모두 공개 자료): 공휴일(configs/project.yaml), ASOS 일자료·체감온도(external/weather)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from bigcontest.config import ROOT
from bigcontest.sim import params as P

PUBLIC_FILES = {
    "config": "configs/project.yaml",
    "asos": "external/weather/kma_asos_daily_2025-07_12.csv",
    "asos_heat": "external/weather/kma_asos_heat_2025-07_12.csv",
}


@dataclass
class Calendar:
    dates: pd.DatetimeIndex
    holidays: list[pd.Timestamp]
    holiday_names: dict[pd.Timestamp, str]
    is_hol: np.ndarray  # 공휴일
    off_block: np.ndarray  # 공휴일이 낀 3일 이상 연휴 (주말 포함)
    pre_hol: np.ndarray  # 연휴 직전 2일 (장보기)
    heat: dict[str, np.ndarray]  # 도시 → 체감 33℃ 이상
    cold: dict[str, np.ndarray]  # 도시 → 최저 −12℃ 이하
    rain: dict[str, np.ndarray]  # 도시 → 강수 10mm 이상
    month: np.ndarray
    dow: np.ndarray

    @property
    def winter(self) -> np.ndarray:
        return np.isin(self.month, P.WINTER_MONTHS)


def _blocks(dates: pd.DatetimeIndex, hol: set) -> np.ndarray:
    off = np.array([(d.dayofweek >= 5) or (d in hol) for d in dates])
    out = np.zeros(len(dates), bool)
    i = 0
    while i < len(dates):
        if not off[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(dates) and off[j + 1]:
            j += 1
        if j - i + 1 >= 3 and any(d in hol for d in dates[i: j + 1]):
            out[i: j + 1] = True
        i = j + 1
    return out


def load_calendar(root: Path = ROOT, extra_holidays: dict | None = None, weather_override: dict | None = None,
                  files: dict | None = None) -> Calendar:
    """extra_holidays: 스트레스 시험용 가상 공휴일 {날짜: 이름}. weather_override: {도시: {"cold"/"heat"/"rain": 날짜 목록}}."""
    f = {k: Path(root) / v for k, v in PUBLIC_FILES.items()} if files is None else files
    with open(f["config"], encoding="utf-8") as fh:
        names = {pd.Timestamp(k): v for k, v in yaml.safe_load(fh)["holidays"].items()}
    names.update({pd.Timestamp(k): v for k, v in (extra_holidays or {}).items()})
    dates = pd.date_range(P.START, P.END)
    hol = set(names)
    is_hol = dates.isin(list(hol))
    block = _blocks(dates, hol)
    starts = np.where(block & ~np.r_[False, block[:-1]])[0]
    pre = np.zeros(len(dates), bool)
    for s in starts:
        pre[max(0, s - 2): s] = True
    w = pd.read_csv(f["asos"], parse_dates=["date"])
    h = pd.read_csv(f["asos_heat"], parse_dates=["date"])
    heat, cold, rain = {}, {}, {}
    for c in P.CITIES:
        wc = w[w["city"] == c].set_index("date").reindex(dates)
        hc = h[h["city"] == c].set_index("date").reindex(dates)
        heat[c] = (hc["at_max"] >= P.HEAT_AT).fillna(False).to_numpy().copy()
        cold[c] = (wc["tmin"] <= P.COLD_TMIN).fillna(False).to_numpy().copy()
        rain[c] = (wc["rain"].fillna(0) >= P.RAIN_MM).to_numpy().copy()
        for kind, arr in (("cold", cold), ("heat", heat), ("rain", rain)):
            for d in (weather_override or {}).get(c, {}).get(kind, []):
                arr[c][dates.get_loc(pd.Timestamp(d))] = True
    return Calendar(dates, sorted(hol), names, is_hol, block, pre & ~block, heat, cold, rain,
                    dates.month.to_numpy(), dates.dayofweek.to_numpy())
