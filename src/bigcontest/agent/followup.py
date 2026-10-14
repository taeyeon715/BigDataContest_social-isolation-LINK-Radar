"""이어 묻기: 이미 끝난 실행의 근거 카드 위에서만 답한다.

- 새 도구를 부르지 않고 그래프 상태도 바꾸지 않는다 (읽기 전용).
- 개인 지목 질문은 intake 와 같은 규칙(guard.individual_target)으로 LLM 없이 막는다.
- 질문에 적힌 대상(후보 ID·동·성별·연령·집단·등급)은 엄격한 조건이다. 카드에 맞는 후보가 없으면
  "이 카드에 맞는 대상이 없습니다"(NO_TARGET)로 답하고 다른 후보를 대신 설명하지 않는다.
  대상 조건이 없는 일반 질문만 검토 배정 순으로 답한다.
- 핵심 문장은 템플릿(후보 문장 + 그 후보가 쓴 근거의 사실 문장)이고, LLM 은 설명 문장만 쓴다.
  LLM 은 카드 안 근거 ID 만 인용할 수 있다 (sanitize_sentences 로 미리 거르고, 보고서 Guard 가 다시 확인).
- 같은 보고서 Guard(사실 검사·표현 검사)를 지나야 답한다. 걸리면 LLM 문장을 빼고 템플릿만으로 한 번 더,
  그래도 걸리면 판단 보류.
"""

from __future__ import annotations

import re
from typing import Any

from bigcontest.agent import guard as G
from bigcontest.agent.llm import call_llm, card_payload, get_llm_safe, sanitize_sentences
from bigcontest.agent.schemas import DISCLAIMER, Candidate, EvidenceCard, Report

_CAND_ID = re.compile(r"C-\d{8}-\d{3}")
_LEVEL = re.compile(r"Level\s*([0-3])")
MAX_CANDIDATES = 3
MAX_FACTS = 6


def _abstain(reason: str, needed: list[str], rev: int | None, guard: list | None = None, log=None) -> dict:
    return {"status": "abstained", "answer": None, "cited_evidence_ids": [], "source": None, "report_revision": rev,
            "guard": guard or [], "abstain_reason": reason, "needed_data": needed, "llm_log": log or []}


def adm_names(card: EvidenceCard) -> dict[str, str]:
    """근거 적용 범위 "춘천 남면 (32010350)" → {32010350: 남면}."""
    out: dict[str, str] = {}
    for e in card.evidence:
        m = re.search(r"(\S+)\s*\((\d{8})\)", e.applied_scope or "")
        if m:
            out[m.group(2)] = m.group(1)
    return out


# 질문의 집단 말 → 서비스 대상 집단 (configs/cohorts.yaml groups 와 같은 이름)
_GROUP_WORDS = {"고령층": "65세 이상", "어르신": "65세 이상", "노인": "65세 이상",
                "중장년": "40~64세 남성", "청년": "20~34세 남성"}
_GROUP_LABELS = ("65세 이상", "40~64세 남성", "20~34세 남성", "50~69세 남성")
_SEX_WORDS = {"여성": "여성", "여자": "여성", "할머니": "여성", "남성": "남성", "남자": "남성", "할아버지": "남성"}
_DECADE = re.compile(r"(\d)0\s*대(\+)?")
NO_TARGET = "NO_TARGET"


def _dong_names(card: EvidenceCard) -> dict[str, str]:
    """행정동 코드 → 이름. 근거 카드의 적용 범위 + ref/adm_cd_names.csv (카드에 없는 동도 이름으로 찾게)."""
    from bigcontest.agent.tools.report import _adm_names

    return {**_adm_names(), **adm_names(card)}


def _decades(text: str | None) -> set[int]:
    """"60대"→{60}, "80대+"→{80}, "60대+"→{60,70,80} (카드 연령 구간 끝은 80)."""
    out: set[int] = set()
    for m in _DECADE.finditer(text or ""):
        lo = int(m.group(1)) * 10
        out |= set(range(lo, 90, 10)) if m.group(2) and lo < 80 else {lo}
    return out


def _range_decades(label: str | None) -> set[int]:
    """서비스 대상 집단 이름의 나이 범위 → 10 단위. "65세 이상"→{60,70,80}, "40~64세 남성"→{40,50,60}."""
    label = label or ""
    if m := re.search(r"(\d+)\s*~\s*(\d+)\s*세", label):
        lo, hi = int(m.group(1)), int(m.group(2))
    elif m := re.search(r"(\d+)\s*세\s*이상", label):
        lo, hi = int(m.group(1)), 89
    else:
        return set()
    return {d for d in range(0, 90, 10) if d <= hi and d + 9 >= lo}


