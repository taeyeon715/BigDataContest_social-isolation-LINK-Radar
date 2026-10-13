"""후보 규칙 (configs/rules.yaml): 구조 순위, 진입, event_id, 연휴 뒤 기대 경로, 검토 배정 순서, 대응 종류.

구조 순위: 순위·분모는 도시 × 기준월의 행정동 **전체**에서 정한다. 실제 도구(get_structure)는 조회 필터와
무관하게 전체 모집단 순위를 근거 경고 "순위 모집단: ..."에 적고, 여기서는 그 값을 읽는다. 그 표시가 없는 근거(가짜 도구·
단위 시험)만 받은 근거 안에서 도시별로 순위를 매긴다.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd

from bigcontest.contracts import cohorts_config
from bigcontest.rules import LEVEL_ORDER, load_rules
from bigcontest.rules.levels import StructFact

RANK_PREFIX = "순위 모집단:"
_RANK_RE = re.compile(r"순위 모집단: (?P<pop>.+?) · 도시 안 (?P<rank>\d+)/(?P<n>\d+)위")


def rank_warning(population: str, rank: int, n: int) -> str:
    return f"{RANK_PREFIX} {population} · 도시 안 {rank}/{n}위"


def population_rank(warnings: list[str]) -> tuple[str, int, int] | None:
    for w in warnings:
        m = _RANK_RE.search(w)
        if m:
            return m.group("pop"), int(m.group("rank")), int(m.group("n"))
    return None


def rank_population(values: dict[str, float]) -> dict[str, tuple[int, int]]:
    """adm → (순위, 동 수). 값 큰 순, 같으면 adm_cd 작은 순 (결정적)."""
    order = sorted(values.items(), key=lambda x: (-x[1], x[0]))
    return {a: (i + 1, len(order)) for i, (a, _) in enumerate(order)}


def top_cut(n: int, rules: dict) -> int:
    s = rules["thresholds"]["structure"]
    return max(1, math.ceil(s["top_share"] * n)) if n >= s["small_n"] else n


def structure_facts(items: list[tuple[str, str | None, float, list[str]]], rules: dict | None = None
                    ) -> dict[str, StructFact]:
    """구조 근거 [(adm, 도시, 값, 경고)] → adm → StructFact (상위 여부 포함). 도시마다 따로 순위."""
    rules = rules or load_rules()
    s = rules["thresholds"]["structure"]
    out: dict[str, StructFact] = {}
    by_city: dict[str | None, list] = {}
    for it in items:
        by_city.setdefault(it[1], []).append(it)
    for city, its in by_city.items():
        local = rank_population({a: v for a, _, v, _ in its})
        for adm, _, v, warns in its:
            pr = population_rank(warns)
            if pr:
                pop, rank, n = pr
            else:
                rank, n = local[adm]
                pop = f"{city or '도시'} 받은 근거 {n}개 동"
            top = rank <= top_cut(n, rules) and v >= s["abs_min"]
            out[adm] = StructFact(adm, v, rank, n, pop, top)
    return out


# ---------------------------------------------------------------- event_id·진입


TRIGGER_CODE = re.compile(r"^(R\d|W)")


def trigger_code(text: str) -> str | None:
    m = TRIGGER_CODE.match(text or "")
    return m.group(1) if m else None


def event_id(city: str, code: str, start: date) -> str:
    """같은 사건 = 같은 event_id. 동절기는 겨울 하나 (11~2월)."""
    if code == "W":
        y = start.year if start.month >= 11 else start.year - 1
        return f"{city}:W:{y}"
    return f"{city}:{code}:{start:%Y%m%d}"


def primary_trigger(codes: list[str], rules: dict | None = None) -> int:
    """트리거 여럿일 때 event_id 를 정하는 트리거의 위치 (rules.yaml trigger_priority)."""
    prio = (rules or load_rules())["candidate_types"]["scheduled_access_review"]["trigger_priority"]
    return min(range(len(codes)), key=lambda i: prio.index(codes[i]) if codes[i] in prio else len(prio))


def candidate_key(c) -> tuple:
    """같은 사건으로 한 번만: (event_id, 도시, 동, 분석 코호트, 종류)."""
    return (c.event_id, c.city, c.adm_cd, c.analysis_cohort, c.candidate_type)


def new_only(candidates: list, seen: set[tuple]) -> list:
    """이미 만든(같은 event_id) 후보는 빼고 새 후보만. seen 을 갱신한다."""
    out = []
    for c in candidates:
        k = candidate_key(c)
        if c.event_id and k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


# ---------------------------------------------------------------- 연휴 뒤 기대 경로


def expected_path(block: tuple[date, date, str], holidays: set[date], rules: dict | None = None
                  ) -> tuple[date, list[date]]:
    """연휴 (시작, 끝, 이름) → (기대 경로 마지막 날, 회복을 볼 날들). 공휴일·주말 아닌 날로 셈."""
    hp = (rules or load_rules())["thresholds"]["holiday_path"]
    start, end, _ = block
    n_days = hp["long_path_days"] if (end - start).days + 1 >= hp["long_block_days"] else hp["short_path_days"]
    d, path = end, []
    while len(path) < n_days:
        d += timedelta(days=1)
        if d not in holidays:
            path.append(d)
    path_end = path[-1] if path else end
    rec, d = [], path_end
    while len(rec) < hp["recover_days"]:
        d += timedelta(days=1)
        if d not in holidays:
            rec.append(d)
    return path_end, rec


def post_path_review(E: np.ndarray, L: np.ndarray, dates: pd.DatetimeIndex, j: int, block, holidays: set[date],
                     cutoff: date, rules: dict | None = None) -> tuple[str, str]:
    """도시 공통 하락('확인 필요') 경보를 기대 경로로 다시 본다. (판정, 사유).

    - "기대 경로 안": 경로가 끝난 뒤 회복일 평균이 평소 수준(absolute ≥ tol)이고 도시 대비 추가 이탈도 없음 → 기각 가능
    - "경로 뒤 낮음": 경로가 지나도 평소보다 낮음 → 확인 필요 그대로
    - "경로 뒤 미관측": 기준일(자른 날)까지 회복일을 다 못 봄 → 확인 필요 그대로
    """
    hp = (rules or load_rules())["thresholds"]["holiday_path"]
    path_end, rec = expected_path(block, holidays, rules)
    span = f"{block[2]} 연휴 끝({block[1]:%m/%d}) 뒤 기대 경로 {path_end:%m/%d}까지"
    if not rec or rec[-1] > cutoff:
        return "경로 뒤 미관측", f"{span}, 경로 뒤 회복 미관측 ({cutoff}까지 자료)"
    idx = [i for i, d in enumerate(dates) if d.date() in rec]
    if len(idx) < len(rec):
        return "경로 뒤 미관측", f"{span}, 경로 뒤 관측일 부족"
    m_abs, m_loc = float(np.nanmean(E[idx, j])), float(np.nanmean(L[idx, j]))
    tol = hp["recover_tol"]
    nums = f"경로 뒤 {len(rec)}일 평균 평소 대비 {np.expm1(m_abs):+.1%}, 도시 대비 {np.expm1(m_loc):+.1%}"
    if m_abs >= tol and m_loc >= tol:
        return "기대 경로 안", f"{span}의 도시 공통 하락이고 경로 뒤 평소 수준 회복 ({nums})"
    return "경로 뒤 낮음", f"{span}이 지나도 평소보다 낮음 ({nums})"


# ---------------------------------------------------------------- 대응 종류 · 검토 배정


def group_of_label(label: str | None) -> str | None:
    """서비스 대상 집단 표시(예: 20~34세 남성) → cohorts.yaml 집단 이름."""
    if not label:
        return None
    for name, g in cohorts_config()["groups"].items():
        if label in (name, g["service"]["label"]):
            return name
    return None


def response_type(level: str | None, group_label: str | None, rules: dict | None = None) -> str | None:
    rt = (rules or load_rules())["response_types"]
    if level is None:
        return None
    g = group_of_label(group_label)
    over = rt.get("by_group", {}).get(g) if g else None
    if over and level in over["levels"]:
        return over["type"]
    return rt["by_level"].get(level)


PRIORITY = {"Level 3": 0, "Level 2": 1, "판단 보류": 2, "Level 1": 3, "Level 0": 4}
CITY_ORDER = {"강남": 0, "춘천": 1}
TYPE_ORDER = {"scheduled_access_review": 0, "unexpected_activity_deviation": 1}


def queue_key(c, struct_value: dict[str, float]) -> tuple:
    adm = c.adm_cd or (c.check_first_adm[0] if c.check_first_adm else None)
    v = struct_value.get(adm) if adm else None
    return (PRIORITY.get(c.candidate_level or "Level 0", 9), CITY_ORDER.get(c.city, 9), TYPE_ORDER.get(c.candidate_type, 9),
            c.service_eligibility_group or "", -(v if v is not None else -1.0), adm or "~", c.candidate_id)


def assign_queue(candidates: list, struct_value: dict[str, float], k: int, rules: dict | None = None) -> list:
    """정렬(rules.yaml selection.order) → queue_rank, K 안의 Level 2·3 만 selected. 등급은 바꾸지 않음.
    돌려주는 목록은 candidate_id 순."""
    eligible = set((rules or load_rules())["selection"]["eligible_levels"])
    ordered = sorted(candidates, key=lambda c: queue_key(c, struct_value))
    out = []
    for rank, c in enumerate(ordered, start=1):
        c = c.model_copy(update={"queue_rank": rank, "selection_budget": k,
                                 "selected_for_review": rank <= k and c.candidate_level in eligible})
        out.append(c)
    return sorted(out, key=lambda c: c.candidate_id)


def level_at_least(level: str | None, floor: str) -> bool:
    return level in LEVEL_ORDER and LEVEL_ORDER[level] >= LEVEL_ORDER[floor]
