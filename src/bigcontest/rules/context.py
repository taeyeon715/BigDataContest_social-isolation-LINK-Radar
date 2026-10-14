"""카드 맥락 신호와 명절 묶음 (rules-v1.1). 등급 축이 아니라 반증 맥락·표시용 계산.

- visitor_context: 연휴 외지 손님 비중 (가맹점 도시 결제 중 다른 시도 거주 고객) vs 연휴 직전 8주 주말
- time_rhythm: 카드 데이터1 오전(6~11시) 결제 비중의 고령 − 40대 차이, 달마다 (일조 효과를 연령 간 차이로 일부 지움)
- diversity: 하루 결제 N건당 기대 업종 수 (복원 추출 희박화 근사, 결제량 보정)
- holiday_timeline: 명절 전(R1 D-7)·중(R2 당일·다음 날)·후(R3 연휴 뒤 10일)를 한 사건으로

모두 도시 단위다 (카드는 동을 가리키지 않음). 기준일 카드(공급 지연을 뺀 날)까지의 자료만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

ELDERLY_CARD = ("60대", "70대", "80대+")
RECOVERY_DAYS = 10  # R3 연휴 뒤 회복 확인 기간 (signal.RECOVERY_DAYS 와 같음)


# ---------------------------------------------------------------- 연휴 외지 손님 비중


@dataclass
class VisitorContext:
    block: tuple[date, date, str]
    holiday_share: float | None  # 연휴 중(기준일 카드까지 관측한 날) 외지 손님 비중
    base_share: float | None  # 연휴 직전 8주 주말(공휴일 아님) 비중
    base_sd: float | None  # 그 주말들의 날별 비중 표준편차
    n_holiday_days: int
    n_base_days: int
    surge_pp: float  # 기준 (rules.yaml card_context.visitor_surge_pp)

    @property
    def diff(self) -> float | None:
        if self.holiday_share is None or self.base_share is None:
            return None
        return self.holiday_share - self.base_share

    @property
    def verdict(self) -> str:
        if self.diff is None:
            return "관측 전"
        return "이동과 일관" if self.diff >= self.surge_pp else "이동 신호 없음"

    def text(self) -> str:
        s, e, name = self.block
        if self.diff is None:
            return f"{name} 연휴({s:%m/%d}~{e:%m/%d}) 외지 손님 비중: 기준일 카드에 연휴 날이 아직 없음"
        sd = f", 주말 날별 표준편차 {self.base_sd:.1%}" if self.base_sd is not None else ""
        return (f"{name} 연휴({s:%m/%d}~{e:%m/%d}, 관측 {self.n_holiday_days}일) 외지 손님 비중 {self.holiday_share:.1%} "
                f"vs 직전 8주 주말 {self.base_share:.1%} ({self.n_base_days}일{sd}) → {self.diff * 100:+.1f}%p "
                f"(기준 +{self.surge_pp * 100:.0f}%p): {self.verdict}")


def visitor_context(visitors: pd.DataFrame, city: str, block: tuple[date, date, str], holidays: set[date],
                    cutoff: date, rules: dict) -> VisitorContext:
    """visitors: DataSource.card2_visitors() (date, city) × local(False/True) 결제건수."""
    cc = rules["thresholds"]["card_context"]
    s, e, _ = block
    v = visitors.xs(city, level="city") if city in visitors.index.get_level_values("city") else None
    if v is None:
        return VisitorContext(block, None, None, None, 0, 0, cc["visitor_surge_pp"])
    v = v[v.index <= pd.Timestamp(cutoff)]
    share = v[False] / (v[False] + v[True])
    days = share.index.date
    hol_mask = (days >= s) & (days <= e)
    b0 = s - timedelta(weeks=cc["visitor_baseline_weeks"])
    base_mask = (days >= b0) & (days < s) & (share.index.dayofweek >= 5) & ~np.isin(days, list(holidays))
    hv = v[hol_mask]
    bv = v[base_mask]
    hs = float(hv[False].sum() / (hv[False].sum() + hv[True].sum())) if len(hv) else None
    bs = float(bv[False].sum() / (bv[False].sum() + bv[True].sum())) if len(bv) else None
    sd = float(share[base_mask].std()) if base_mask.sum() >= 2 else None
    return VisitorContext(block, hs, bs, sd, int(hol_mask.sum()), int(base_mask.sum()), cc["visitor_surge_pp"])


# ---------------------------------------------------------------- 시간대 상대 리듬


def time_rhythm(card1: pd.DataFrame, city: str, cutoff: date, band: str = "06_11") -> pd.DataFrame:
    """달마다 (고령 오전 비중, 40대 오전 비중, 차이). 기준일 카드(cutoff)까지, 그 달 관측일 20일 이상인 달만.

    오전 비중 = 같은 연령 안에서 오전(band) 결제 ÷ 모든 시간대 결제. 연령끼리 건수는 비교하지 않고 비중만."""
    d = card1[(card1["city"] == city) & (card1["date"] <= pd.Timestamp(cutoff))]
    d = d.assign(month=d["date"].dt.month, grp=np.where(d["age_g"].isin(ELDERLY_CARD), "고령",
                                                         np.where(d["age_g"] == "40대", "40대", None)))
    d = d[d["grp"].notna()]
    ndays = d.groupby("month")["date"].nunique()
    tot = d.groupby(["month", "grp"])["USE_CNT"].sum()
    am = d[d["TIME_GB"] == band].groupby(["month", "grp"])["USE_CNT"].sum()
    sh = (am / tot).unstack("grp")
    sh = sh[sh.index.isin(ndays[ndays >= 20].index)]
    if sh.empty or not {"고령", "40대"} <= set(sh.columns):
        return pd.DataFrame(columns=["고령", "40대", "차이"])
    sh["차이"] = sh["고령"] - sh["40대"]
    return sh[["고령", "40대", "차이"]]


# ---------------------------------------------------------------- 업종 다양성


def expected_categories(counts: np.ndarray, draws: int) -> float:
    """결제 draws 건을 (복원 추출로) 뽑을 때 기대 업종 수: Σ 1 − (1 − p_i)^draws. 결제량이 달라도 같은 건수로 맞춤."""
    c = np.asarray(counts, dtype=float)
    c = c[c > 0]
    if c.sum() <= 0:
        return float("nan")
    p = c / c.sum()
    return float(np.sum(1.0 - (1.0 - p) ** draws))


def daily_diversity(cat_counts: pd.Series, city: str, age: str, sex: str, draws: int) -> pd.Series:
    """DataSource.card2_categories() → 그 코호트의 날짜별 기대 업종 수."""
    try:
        g = cat_counts.loc[(city, age, sex)]
    except KeyError:
        return pd.Series(dtype=float)
    return g.groupby(level="date").apply(lambda s: expected_categories(s.to_numpy(), draws))


# ---------------------------------------------------------------- 명절 전·중·후


def holiday_bundle(blocks: list[tuple[date, date, str]], holiday_names: dict, as_of: date) -> tuple | None:
    """기준일이 걸린 명절 묶음 (연휴 시작 7일 전 ~ 연휴 끝 10일 뒤). 명절(추석·설) 이름이 있는 연휴만.
    (시작, 끝, 이름, 명절 당일 목록)."""
    for s, e, name in blocks:
        days = [d.date() if hasattr(d, "date") else d for d, n in holiday_names.items()
                if n in ("추석", "설날") and s <= (d.date() if hasattr(d, "date") else d) <= e]
        if not days:
            continue
        if s - timedelta(days=7) <= as_of <= e + timedelta(days=RECOVERY_DAYS):
            return s, e, name, sorted(days)
    return None


def _status(as_of: date, a: date, b: date) -> str:
    return "예정" if as_of < a else ("지남" if as_of > b else "지금")


def holiday_timeline(bundle: tuple, as_of: date, city: str, elderly: bool) -> tuple[str, list[dict]]:
    """명절 묶음 → (event_bundle_id, [단계 dict]). R3 은 춘천 고령층에만 적용 (applies=False 면 표시만)."""
    s, e, name, days = bundle
    r2a, r2b = days[0], days[-1] + timedelta(days=1)
    steps = [
        {"phase": "전", "code": "R1", "label": f"연휴 D-7 준비 ({s - timedelta(days=7):%m/%d}~{s - timedelta(days=1):%m/%d})",
         "start": s - timedelta(days=7), "end": s - timedelta(days=1), "applies": True},
        {"phase": "중", "code": "R2", "label": f"명절 당일·다음 날 ({r2a:%m/%d}~{r2b:%m/%d}, 연휴 {s:%m/%d}~{e:%m/%d})",
         "start": r2a, "end": r2b, "applies": True},
        {"phase": "후", "code": "R3", "label": f"연휴 뒤 회복 확인 ({e + timedelta(days=1):%m/%d}~{e + timedelta(days=RECOVERY_DAYS):%m/%d})"
         + ("" if city == "춘천" and elderly else " · 춘천 고령층만 후보"),
         "start": e + timedelta(days=1), "end": e + timedelta(days=RECOVERY_DAYS), "applies": city == "춘천" and elderly},
    ]
    for st in steps:
        st["status"] = _status(as_of, st["start"], st["end"])
    return f"{city}:명절:{s:%Y%m%d}", steps


def timeline_text(steps: list[dict]) -> str:
    return "명절 묶음: " + " · ".join(f"{st['phase']} {st['code']} {st['label']} [{st['status']}]" for st in steps)
