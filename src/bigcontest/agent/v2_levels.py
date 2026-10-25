"""assign_level 마지막 단계: 남은 후보를 칸(동 × 성 × 연령) 판정으로 다시 매긴다.

연휴 주의·동절기 단계에서 이미 정한 후보(V2- 규칙)는 건드리지 않는다.
예정된 점검은 후보 집단의 칸마다 decide_v2 를 돌려 가장 높은 칸의 등급을 쓴다. 카드 신호는 연휴 트리거의
연휴 연령 칸에만, 겨울 신호는 동절기 칸에만 붙는다. 동이 없는 도시 단위 후보는 Level 1.
카드 이탈 후보는 같은 성·연령에서 행동 고립 상위인 동을 먼저 볼 동으로 고르고, 있으면 Level 2, 없으면 Level 1.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from bigcontest.agent.schemas import Candidate, ToolResult
from bigcontest.agent.tools import report as R
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.cell_axes import axes_at
from bigcontest.agent.tools.structural_risk import GROUPS
from bigcontest.holiday_watch import cell_facts, watch_config
from bigcontest.rules.levels_v2 import cells_text, decide_v2, load_rules_v2, trigger_text, why_not3

RANK = {"Level 0": 0, "Level 1": 1, "Level 2": 2, "Level 3": 3}
HOLIDAY = ("R1", "R2", "R3")
CARD_AGE = {"20대": "20대", "30대": "30대", "40대": "40대", "50대": "50대", "60대": "60대+", "70대": "60대+", "80대+": "60대+"}
FIRST_N = 3


def _card_gaps(evidence: list[ToolResult]) -> dict[tuple[str, str], float]:
    """(도시, 성) → 지난 연휴 60대 이상 의료 결제 40·50대 대비 (get_holiday_watch, 비율)."""
    out = {}
    for e in evidence:
        if e.metric == S.WATCH_CARD_METRIC and e.status == "OK" and isinstance(e.value, (int, float)):
            sex = "남" if (e.analysis_cohort or "").endswith("남성") else "여"
            out[(S.city_of_evidence(e), sex)] = float(e.value) / 100
    return out


def _winter(ds, as_of: date, mode: str) -> pd.DataFrame:
    from bigcontest.agent.tools.common import usable_months
    from bigcontest.winter import winter_by_area

    months = [m.base_month for m in usable_months("tel_age", as_of, mode)]
    w = ds.cached(("winter_by_area", tuple(months)),
                  lambda: winter_by_area(ds.tel("tel_age"), ds.tel("tel_time"), ds.tel("tel_wkdy"), months))
    return w.set_index("adm_cd") if len(w) else pd.DataFrame()


def _group(c: Candidate, req_group: str | None) -> str | None:
    if req_group in GROUPS:
        return req_group
    g = R.group_key(c)
    return g if g in GROUPS else None


def _cell_decisions(c: Candidate, ax: pd.DataFrame, group: str, card: dict, w: pd.DataFrame, rules: dict):
    sexes, ages, _ = GROUPS[group]
    code = (c.trigger or "")[:2]
    rows = ax[(ax["adm_cd"] == c.adm_cd) & ax["sex"].isin(sexes) & ax["age5"].isin(ages)]
    target = watch_config(rules)["target_ages"]
    out = []
    for _, r in rows.iterrows():
        gap = card.get((c.city, r["sex"])) if code in HOLIDAY and r["age5"] in target else None
        f = cell_facts(r, c.trigger or "-", rules, gap)
        if code.startswith("W") and len(w) and c.adm_cd in w.index:
            f.axes["W"] = bool(w.loc[c.adm_cd, "w_ok"])
            f.axis_why["W"] = f"60대 이상 겨울 체류 여름 대비 {w.loc[c.adm_cd, 'w60']:+.1%}"
        out.append((r, decide_v2(f, rules)))
    return out


def _unverified_note(r, d) -> str:
    if "CAP-SIGNAL-UNVERIFIED" not in d.caps:
        return ""
    from bigcontest.rules.signal_check import AXIS_KO, unverified

    names = "·".join(AXIS_KO[a] for a in unverified(r["sex"], r["age5"]) if a in d.axes_on)
    return f"{r['sex']}성 {r['age5'].replace('+', ' 이상')}의 {names} 신호는 방향 미확인이라 Level 3 근거에서 뺌"


def _reason(cells, trigger: str | None) -> str:
    """칸 판정 결과 → 등급 사유 문장."""
    r, d = max(cells, key=lambda x: (RANK.get(x[1].level, 0), len(x[1].axes_on)))
    parts = [cells_text([(rr["sex"], rr["age5"], dd.level, dd.axes_on) for rr, dd in cells]), trigger_text(trigger)]
    if d.level == "Level 2":
        parts.append(_unverified_note(r, d) or why_not3(d.caps))
    elif d.level == "Level 1":
        parts.append("동을 가리키는 통신 신호(행동 고립·겨울 체류 감소)가 없어 Level 1")
    return ". ".join(p for p in parts if p)


def relevel_scheduled(cands: list[Candidate], evidence: list[ToolResult], ds, as_of: date, mode: str,
                      req_group: str | None = None) -> list[Candidate]:
    rules = load_rules_v2()
    ax = axes_at(ds, as_of, mode)
    card = _card_gaps(evidence)
    w = _winter(ds, as_of, mode) if any((c.trigger or "").startswith("W") for c in cands) else pd.DataFrame()
    out = []
    for c in cands:
        if c.candidate_type != "scheduled_access_review" or (c.rule_id or "").startswith("V2-"):
            out.append(c)
            continue
        if c.adm_cd is None:
            out.append(c.model_copy(update={
                "candidate_level": "Level 1", "rule_id": "V2-L1-CITY", "rule_version": "rules-v2.0", "rule_checks": [],
                "level_reason": "동을 가리키지 않는 도시 단위 후보라 칸별로 판정하지 않음 [rules-v2.0]"}))
            continue
        group = _group(c, req_group)
        cells = _cell_decisions(c, ax.df, group, card, w, rules) if ax is not None and group else []
        if not cells:
            out.append(c.model_copy(update={
                "candidate_level": "Level 1", "rule_id": "V2-L1-NO-CELL", "rule_version": "rules-v2.0", "rule_checks": [],
                "level_reason": "기준일에 쓸 수 있는 통신·주민등록 자료가 없어 신호를 셀 수 없음 [rules-v2.0]"}))
            continue
        _, d = max(cells, key=lambda x: (RANK.get(x[1].level, 0), len(x[1].axes_on)))
        out.append(c.model_copy(update={
            "candidate_level": d.level, "rule_id": d.rule_id + "".join(f"+{x}" for x in d.caps),
            "rule_version": "rules-v2.0", "rule_checks": [], "level_reason": _reason(cells, c.trigger)}))
    return out + _new_scheduled(out, evidence, ds, ax, card, w, rules, as_of, req_group)


def _new_scheduled(cands, evidence, ds, ax, card, w, rules, as_of, req_group) -> list[Candidate]:
    """후보가 없는 동 중 칸 판정이 Level 2 이상인 동을 같은 트리거의 후보로 새로 만든다. 동절기는 읍·면만."""
    if ax is None:
        return []
    have = {(c.city, c.adm_cd, c.trigger) for c in cands if c.candidate_type == "scheduled_access_review"}
    tmpls: dict[tuple[str, str], Candidate] = {}
    for c in cands:
        if c.candidate_type == "scheduled_access_review" and c.adm_cd and c.trigger:
            tmpls.setdefault((c.city, c.trigger), c)
    city_level = {e.evidence_id for e in evidence if S.adm_of(e) is None}
    used = {c.candidate_id for c in cands}
    out = []
    for (city, trig), t in tmpls.items():
        group = _group(t, req_group)
        if group is None:
            continue
        adms = sorted(ax.df.loc[ax.df["city"] == city, "adm_cd"].unique())
        if trig.startswith("W"):
            adms = [a for a in adms if ds.adm_index[a]["adm_type"] in ("읍", "면")]
        n = S.ID_BASE.get(city, 0) + 200
        for adm in adms:
            if (city, adm, trig) in have:
                continue
            probe = t.model_copy(update={"adm_cd": adm})
            cells = _cell_decisions(probe, ax.df, group, card, w, rules)
            if not cells:
                continue
            _, d = max(cells, key=lambda x: (RANK.get(x[1].level, 0), len(x[1].axes_on)))
            if RANK.get(d.level, 0) < 2:
                continue
            while (cid := f"C-{as_of:%Y%m%d}-{n:03d}") in used:
                n += 1
            used.add(cid)
            out.append(probe.model_copy(update={
                "candidate_id": cid, "context": [], "queue_rank": None, "selected_for_review": False,
                "input_status": "충족", "missing_inputs": [],
                "evidence_ids": [i for i in t.evidence_ids if i in city_level],
                "candidate_level": d.level, "rule_id": d.rule_id + "".join(f"+{x}" for x in d.caps),
                "rule_version": "rules-v2.0", "rule_checks": [],
                "level_reason": _reason(cells, trig)}))
    return out


def relevel_deviation(cands: list[Candidate], ds, as_of: date, mode: str) -> list[Candidate]:
    rules = load_rules_v2()
    ax = axes_at(ds, as_of, mode)
    out = []
    for c in cands:
        if c.candidate_type != "unexpected_activity_deviation":
            out.append(c)
            continue
        if c.candidate_level == "판단 보류" or c.counter_result == "기각" or (c.rule_id or "").startswith("L1-DEV-SMALL"):
            # 보류·기각·작은 이탈은 v1.1 과 같은 판정이고 최소 하락폭만 v2 값. 사유 꼬리표의 판만 바꾼다
            out.append(c.model_copy(update={"rule_version": "rules-v2.0",
                                            "level_reason": (c.level_reason or "").replace("[rules-v1.1 ", "[rules-v2.0 ")}))
            continue
        parts = (c.analysis_cohort or "").split()
        age = CARD_AGE.get(parts[0]) if parts else None
        sex = parts[1][0] if len(parts) > 1 else None
        first: list[str] = []
        if ax is not None and age and sex in ("남", "여"):
            g = ax.df[(ax.df["city"] == c.city) & (ax.df["sex"] == sex) & (ax.df["age5"] == age)
                      & ax.df["h_top"].fillna(False).astype(bool) & ~ax.df["s_small"]].copy()
            g["ext"] = g["s_top"].astype(int) + g["v_counts"].astype(int) + g["a_top"].astype(int)
            first = g.sort_values(["ext", "h"], ascending=[False, False])["adm_cd"].head(FIRST_N).tolist()
        if first:
            names = "·".join(R.adm_name(a) or a for a in first)
            upd = {"candidate_level": "Level 2", "rule_id": "V2-L2-DEV-DATA+CAP-SHARED-CONTEXT",
                   "level_reason": f"{c.analysis_cohort} 카드 결제 이탈이 다른 원인으로 설명되지 않음. 먼저 볼 동({names})은 "
                                   "같은 성·연령에서 행동 고립 상위. 카드는 도시 단위라 Level 2까지 [rules-v2.0]"}
        else:
            upd = {"candidate_level": "Level 1", "rule_id": "L1-DEVIATION-ONLY",
                   "level_reason": f"{c.analysis_cohort} 카드 결제 이탈이 다른 원인으로 설명되지 않지만, 같은 성·연령에서 "
                                   "행동 고립 상위인 동이 없음 [rules-v2.0]"}
        out.append(c.model_copy(update={**upd, "check_first_adm": first, "is_shared_context": True,
                                        "rule_version": "rules-v2.0", "rule_checks": []}))
    return out
