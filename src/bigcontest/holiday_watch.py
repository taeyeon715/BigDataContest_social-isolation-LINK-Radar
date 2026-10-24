"""연휴 주의 지역 (configs/rules_v2.yaml holiday_watch, reports/r3_규칙개정/README.md 8절 R-3b). LLM 과 무관하고 결정적이다.

연휴에 실제로 끊기는 연령(카드, 도시 단위)을 규칙으로 두고, 그 연령 칸(동 × 성) 중 근거 축이 2개 이상인 칸을
rules-v2.0 등급 판정(levels_v2.decide_v2)으로 고른 뒤 동으로 묶는다.
- 어느 연령: holiday_watch.target_ages (R-3b, 2025 추석 1회 관측)
- 어느 동: S(1인 비율)·H(행동 고립)·V(경제 취약)·A(거점 거리) 축 (cells·stay·vulnerability)
- 언제: 연휴 전 lead_days(운영 가정) ~ 연휴 끝, 그리고 follow_up_days 가 있는 도시는 연휴 뒤까지
- 참고 근거: 기준일에 볼 수 있는 지난 연휴의 카드 값 (`card_gap`). 등급 입력은 아님
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from bigcontest.cells import s_axis
from bigcontest.rules.levels_v2 import CellFacts, cell_type, decide_v2, load_rules_v2
from bigcontest.stay import h_axis
from bigcontest.vulnerability import v_axis

# 칸 연령 → 거점 거리를 잰 정책 집단 (경로당·노인맞춤돌봄은 고령층 자원). R-3 r3_rules_v2.py 와 같음
AGE_GROUP = {"60대+": "고령층", "50대": "중장년 남성 1인가구", "40대": "중장년 남성 1인가구",
             "30대": "청년 남성", "20대": "청년 남성"}
CARD_AGES = {"60대+": ["60대", "70대", "80대+"]}
CLINIC = ["일반병원", "치과병원", "한의원", "기타의료", "약국"]


def watch_config(rules: dict | None = None) -> dict:
    return (rules or load_rules_v2())["holiday_watch"]


@dataclass(frozen=True)
class Phase:
    name: str  # 연휴 이름 (예: 추석)
    start: date
    end: date
    phase: str  # 연휴 전 / 연휴 중 / 연휴 뒤

    @property
    def label(self) -> str:
        return f"{self.start.year} {self.name} {self.phase} ({self.start:%m/%d}~{self.end:%m/%d})"

    @property
    def follow_up(self) -> bool:
        return self.phase == "연휴 뒤"


def holiday_phase(blocks: list[tuple[date, date, str]], as_of: date, city: str, rules: dict | None = None) -> Phase | None:
    """기준일이 어느 연휴의 주의 기간인가. blocks 는 signal.holiday_blocks (공휴일이 낀 3일 이상 연휴)."""
    c = watch_config(rules)
    lead, follow = int(c["lead_days"]), int((c.get("follow_up_days") or {}).get(city, 0))
    for start, end, name in blocks:
        if start - timedelta(days=lead) <= as_of < start:
            return Phase(name, start, end, "연휴 전")
        if start <= as_of <= end:
            return Phase(name, start, end, "연휴 중")
        if follow and end < as_of <= end + timedelta(days=follow):
            return Phase(name, start, end, "연휴 뒤")
    return None


def cell_axes(pop: pd.DataFrame, one: pd.DataFrame, tel_age: pd.DataFrame, tel_time: pd.DataFrame, res_month: int,
              tel_months: list[int], as_of: date, access_km: dict[tuple[str, str], float] | None = None,
              ages: list[str] | None = None, rules: dict | None = None) -> pd.DataFrame:
    """칸(동 × 성 × 연령)마다 S·H·V·A. res_month·tel_months 는 기준일에 쓸 수 있던 달 (시간 계약은 부르는 쪽).
    access_km: (adm_cd, 정책 집단) → 안부·관계 거점 직선거리."""
    rules = rules or load_rules_v2()
    ax = rules["axes"]
    s = s_axis(pop, one, res_month, ax["top_share"])
    h = h_axis(tel_age, tel_time, pop, tel_months, ax["top_share"])
    v = v_axis(pop, res_month)
    key = ["adm_cd", "sex", "age5"]
    df = s.merge(h[key + ["h", "h_top", "h_state", "h_basis"]], on=key, how="left")
    df = df.merge(v[key + ["v_eb", "v_top", "v_state", "v_source"]], on=key, how="left")
    if ages:
        df = df[df["age5"].isin(ages)].copy()
    acc = access_km or {}
    df["a_km"] = [acc.get((a, AGE_GROUP[g]), np.nan) for a, g in zip(df["adm_cd"], df["age5"])]
    df["a_top"] = df["a_km"].ge(float(ax["access_far_km"]))
    pub = ax["v_published"].get("강남")
    gn_ok = pub is None or as_of >= date.fromisoformat(pub)
    df["v_counts"] = df["v_top"].fillna(False).astype(bool) & ((df["city"] != "강남") | gn_ok)
    return df.reset_index(drop=True)


def cell_facts(row, trigger: str, rules: dict, card_gap: float | None = None) -> CellFacts:
    """칸 하나 → 등급 판정 입력. card_gap: 그 도시·성의 연휴 연령 카드 값(40·50대 대비, card_gap 의 rel).
    holiday_watch.card_min_gap 보다 낮으면 C(카드) 신호. None 이면 카드 신호를 세지 않는다."""
    axes = {"S": bool(row["s_top"]), "A": bool(row["a_top"])}
    why = {"S": f"1인 비율 {row['s_eb']:.3f}, 같은 성·연령 {int(row['s_rank'])}/{int(row['s_n'])}위"
           if pd.notna(row["s_eb"]) else ""}
    if row.get("h_state") == "observed":
        axes["H"] = bool(row["h_top"])
        why["H"] = f"H {row['h']:.2f} ({row['h_basis']})"
    if row.get("v_state") == "observed":
        axes["V"] = bool(row["v_counts"])
        why["V"] = f"{row['v_eb']:.3f} ({row['v_source']})"
    if pd.notna(row["a_km"]):
        why["A"] = f"거점 직선 {row['a_km']:.1f}km (현재 원장 기준)"
    if card_gap is not None and not pd.isna(card_gap):
        axes["C"] = card_gap <= float(watch_config(rules)["card_min_gap"])
        why["C"] = f"지난 연휴 의료 결제 40·50대 대비 {card_gap:+.0%}"
    conf, cwhy = "보통", ["가정한 공개 시점(simulated_replay)", "현재 원장 기준 접근성"]
    from bigcontest.rules.signal_check import unverified

    unver = [a for a in unverified(row["sex"], row["age5"]) if axes.get(a)]
    if row["s_small"]:
        conf = "낮음"
        cwhy.append(f"칸 인구 {int(row['pop']) if pd.notna(row['pop']) else 0}명 < {rules['unit']['small_cell']}")
    return CellFacts("scheduled_access_review", "충족", [], trigger, axes, why, confidence=conf, confidence_why=cwhy,
                     unverified=unver)


def watch_cells(axes: pd.DataFrame, trigger: str, rules: dict | None = None,
                card: dict[str, float] | None = None) -> pd.DataFrame:
    """주의 연령 칸마다 등급을 정하고 Level 3 칸만 돌려준다 (근거 축 많은 순 → 칸 인구 큰 순).
    card: 성(남·여) → 기준일에 볼 수 있는 지난 연휴의 카드 값 (없으면 카드 신호 없음 → Level 3 불가)."""
    rules = rules or load_rules_v2()
    ages = watch_config(rules)["target_ages"]
    card = card or {}
    rows = []
    for _, r in axes[axes["age5"].isin(ages)].iterrows():
        f = cell_facts(r, trigger, rules, card.get(r["sex"]))
        d = decide_v2(f, rules)
        rows.append({**{k: r[k] for k in ("city", "adm_cd", "sex", "age5", "pop")},
                     "axes_on": d.axes_on, "n_axes": len(d.axes_on), "cell_type": cell_type(d.axes_on),
                     "level": d.level, "rule_id": d.rule_id, "caps": d.caps, "reason": d.reason,
                     "confidence": f.confidence})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out[out["level"] == "Level 3"]
    return out.sort_values(["n_axes", "pop"], ascending=[False, False]).reset_index(drop=True)


def dongs_of(cells: pd.DataFrame) -> list[str]:
    """칸 → 동 (처음 나온 순서, 중복 없음)."""
    return list(dict.fromkeys(cells["adm_cd"]))


# ---------------------------------------------------------------- 참고 근거: 지난 연휴의 카드 값


def card_periods(blocks: list[tuple[date, date, str]], holiday_names: dict, cutoff: date) -> list[tuple[date, date, str]]:
    """기준일에 결제를 다 볼 수 있는 지난 연휴·공휴일 (끝 ≤ cutoff). 연휴 묶음과 묶음 밖 하루 공휴일."""
    out = [(s, e, n) for s, e, n in blocks if e <= cutoff]
    inside = {d for s, e, _ in blocks for d in pd.date_range(s, e).date}
    for ts, n in holiday_names.items():
        d = ts.date()
        if d not in inside and d <= cutoff:
            out.append((d, d, n))
    return sorted(out)


def card_gap(card2: pd.DataFrame, city: str, period: tuple[date, date, str], holidays: list,
             rules: dict | None = None) -> pd.DataFrame:
    """지난 연휴 하나: 주의 연령의 의료 결제가 같은 성 40·50대보다 평소 대비 얼마나 더 줄었나 (비율, −0.3 = 30% 더 줄어듦).
    평소 값 = 연휴 시작 전 card_base_weeks 주의 같은 요일 평균 (공휴일 제외). 기준일 뒤 날은 쓰지 않는다."""
    c = watch_config(rules)
    start, end, _ = period
    days = pd.date_range(start, end)
    base = pd.date_range(start - timedelta(weeks=int(c["card_base_weeks"])), start - timedelta(days=1))
    hol = set(pd.DatetimeIndex(holidays))
    base = base[~base.isin(list(hol))]
    d = card2[(card2["city"] == city) & card2["MCT_RY_CD"].isin(CLINIC)]
    d = d[d["date"].between(base[0], days[-1])]
    ages = {a: [a] for a in c["card_ref_ages"]} | {a: CARD_AGES.get(a, [a]) for a in c["target_ages"]}
    rows = []
    for sex in ("남성", "여성"):
        lr = {}
        for name, members in ages.items():
            s = d[(d["SEX_CCD"] == sex) & d["age_g"].isin(members)].groupby("date")["USE_CNT"].sum()
            s = s.reindex(base.append(days), fill_value=0)
            exp = s.loc[base].groupby(base.dayofweek).mean()
            denom = float(exp.reindex(days.dayofweek).sum())
            lr[name] = np.log(float(s.loc[days].sum()) / denom) if denom > 0 and s.loc[days].sum() > 0 else np.nan
        ref = np.nanmean([lr[a] for a in c["card_ref_ages"]])
        for a in c["target_ages"]:
            rows.append({"city": city, "sex": sex, "age": a, "ratio": float(np.expm1(lr[a])),
                         "rel": float(np.expm1(lr[a] - ref)), "start": start, "end": end, "name": period[2],
                         "base_from": base[0].date(), "base_to": base[-1].date()})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 동절기 칸 (rules-v2.0 설계 3절: W → 춘천 읍·면 60대 이상 칸)

WINTER_AGES = ["60대+"]


def winter_facts(row, w: pd.Series | None, trigger: str, rules: dict) -> CellFacts:
    """동절기 칸 하나. 그 읍·면 주민형 격자 60+ 겨울/여름 변화(SK)가 rules winter.decline 미만이면 W 신호.
    겨울 달이 아직 없으면 필수 근거 결측(부분 판정), 격자가 적어 읍·면 전체 값이면 공유 맥락 표시."""
    f = cell_facts(row, trigger, rules)
    if w is None or pd.isna(w.get("w_ok")):
        f.input_status, f.missing_inputs = "부분 판정", ["읍·면 겨울 체류 변화 (겨울 달 공개 전)"]
    else:
        f.axes["W"] = bool(w["w_ok"])
        f.axis_why["W"] = f"60+ 겨울/여름 {w['w60']:+.1%}"
        if bool(w["shared"]):
            f.confidence_why.append("겨울 체류: 읍·면 전체 값(공유 맥락)")
    return f


def winter_cells(axes: pd.DataFrame, w: pd.DataFrame, eupmyeon: set[str], trigger: str,
                 rules: dict | None = None) -> pd.DataFrame:
    """춘천 읍·면 60대 이상 칸마다 동절기 등급 (levels_v2.decide_v2). 모든 칸을 돌려준다 (Level 1·2·3)."""
    rules = rules or load_rules_v2()
    wk = w.set_index("adm_cd") if len(w) else pd.DataFrame()
    rows = []
    sel = axes[(axes["city"] == "춘천") & axes["adm_cd"].isin(eupmyeon) & axes["age5"].isin(WINTER_AGES)]
    for _, r in sel.iterrows():
        wr = wk.loc[r["adm_cd"]] if r["adm_cd"] in wk.index else None
        f = winter_facts(r, wr, trigger, rules)
        d = decide_v2(f, rules)
        rows.append({**{k: r[k] for k in ("city", "adm_cd", "sex", "age5", "pop")},
                     "axes_on": d.axes_on, "n_axes": len(d.axes_on), "cell_type": cell_type(d.axes_on),
                     "level": d.level, "rule_id": d.rule_id, "caps": d.caps, "reason": d.reason,
                     "input_status": f.input_status, "missing": f.missing_inputs, "confidence": f.confidence,
                     "w60": None if wr is None else wr["w60"]})
    return pd.DataFrame(rows)