def _cand_decades(c: Candidate) -> set[int]:
    return _decades(c.analysis_cohort) or _range_decades(c.service_eligibility_group)


def _cand_sexes(c: Candidate) -> set[str]:
    """후보 집단의 성별. 분석 집단·서비스 대상 집단 이름에 성별이 없으면 둘 다 (남녀 모두 든 집단)."""
    found = set(re.findall(r"남성|여성", f"{c.analysis_cohort or ''} {c.service_eligibility_group or ''}"))
    return found or {"남성", "여성"}


def target_conditions(question: str, card: EvidenceCard) -> dict[str, set]:
    """질문에 적힌 대상 조건 (모두 엄격하게 씀): 후보 ID·동·성별·연령(10 단위)·집단·등급."""
    from bigcontest.agent.places import find_dongs

    names = _dong_names(card)
    # 동 이름 사전으로 찾음 (긴 이름 먼저·낱말 경계, 줄임 "역삼동" → 역삼1동·역삼2동 둘 다 조건)
    found = find_dongs(question)
    hit = {nm for nm, _, _ in found}
    codes = {cd for _, cds, _ in found for cd in cds}
    groups = {g for g in _GROUP_LABELS if g in question} | {g for w, g in _GROUP_WORDS.items() if w in question}
    return {
        "ids": set(_CAND_ID.findall(question)),
        "dongs": codes | {cd for cd, nm in names.items() if nm in hit},
        "dong_names": hit,
        "sexes": {v for w, v in _SEX_WORDS.items() if w in question},
        "decades": _decades(question),
        "groups": groups,
        "group_words": {g for g in _GROUP_LABELS if g in question} | {w for w in _GROUP_WORDS if w in question},
        "levels": {f"Level {m}" for m in _LEVEL.findall(question)} | ({"판단 보류"} if "보류" in question else set()),
    }


def _fits(c: Candidate, t: dict[str, set]) -> bool:
    if t["ids"] and c.candidate_id not in t["ids"]:
        return False
    if t["dongs"] and not t["dongs"] & {c.adm_cd, *c.check_first_adm}:
        return False
    if t["sexes"] and not t["sexes"] <= _cand_sexes(c):
        return False
    if t["decades"] and not t["decades"] & _cand_decades(c):
        return False
    if t["groups"]:
        grp = c.service_eligibility_group
        if grp:
            if grp not in t["groups"]:
                return False
        elif not any(_range_decades(g) & _cand_decades(c)
                     and set(re.findall(r"남성|여성", g)) <= _cand_sexes(c) for g in t["groups"]):
            return False  # 집단 이름이 없는 후보는 나이 범위·성별로 맞춤 (중장년·청년 집단은 남성)
    if t["levels"] and c.candidate_level not in t["levels"]:
        return False
    return True


def pick_candidates(question: str, cands: list[Candidate], card: EvidenceCard) -> list[Candidate]:
    """질문이 가리키는 후보.

    질문에 적힌 후보 ID·동 이름·성별·연령·집단·등급은 모두 엄격한 조건이다. 조건을 모두 맞는 후보만 고르고,
    맞는 후보가 없으면 빈 목록 (다른 후보로 대신 답하지 않음 → answer_followup 이 "대상 없음"으로 답함).
    동은 후보의 adm_cd 와 먼저 확인할 동(공유 맥락) 모두에서 찾는다. 대상 조건이 하나도 없을 때만 검토 배정 순."""
    t = target_conditions(question, card)
    pool = [c for c in cands if _fits(c, t)]
    ranked = sorted(pool, key=lambda c: (not c.selected_for_review, c.queue_rank or 999, c.candidate_id))
    return ranked[:MAX_CANDIDATES]


def _target_text(t: dict[str, set]) -> str:
    """질문에서 읽은 대상 조건을 질문의 말 그대로 (예: 청년·여성·Level 3)."""
    parts = (sorted(t["ids"]) + sorted(t["dong_names"]) + sorted(t["group_words"])
             + [f"{d}대" for d in sorted(t["decades"])] + sorted(t["sexes"]) + sorted(t["levels"]))
    return "·".join(parts)


