"""연휴 주의 지역과 동절기 칸 판정.

연휴에 결제가 끊기는 연령(지금은 60대 이상, 2025 추석 1회 관측)의 칸만 보고, 그중 Level 3인 칸을 동으로 묶는다.
카드 신호는 기준일에 결제를 다 볼 수 있는 지난 연휴의 값이다. 기준은 configs/rules_v2.yaml holiday_watch.
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

# 칸 연령별로 거점 거리를 잰 정책 집단 (경로당·노인맞춤돌봄은 고령층 자원)
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
    """칸마다 S·H·V·A 값. 달은 부르는 쪽이 기준일에 쓸 수 있던 달로 넘긴다.
    access_km 은 (동 코드, 정책 집단)별 안부·관계 거점 직선거리."""
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
    """칸 하나의 판정 입력. card_gap 은 그 도시·성의 지난 연휴 카드 값(40·50대 대비)이고,
    card_min_gap 보다 낮으면 카드 신호가 선다. None 이면 카드 신호를 세지 않는다."""
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
    """주의 연령 칸의 등급을 정하고 Level 3 칸만 돌려준다. 선 신호가 많은 칸, 인구가 많은 칸 순.
    card 는 성별 지난 연휴 카드 값. 없으면 카드 신호가 없어 Level 3가 나오지 않는다."""
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
    """칸 목록의 동 코드 (처음 나온 순서, 중복 없음)."""
    return list(dict.fromkeys(cells["adm_cd"]))


# ---------------------------------------------------------------- 지난 연휴의 카드 값


def card_periods(blocks: list[tuple[date, date, str]], holiday_names: dict, cutoff: date) -> list[tuple[date, date, str]]:
    """cutoff 까지 끝난 연휴와 하루짜리 공휴일."""
    out = [(s, e, n) for s, e, n in blocks if e <= cutoff]
    inside = {d for s, e, _ in blocks for d in pd.date_range(s, e).date}
    for ts, n in holiday_names.items():
        d = ts.date()
        if d not in inside and d <= cutoff:
            out.append((d, d, n))
    return sorted(out)


def card_gap(card2: pd.DataFrame, city: str, period: tuple[date, date, str], holidays: list,
             rules: dict | None = None) -> pd.DataFrame:
    """연휴 동안 주의 연령의 의료 결제가 같은 성 40·50대보다 평소 대비 얼마나 더 줄었나 (−0.3 이면 30% 더 줄어듦).
    평소 값은 연휴 전 card_base_weeks 주의 같은 요일 평균이고 공휴일은 뺀다."""
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


# ---------------------------------------------------------------- 동절기 칸 (춘천 읍·면 60대 이상)

WINTER_AGES = ["60대+"]


def winter_facts(row, w: pd.Series | None, trigger: str, rules: dict) -> CellFacts:
    """동절기 칸 하나. 그 읍·면 60대 이상의 겨울 체류가 여름보다 기준 이상 줄었으면 겨울 신호가 선다.
    겨울 달이 아직 없으면 부분 판정, 격자가 적어 읍·면 전체 값을 썼으면 신뢰도 사유에 적는다."""
    f = cell_facts(row, trigger, rules)
    if w is None or pd.isna(w.get("w_ok")):
        f.input_status, f.missing_inputs = "부분 판정", ["읍·면 겨울 체류 변화 (겨울 달 공개 전)"]
    else:
        f.axes["W"] = bool(w["w_ok"])
        f.axis_why["W"] = f"60대 이상 겨울 체류 여름 대비 {w['w60']:+.1%}"
        if bool(w["shared"]):
            f.confidence_why.append("겨울 체류: 읍·면 전체 값(공유 맥락)")
    return f


def winter_cells(axes: pd.DataFrame, w: pd.DataFrame, eupmyeon: set[str], trigger: str,
                 rules: dict | None = None) -> pd.DataFrame:
    """춘천 읍·면 60대 이상 칸마다 동절기 등급. Level 1~3 모든 칸을 돌려준다."""
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