def answer_followup(values: dict, question: str, llm_name: str | None = None) -> dict[str, Any]:
    """{status, answer, cited_evidence_ids, source, report_revision, guard, abstain_reason, needed_data, llm_log}."""
    from bigcontest.agent.nodes import INDIVIDUAL_CODE, NEEDED_DATA, candidate_sentence

    rev = values.get("report_revision")
    if G.individual_target(question):
        return _abstain(f"{INDIVIDUAL_CODE}: {G.rules()['individual']['message']}",
                        [NEEDED_DATA[INDIVIDUAL_CODE]], rev)
    from bigcontest.agent.state import report_dict

    report = report_dict(values) or {}
    if not report.get("evidence_card"):
        return _abstain("EMPTY: 이 실행에는 근거 카드가 없음", ["근거 카드가 있는 실행 (조사·조회가 끝난 실행)"], rev)
    if report.get("is_abstain"):  # 보류 보고서의 후보·등급을 근거처럼 풀어 주지 않음
        from bigcontest.agent.nodes import needed_data

        return _abstain(f"판단 보류된 보고서라 이어 묻기로 답하지 않음: {values.get('abstain_reason') or '판단 보류'}"[:300],
                        needed_data(values), rev)
    card = EvidenceCard(**report["evidence_card"])
    cands = [Candidate(**c) for c in values.get("candidates") or []]
    card_cities = {c.city for c in cands} | set(G._cities_in(" ".join(e.applied_scope or "" for e in card.evidence)))
    asked = G._cities_in(question)
    if asked and card_cities and not asked <= card_cities:
        return _abstain(f"OUT_OF_SCOPE: 이 실행의 근거 카드에 없는 도시 ({'·'.join(sorted(asked - card_cities))})",
                        ["그 도시를 묻는 새 실행 (이어 묻기는 이 실행 근거 안에서만 답함)"], rev)

    t = target_conditions(question, card)
    picked = pick_candidates(question, cands, card)
    asked_target = any(t.values()) if cands else bool(t["ids"] or t["levels"])
    if not picked and asked_target:
        # 질문한 대상이 이 카드에 없음: 다른 후보로 대신 답하지 않는다
        return _abstain(f"{NO_TARGET}: 이 카드에 맞는 대상이 없습니다 ({_target_text(t)}). "
                        "다른 동·집단은 새 조사로 확인하세요."[:300],
                        ["그 대상을 묻는 새 조사 (이어 묻기는 이 카드의 후보 안에서만 답함)"], rev)
    want = {i for c in picked for i in c.evidence_ids}
    facts = [f for f in card.facts if f.evidence_id in want][:MAX_FACTS] if picked else card.facts[:MAX_FACTS]
    template = [candidate_sentence(c) for c in picked] + [G.render_fact(f) for f in facts]
    if not template:
        template = ["이 실행의 근거 카드에는 질문에 맞는 후보·사실이 없음"]
    if card.time_label:
        template.append(card.time_label)
    template.append(DISCLAIMER)

    llm, init_rec = get_llm_safe(llm_name)
    view = card.model_copy(update={"facts": facts})
    payload = card_payload(view, [c.model_dump(mode="json") for c in picked])
    lines, rec = call_llm(llm, "followup", llm.followup if llm else None, G.mask_pii(question)[0], payload)
    narrative = sanitize_sentences(list(lines or []), card, values.get("candidates") or [])
    log = [r for r in (init_rec, rec) if r]

    guard: list[dict] = []
    for attempt, narr in enumerate([narrative, []] if narrative else [[]], start=1):
        rep = Report(report_revision=max(1, rev or 1), evidence_card=card, fact_sentences=template,
                     narrative="\n".join(narr) or None)
        f = G.check_facts(rep, card, attempt=attempt, candidates=values.get("candidates") or [])
        e = G.check_expression(rep, card, attempt=attempt)
        guard += [f.model_dump(mode="json"), e.model_dump(mode="json")]
        if f.verdict == "PASS" and e.verdict == "PASS":
            text = template[:-1] + narr + template[-1:]
            cited = list(dict.fromkeys([x.evidence_id for x in facts]
                                       + [i for s in narr for i in G._EV_ID.findall(s)]))
            return {"status": "answered", "answer": "\n".join(text), "cited_evidence_ids": cited,
                    "source": "llm" if narr else "template", "report_revision": rev, "guard": guard,
                    "abstain_reason": None, "needed_data": [], "llm_log": log}
    reasons = [r for g in guard[-2:] for r in g.get("reasons", [])]
    return _abstain("G-05: 이어 묻기 답이 보고서 Guard 를 통과하지 못함: " + "; ".join(reasons)[:300],
                    [NEEDED_DATA["G-05"]], rev, guard, log)
