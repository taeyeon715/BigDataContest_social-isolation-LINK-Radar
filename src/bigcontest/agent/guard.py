"""Guard: 신호·조회 Guard 와 보고서 Guard (에이전트 설계 §6, 규칙은 configs/guard_rules.yaml).

- 신호·조회 Guard (`check_signal`): G-06, G-08(작은 칸: 인원 칸 + 비율의 분모), G-11, G-13(근거 시점), G-14
- 보고서 Guard 는 둘로 나눈다
  - 사실 검사 (`check_facts`, G-05): 보고서 문장에서 (지표, 지역, 기간, 값, 단위, 방향, 근거 ID) 묶음을 만들어
    근거 카드의 사실과 하나씩 대조한다. 숫자만 뽑아 대조하지 않는다.
  - 표현 검사 (`check_expression`): G-03, G-04, G-09, G-10, G-12, G-13(표시), G-14. 거주지 주장은 contracts.claims.check_claim
- 자원 검사 (`check_resources`, G-07): 출처·지역·대상·확인일이 없는 자원은 빼고 경고 (판정은 PASS)
- 판정 합치기·REVISE 한도는 `decide`
- 개인 지목 질문 표지(`individual_target`)와 LLM 에 보내기 전 개인정보 가리기(`mask_pii`)도 여기 둔다 (규칙은 같은 yaml)
"""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

from bigcontest.agent.schemas import (
    ASSUMED_AVAILABILITY_LABEL,
    CandidateType,
    EvidenceCard,
    Fact,
    GuardResult,
    Report,
    ToolResult,
    Verdict,
)
from bigcontest.contracts import load_yaml
from bigcontest.contracts.claims import check_claim

_ORDER = {"PASS": 0, "REVISE": 1, "ABSTAIN": 2}
# contracts.claims 가 아는 원천
KNOWN_SOURCES = {"tel_age", "tel_time", "tel_wkdy", "card1", "card2", "resident_pop", "resident_hh", "seoul_living",
                 "asos", "resources"}
NO_DIRECTION = "방향 없음"


@lru_cache
def rules() -> dict:
    return load_yaml("guard_rules.yaml")


def rule_verdict(rule_id: str, analysis_mode: str | None = None) -> Verdict:
    """규칙 → 판정 (guard_rules.yaml 의 verdict / verdict_by_mode)."""
    r = rules()["rules"][rule_id]
    if "verdict_by_mode" in r:
        return r["verdict_by_mode"][analysis_mode or "simulated_replay"]
    return r["verdict"]


def worst(verdicts) -> Verdict:
    out = "PASS"
    for v in verdicts:
        if _ORDER[v] > _ORDER[out]:
            out = v
    return out


class _Hits:
    """규칙 위반 모으기 → GuardResult."""

    def __init__(self, guard: str, check: str, analysis_mode: str | None = None):
        self.guard, self.check, self.mode = guard, check, analysis_mode
        self.items: list[tuple[str, Verdict, str]] = []  # (규칙, 판정, 사유)
        self.notes: list[str] = []  # PASS 에 붙는 경고 (G-07)

    def add(self, rule_id: str, reason: str, verdict: Verdict | None = None) -> None:
        v = verdict or rule_verdict(rule_id, self.mode)
        if (rule_id, v, reason) not in self.items:
            self.items.append((rule_id, v, reason))

    def result(self, attempt: int = 1) -> GuardResult:
        verdict = worst(v for _, v, _ in self.items)
        rule_ids = list(dict.fromkeys(r for r, v, _ in self.items if r))
        reasons = [f"{r}: {why}" if r else why for r, _, why in self.items] + self.notes
        return GuardResult(guard=self.guard, check=self.check, verdict=verdict, rule_ids=rule_ids, reasons=reasons, attempt=attempt)


# ---------------------------------------------------------------- 사실 문장 (템플릿 ↔ 묶음)


def fmt_value(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def render_fact(f: Fact) -> str:
    """근거 카드 사실 → 핵심 사실 문장 (템플릿). parse_fact 로 다시 묶음을 만들 수 있어야 한다."""
    return (
        f"{f.metric} {fmt_value(f.value)}({f.unit or '단위 없음'}) — "
        f"{f.region}, {f.period}, {f.direction or NO_DIRECTION} [{f.evidence_id}]."
    )


_FACT_RE = re.compile(
    r"^(?P<metric>.+?) (?P<value>-?\d+(?:\.\d+)?)\((?P<unit>[^)]*)\) — "
    r"(?P<region>[^,]+), (?P<period>[^,]+), (?P<direction>[^\[]+?) \[(?P<eid>EV-[0-9A-Za-z\-]+)\]\.?$"
)
_EV_ID = re.compile(r"EV-[0-9A-Za-z\-]+")


def parse_fact(sentence: str) -> Fact | None:
    """사실 문장 → (지표, 지역, 기간, 값, 단위, 방향, 근거 ID). 형식이 다르면 None."""
    m = _FACT_RE.match(sentence.strip())
    if not m:
        return None
    raw = m.group("value")
    value: float | int = float(raw) if "." in raw else int(raw)
    unit = m.group("unit")
    direction = m.group("direction").strip()
    if direction == NO_DIRECTION:
        direction = None
    elif direction not in ("감소", "증가", "변화 없음"):
        direction = direction  # 형식 밖 방향은 그대로 두고 대조에서 걸림
    return Fact.model_construct(
        metric=m.group("metric").strip(),
        region=m.group("region").strip(),
        period=m.group("period").strip(),
        value=value,
        unit=None if unit == "단위 없음" else unit,
        direction=direction,
        evidence_id=m.group("eid"),
    )


def _same_value(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return a == b


def compare_fact(got: Fact, card: EvidenceCard) -> list[str]:
    """보고서 사실 하나를 근거 카드와 대조. 다른 칸 이름 목록 (빈 목록이면 같음)."""
    want = next((f for f in card.facts if f.evidence_id == got.evidence_id), None)
    if want is None:
        return ["근거 ID"]
    diffs = []
    for name in ("metric", "region", "period", "unit", "direction"):
        if getattr(got, name) != getattr(want, name):
            diffs.append(name)
    if not _same_value(got.value, want.value):
        diffs.append("value")
    return diffs


# 설명 문장에서 사실이 아닌 숫자: 규칙·근거·후보 번호, 날짜.
# 등급(Level N)과 트리거(R1, D-7)는 면제하지 않는다: 등급은 후보 ID 와 묶어 실제 등급과 대조하고, 트리거는
# 실제 후보의 트리거 이름에 있는 것만 허용
_NON_FACT_NUM = re.compile(
    r"G-\d+|EV-[0-9A-Za-z\-]+|C-\d{8}-\d+|\d{4}-\d{2}(-\d{2})?|\d{8}"
)
_CAND_ID = re.compile(r"C-\d{8}-\d+")
_LEVEL = re.compile(r"Level\s*([0-9])")


def _tokens_of(evs) -> set[str]:
    """근거의 코호트·대상 집단·트리거 이름 (숫자가 든 이름 속 숫자는 새 숫자로 보지 않음)."""
    toks: set[str] = set()
    for e in evs:
        for t in (e.analysis_cohort, e.service_eligibility_group):
            if t:
                toks.add(t)  # 정확한 이름만 ("60대·70대·80대+" 에서 "60대"만 떼어 쓰지 못함)
        if isinstance(e.value, str) and re.search(r"\d", e.value) and len(e.value) <= 60:
            toks.add(e.value)
    return toks


def _cand(c) -> dict:
    return c if isinstance(c, dict) else c.model_dump(mode="json")


def _trigger_tokens(cands: list[dict]) -> set[str]:
    toks: set[str] = set()
    for c in cands:
        t = c.get("trigger")
        if t:
            toks.add(t)
            toks.update(x for x in t.split() if re.search(r"\d", x))
    return toks


def _allowed_tokens(card: EvidenceCard, sentence: str | None = None, candidates: list | None = None) -> list[str]:
    """설명 문장에 나와도 '새 숫자'로 보지 않는 이름 (긴 것부터).
    - 문장이 근거 ID 를 인용하면 그 근거들의 코호트·대상 집단·트리거 이름만
    - 인용이 없으면 대상 집단 이름(service_eligibility_group)만
    sentence 를 주지 않으면 카드 전체 (예전 동작, 참고용)."""
    if sentence is None:
        toks = _tokens_of(card.evidence)
    else:
        cited = set(_EV_ID.findall(sentence))
        evs = [e for e in card.evidence if e.evidence_id in cited]
        if evs:
            toks = _tokens_of(evs)
        else:
            toks = {e.service_eligibility_group for e in card.evidence if e.service_eligibility_group}
    # 트리거 이름(R1, D-7): 문장이 인용한 후보의 것, 인용이 없으면 이 실행 후보들의 것 (정확한 이름만).
    # 인용한 후보의 코호트 이름도 허용
    cands = [_cand(c) for c in candidates or []]
    if sentence is not None and cands:
        cited_c = set(_CAND_ID.findall(sentence))
        mine = [c for c in cands if c["candidate_id"] in cited_c]
        toks |= _trigger_tokens(mine or cands)
        toks |= {c["analysis_cohort"] for c in mine if c.get("analysis_cohort")}
    # 이름 속 숫자는 늘 허용: 지표 이름("1인세대")과 카드에 있는 동 이름("효자1동"). 동이 맞는지는 지역 대조가 봄
    facts = {f.evidence_id: f for f in card.facts}
    for e in card.evidence:
        toks.update(w for w in re.findall(r"[0-9A-Za-z가-힣+]+", e.metric) if re.search(r"\d", w) and re.search(r"[가-힣]", w))
        toks.update(_places(e, facts.get(e.evidence_id)))
    return sorted(toks, key=len, reverse=True)


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


_SPACED = re.compile(r"(?<![가-힣])[가-힣](?:\s+[가-힣](?![가-힣]))+")


def _collapse(s: str) -> str:
    """한 글자씩 띄어 쓴 말만 붙임 ("절 반" → "절반", "늘 었 습 니 다" → "늘었습니다"). 다른 띄어쓰기는 그대로라
    "취약성과 반증" 이 "과반" 이 되지 않음 (수량·방향 검사용. 표현 검사는 공백을 모두 뺀 문장도 봄)."""
    return _SPACED.sub(lambda m: re.sub(r"\s+", "", m.group(0)), s)


def quantity_words(sentence: str) -> list[str]:
    """한글 수량 말 (절반, 두 배, 열 명 중 세 명, 대부분 …). 공백을 뺀 문장도 본다."""
    out: list[str] = []
    for pat in rules()["narrative"]["quantity_patterns"]:
        for text in (sentence, _collapse(sentence)):
            m = re.search(pat, text)
            if m:
                out.append(m.group(0).strip())
                break
    return list(dict.fromkeys(out))


def _directions(text: str) -> set[str]:
    cfg = rules()["narrative"]["direction_words"]
    return {d for d, pats in cfg.items() if any(re.search(p, text) for p in pats)}


def _cities_in(text: str) -> set[str]:
    """문장에 나온 도시 (별칭 포함: 서울 → 강남, 강원 → 춘천). 동 이름을 먼저 찾아 지우고, 찾은 동의 도시를 더한다
    ("춘천 강남동" → {춘천}, 서울 강남구가 아님. places.cities_in)."""
    from bigcontest.agent.places import cities_in

    cfg = rules()["narrative"]["cities"]
    return cities_in(text, {c: tuple(names) for c, names in cfg.items()})


def _city_of(ev: ToolResult, fact: Fact | None) -> str | None:
    from bigcontest.agent.tools.common import city_of_evidence  # 순환 import 피함

    c = city_of_evidence(ev)
    if c:
        return c
    found = _cities_in(" ".join(x for x in (fact.region if fact else None, ev.applied_scope) if x))
    return next(iter(found)) if len(found) == 1 else None


_PLACE = re.compile(r"[가-힣]+\d*(?:동|읍|면)(?![가-힣])")


def _places(ev: ToolResult, fact: Fact | None) -> set[str]:
    """근거가 가리키는 동·읍·면 이름 (도시 단위 근거면 빈 집합)."""
    text = " ".join(x for x in (ev.applied_scope, fact.region if fact else None) if x)
    return set(_PLACE.findall(text))


_STATE_WORDS = {"기각": lambda c: c.get("counter_result") == "기각",
                "판단 보류": lambda c: c.get("candidate_level") == "판단 보류"}


def _candidate_claims(sentence: str, candidates: list | None) -> tuple[list[str], str]:
    """등급·후보 결론 주장 대조. (문제 목록, 등급 표기를 지운 문장).
    - 등급(Level N)은 후보 ID 를 같은 문장에 인용해야 하고, 인용한 후보의 실제 등급과 같아야 함
    - 인용한 후보 ID 는 이 실행의 후보여야 함
    - "기각"·"판단 보류"는 인용한 후보(인용이 없으면 이 실행 후보 중 하나)의 실제 상태여야 함"""
    out: list[str] = []
    cands = {c["candidate_id"]: c for c in (_cand(x) for x in candidates or [])}
    cited = list(dict.fromkeys(_CAND_ID.findall(sentence)))
    unknown = [i for i in cited if i not in cands]
    if unknown:
        out.append(f"이 실행에 없는 후보 ID {', '.join(unknown)}")
    mine = [cands[i] for i in cited if i in cands]
    levels = {f"Level {m}" for m in _LEVEL.findall(sentence)}
    if levels:
        actual = {c.get("candidate_level") for c in mine}
        if not mine:
            out.append(f"등급({', '.join(sorted(levels))})은 후보 ID 와 같은 문장에서만, 실제 등급과 같게 씀")
        elif not levels <= actual or (len(levels) == 1 and actual != levels):
            out.append(f"등급({', '.join(sorted(levels))})이 인용한 후보의 실제 등급"
                       f"({', '.join(sorted(a or '없음' for a in actual))})과 다름")
    for word, ok in _STATE_WORDS.items():
        if word in sentence:
            pool = mine or list(cands.values())
            if not any(ok(c) for c in pool):
                out.append(f"'{word}'인 후보가 {'인용한 후보 중에' if mine else '이 실행에'} 없음")
    return out, _LEVEL.sub("", sentence)


def narrative_problems(sentence: str, card: EvidenceCard, candidates: list | None = None) -> list[str]:
    """LLM 설명 문장 하나의 G-05 문제 (빈 목록이면 통과). Guard 와 sanitize_sentences 가 같이 쓴다.

    - 카드에 없는 근거 ID 인용
    - 숫자 (코호트·트리거 이름 속 숫자는 그 문장이 인용한 근거의 것만 허용)
    - 한글 수량 말
    - 방향 말(늘었다·줄었다 …)은 같은 문장이 인용한 근거의 방향과 맞아야 함. 인용 근거가 없거나 방향이 없으면 대조할 수
      없어 문제로 봄. 방향 말을 부정한 꼴("줄지 않았다")도 대조할 수 없어 문제
    - 도시 이름은 인용한 근거의 도시와 같아야 함 (인용이 없으면 카드에 있는 도시). 카드에 없을 지역 이름은 문제.
      인용한 근거가 동 단위면 문장에 나온 그 카드의 동 이름도 인용 근거의 동이어야 함
    """
    cfg = rules()["narrative"]
    out: list[str] = []
    ids = {e.evidence_id for e in card.evidence}
    cited_ids = list(dict.fromkeys(_EV_ID.findall(sentence)))
    unknown = [i for i in cited_ids if i not in ids]
    if unknown:
        out.append(f"근거 카드에 없는 근거 ID {', '.join(unknown)}")
    claims, rest = _candidate_claims(sentence, candidates)
    out += claims
    rest = _NON_FACT_NUM.sub("", rest)
    for t in _allowed_tokens(card, sentence, candidates):
        rest = rest.replace(t, "")
    if re.search(r"\d", rest):
        out.append("근거 ID 없는 숫자(설명 문장은 숫자를 쓸 수 없음)")
    q = quantity_words(rest)
    if q:
        out.append(f"한글 수량 말({', '.join(q)})은 숫자로 봄 (설명 문장은 숫자를 쓸 수 없음)")

    body = _EV_ID.sub(" ", sentence)
    facts = {f.evidence_id: f for f in card.facts}
    cited = [e for e in card.evidence if e.evidence_id in cited_ids]
    # 방향
    if re.search(cfg["negated_direction"], body) or re.search(cfg["negated_direction"], _collapse(body)):
        out.append("방향 말을 부정문으로 씀 (근거 방향과 대조할 수 없음)")
    said = _directions(body) | _directions(_collapse(body))
    if said:
        have = {(facts[e.evidence_id].direction if e.evidence_id in facts else e.direction) for e in cited} - {None}
        if not have:
            out.append(f"방향 말({'·'.join(sorted(said))})을 썼지만 방향이 있는 근거를 인용하지 않음")
        elif not said <= have:
            out.append(f"방향 말({'·'.join(sorted(said))})이 인용한 근거의 방향({'·'.join(sorted(have))})과 다름")
    # 지역
    flat = _collapse(body)
    other = [r for r in cfg["other_regions"] if r in flat]
    if other:
        out.append(f"근거 카드에 없는 지역 이름({', '.join(other)})")
    named = _cities_in(flat)
    if named:
        if cited:
            have_c = {_city_of(e, facts.get(e.evidence_id)) for e in cited} - {None}
        else:
            have_c = {_city_of(e, facts.get(e.evidence_id)) for e in card.evidence} - {None}
        if have_c and not named <= have_c:
            out.append(f"도시({'·'.join(sorted(named))})가 {'인용한 근거' if cited else '근거 카드'}의 도시"
                       f"({'·'.join(sorted(have_c))})와 다름")
    cited_places = set().union(*[_places(e, facts.get(e.evidence_id)) for e in cited]) if cited else set()
    if cited_places:
        card_places = set().union(*[_places(e, facts.get(e.evidence_id)) for e in card.evidence])
        wrong = sorted(p for p in card_places - cited_places if p in body and not any(p in c for c in cited_places))
        if wrong:
            out.append(f"동 이름({', '.join(wrong)})이 인용한 근거의 동({', '.join(sorted(cited_places))})과 다름")
    return out


def check_facts(report: Report, card: EvidenceCard, attempt: int = 1, candidates: list | None = None) -> GuardResult:
    """사실 검사 (G-05).

    - 근거 ID 가 있는 핵심 사실 문장(템플릿)은 (지표, 지역, 기간, 값, 단위, 방향, 근거 ID) 묶음으로 근거 카드와 대조
    - LLM 설명 문장은 근거 ID 를 인용만 할 수 있다 (사실 묶음 형식 그대로 쓴 문장은 묶음 대조). 나머지는
      `narrative_problems`: 숫자·한글 수량 말 금지, 방향·도시·동 이름은 인용한 근거와 대조
    """
    hits = _Hits("guard_report", "사실")
    for s in report.fact_sentences:
        if not _EV_ID.search(s) or s.startswith("판단 보류") or s.startswith("후보 "):
            continue
        got = parse_fact(s)
        if got is None:
            hits.add("G-05", f"근거 ID 가 있지만 사실 형식이 아닌 문장: {s}")
            continue
        diffs = compare_fact(got, card)
        if diffs:
            hits.add("G-05", f"근거 카드와 다름({', '.join(diffs)}): {s}")
    for s in split_sentences(report.narrative):
        got = parse_fact(s)
        if got is not None:
            diffs = compare_fact(got, card)
            if diffs:
                hits.add("G-05", f"근거 카드와 다름({', '.join(diffs)}): {s}")
            continue
        for why in narrative_problems(s, card, candidates):
            hits.add("G-05", f"{why}: {s}")
    return hits.result(attempt)


def split_sentences(text: str | None) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in text.split("\n") if s.strip()]


# ---------------------------------------------------------------- 표현 검사


def evidence_source(ev: ToolResult) -> str | None:
    """근거의 원천 (check_claim 용). 실제 도구는 data_version 앞부분(`<원천>@sha256:...`)에 원천을 적는다.
    가짜 도구(data_version=stub)만 지표 이름으로 고른다."""
    dv = ev.data_version or ""
    if "@" in dv:
        src = dv.split("@", 1)[0]
        return src if src in KNOWN_SOURCES else None
    if ev.merchant_region or ev.customer_residence:
        return "card2"
    if "1인세대" in ev.metric:
        return "resident_hh"
    if "체류" in ev.metric:
        return "tel_age"
    if "시민생활" in ev.metric:
        return "seoul_living"
    return None


def check_expression(report: Report, card: EvidenceCard, attempt: int = 1) -> GuardResult:
    """표현 검사: 진단 문장, 거주민 단정, 지표 뜻 바꾸기, 담당자 판단 문구, 시간 표시."""
    cfg = rules()
    hits = _Hits("guard_report", "표현", card.analysis_mode)
    text_lines = list(report.fact_sentences) + split_sentences(report.narrative)
    whole = "\n".join(text_lines)

    # contracts.claims: 원천별 해도 되는/안 되는 주장
    # - 사실 문장은 그 근거의 원천 기준으로
    # - 그 밖 문장(후보·설명)은 카드 근거 기준(거주지·동 단정) + 원천 무관 규칙으로
    ev_by_id = {ev.evidence_id: ev for ev in card.evidence}
    card_ev = next((ev for ev in card.evidence if evidence_source(ev) == "card2"), None)
    n_template = len(report.fact_sentences)
    for k, line in enumerate(text_lines):
        fact = parse_fact(line)
        if fact and fact.evidence_id in ev_by_id:
            ev = ev_by_id[fact.evidence_id]
            checks = [(evidence_source(ev) or "general", ev)]
        else:
            checks = [("general", None)] + ([("card2", card_ev)] if card_ev else [])
        for src, ev in checks:
            res = check_claim(line, src, ev)
            for code, desc in res.violations:
                rule_id = cfg["claim_code_rules"].get(code, "G-14")
                hits.add(rule_id, f"{desc}: {line}")
        template = k < n_template
        for rule_id, r in cfg["rules"].items():
            for pat in r.get("patterns", []):
                if pattern_hit(pat, line, rule_id, template):
                    hits.add(rule_id, f"{r['name']}: {line}")
                    break

    # G-10: 최종 판단을 사람에게
    if cfg["rules"]["G-10"]["required_text"] not in whole:
        hits.add("G-10", "'최종 판단은 담당자' 문장이 없음")

    # G-13: 가정한 공개 시점 표시 / 기준일 뒤 자료
    if card.analysis_mode == "simulated_replay" and ASSUMED_AVAILABILITY_LABEL not in whole:
        hits.add("G-13", "가정한 공개 시점 표시가 없음", "REVISE")
    late = after_as_of(card.evidence, card.as_of)
    if late and card.analysis_mode == "strict_as_of":
        hits.add("G-13", f"기준일 뒤 자료: {', '.join(late)}", "ABSTAIN")
    return hits.result(attempt)


def _negation_exempt(rule_id: str, after: str, template: bool) -> bool:
    """금지 표현 뒤가 부정("~가 아님")이면 위반이 아님. 템플릿 문장에만, 반어·조건 꼴은 빼고."""
    cfg = rules()
    if not template or rule_id not in cfg.get("negation_exempt", []):
        return False
    if re.match(cfg["rhetorical_pattern"], after):
        return False
    if cfg.get("negation_reassert") and re.search(cfg["negation_reassert"], after):
        return False  # 부정한 뒤 다시 단정
    return bool(re.match(cfg["negation_pattern"], after))


def pattern_hit(pat: str, line: str, rule_id: str, template: bool) -> bool:
    """표현 패턴이 문장에 걸리는가. 원문과 공백을 뺀 문장 둘 다 본다 ("거 주 민")."""
    for text in (line, _nospace(line)):
        for m in re.finditer(pat, text):
            if _negation_exempt(rule_id, text[m.end():], template):
                continue
            return True
    return False


def after_as_of(evidence: list[ToolResult], as_of: date) -> list[str]:
    """기준일 뒤에 관측됐거나 공개된 근거 ID."""
    out = []
    for ev in evidence:
        avail = ev.actual_available_at or ev.simulated_available_at
        if (ev.observed_to and ev.observed_to > as_of) or (avail and avail > as_of):
            out.append(ev.evidence_id)
    return out


def check_resources(resources: list[dict]) -> tuple[list[dict], list[str]]:
    """G-07: 출처·지역·대상·확인일이 있는 자원만 남김. (남은 자원, 경고)."""
    need = rules()["rules"]["G-07"]["required_fields"]
    kept, notes = [], []
    for r in resources:
        missing = [k for k in need if not r.get(k)]
        if missing:
            notes.append(f"G-07: 추천 제외 {r.get('resource_id')} ({', '.join(missing)} 없음)")
        else:
            kept.append(r)
    return kept, list(dict.fromkeys(notes))


# ---------------------------------------------------------------- 신호·조회 Guard


def _meaning_claims(ev: ToolResult) -> list[str]:
    """G-14: 근거가 주장하는 내용 중 지표 뜻을 바꾼 것."""
    pats = rules()["rules"]["G-14"]["patterns"]
    return [c for c in ev.valid_claims if any(re.search(p, c) for p in pats)]


def drop_targets(evidence: list[ToolResult]) -> dict[str, str]:
    """처치 drop_evidence 대상: 근거 ID → 규칙 (G-11 폐기 지표, G-14 지표 뜻 바꾸기)."""
    out: dict[str, str] = {}
    for ev in evidence:
        if ev.metric in rules()["deprecated_metrics"]:
            out[ev.evidence_id] = "G-11"
        elif _meaning_claims(ev):
            out[ev.evidence_id] = "G-14"
    return out


def retry_targets(evidence: list[ToolResult]) -> list[str]:
    """처치 retry_failed_tool 대상: 다시 시도할 수 있는 오류(retriable_error_codes)로 실패한 근거 ID."""
    codes = set(rules()["retriable_error_codes"])
    return [ev.evidence_id for ev in evidence if ev.status == "ERROR" and ev.error_code in codes]


def remedy_of(rule_id: str, where: str) -> str:
    """규칙 → 처치 (guard_rules.yaml remedy / remedy_by_where). 모르면 abstain."""
    r = rules()["rules"].get(rule_id) or {}
    return (r.get("remedy_by_where") or {}).get(where) or r.get("remedy") or "abstain"


def pick_remedy(rule_ids: list[str], where: str) -> str:
    """걸린 규칙들 → 처치 하나 (remedy_order 순서로 앞선 것)."""
    order = rules()["remedy_order"]
    found = {remedy_of(r, where) for r in rule_ids if r}
    return next((x for x in order if x in found), "abstain")


def reason_problems(text: str | None) -> list[str]:
    """LLM 이 쓴 짧은 이유(조사 루프 why 등)를 보고서에 싣기 전 검사: 숫자·한글 수량 말(G-05)과 표현 규칙 패턴.
    걸린 규칙 ID 목록 (빈 목록이면 통과)."""
    if not text:
        return []
    out = []
    if re.search(r"\d", _NON_FACT_NUM.sub("", text)) or quantity_words(text):
        out.append("G-05")
    for rule_id, r in rules()["rules"].items():
        if any(pattern_hit(p, text, rule_id, False) for p in r.get("patterns", [])):
            out.append(rule_id)
    return list(dict.fromkeys(out))


def check_signal(
    evidence: list[ToolResult],
    candidates: list[dict],
    analysis_mode: str,
    as_of: date,
    abstain_reason: str | None = None,
    attempt: int = 1,
    extra: list[ToolResult] | None = None,
) -> GuardResult:
    """신호·조회 Guard. candidates 는 Candidate dict (반증 기록 확인용).
    extra 는 조사 루프 근거 (등급 입력 아님): 작은 칸·폐기 지표·지표 뜻 검사만 한다."""
    cfg = rules()
    hits = _Hits("guard_signal", "신호", analysis_mode)
    if abstain_reason:
        hits.add("", abstain_reason, "ABSTAIN")

    for ev in list(evidence) + list(extra or []):
        for rule_id, why in evidence_problems(ev):
            hits.add(rule_id, f"{ev.evidence_id}: {why}")
    for ev in evidence:
        # G-06 다시 하면 풀릴 반증 오류
        if ev.status == "ERROR" and ev.metric.startswith("반증") and ev.error_code in cfg["retriable_error_codes"]:
            hits.add("G-06", f"{ev.evidence_id}: 반증 도구 {ev.error_code} → 실패한 도구만 다시")

    # G-13 기준일 뒤 자료 (strict_as_of 는 ABSTAIN)
    late = after_as_of(evidence, as_of)
    if late and analysis_mode == "strict_as_of":
        hits.add("G-13", f"기준일 뒤 자료: {', '.join(late)}", "ABSTAIN")

    # G-06 이탈 후보는 반증 결과와 사유가 있어야 함
    deviation: CandidateType = "unexpected_activity_deviation"
    for c in candidates:
        if c["candidate_type"] == deviation and (c.get("counter_result") in (None, "해당 없음") or not c.get("counter_reason")):
            hits.add("G-06", f"{c['candidate_id']}: 반증 검토 기록 없음")
    return hits.result(attempt)


def evidence_problems(ev: ToolResult) -> list[tuple[str, str]]:
    """근거 하나의 신호 Guard 문제 (규칙 ID, 사유). check_signal 과 조사 근거 격리(quarantine_reasons)가 같이 쓴다.
    G-08 작은 칸·비공개 값, G-11 폐기 지표, G-14 지표 뜻 바꾸기."""
    cfg = rules()
    out: list[tuple[str, str]] = []
    # G-08 작은 칸: 인원 칸 값, 또는 비율의 분모(사람·세대 수)가 기준보다 작음
    why = small_cell(ev)
    if why:
        out.append(("G-08", why))
    if ev.value_state == "suppressed" and ev.value is not None:
        out.append(("G-08", "비공개 값을 출력하려 함"))
    # G-11 폐기 지표
    if ev.metric in cfg["deprecated_metrics"]:
        out.append(("G-11", f"폐기한 지표 '{ev.metric}'"))
    # G-14 지표 뜻 바꾸기 (근거가 주장하는 내용)
    for claim in _meaning_claims(ev):
        out.append(("G-14", f"'{claim}'"))
    return out


def quarantine_reasons(ev: ToolResult) -> list[tuple[str, str]]:
    """참고용 근거(조사 루프 근거)를 쓰지 않고 격리할 이유 (규칙 ID 또는 오류 코드, 사유). 빈 목록이면 써도 됨.
    신호 Guard 문제(evidence_problems) + 도구 오류(status ERROR)."""
    out = evidence_problems(ev)
    if ev.status == "ERROR":
        out.append((ev.error_code or "TOOL_ERROR", "도구 오류"))
    return out


def small_cell(ev: ToolResult) -> str | None:
    """G-08 작은 칸 사유 (없으면 None). 기준은 guard_rules.yaml min_cell (기본 10).
    - 인원·세대 수 근거(unit 이 count_units): 값이 기준보다 작으면
    - 비율 근거: 분모가 사람·세대 수(small_cell_denominators)인데 그 크기(n_obs)가 기준보다 작으면"""
    cfg = rules()
    th = cfg.get("min_cell", 10)
    if ev.status != "OK":
        return None
    if ev.unit in cfg.get("count_units", ["명"]) and isinstance(ev.value, (int, float)) and not isinstance(ev.value, bool) \
            and ev.value < th:
        return f"인원 {fmt_value(ev.value)}{ev.unit} 칸 (기준 {th} 미만)"
    if ev.n_obs is not None and ev.denominator and ev.n_obs < th \
            and any(k in ev.denominator for k in cfg.get("small_cell_denominators", [])):
        return f"분모 {ev.n_obs} ({ev.denominator}) 인 {ev.unit or '값'} (기준 {th} 미만)"
    return None


# ---------------------------------------------------------------- 개인 지목 질문 · 개인정보 가리기


def _person_names(text: str) -> list[str]:
    """사람 이름 표현 (agent/pii.py: 이름+호칭, 이름+에게·한테·께, 이름+조사+행동 말, 연락 말 옆 세 글자 이름,
    영문 이름, 성+씨). guard_rules.yaml individual.not_names 도 이름이 아닌 말로 본다. 모든 이름을 찾는다고 보장하지 않음."""
    from bigcontest.agent.pii import find_names

    return find_names(text, tuple(rules()["individual"].get("not_names") or ()))


def individual_target(question: str | None) -> list[str]:
    """개인을 찾거나 판정하려는 질문의 표지 (빈 목록이면 아님). LLM 없이 intake 가 씀."""
    if not question:
        return []
    cfg = rules()["individual"]
    found = list(_person_names(question))
    for pat in cfg["patterns"]:
        m = re.search(pat, question)
        if m:
            found.append(m.group(0))
    return list(dict.fromkeys(found))


def mask_pii(text: str | None) -> tuple[str, int]:
    """LLM 에 보내기·저장하기 전에 전화번호·주민번호·이메일·상세 주소·사람 이름(pii.find_names)을 가림.
    (가린 문장, 가린 개수). 규칙 기반이라 모든 개인정보를 지운다고 보장하지 않는다."""
    if not text:
        return text or "", 0
    n = 0
    for item in rules()["pii_mask"]:
        text, k = re.subn(item["pattern"], f"[{item['label']}]", text)
        n += k
    from bigcontest.agent.pii import mask_names

    text, k = mask_names(text, tuple(rules()["individual"].get("not_names") or ()))
    n += k
    return text, n


# ---------------------------------------------------------------- 판정 + 한도


def decide(result: GuardResult | list[GuardResult], revise_used: int, allow_revise: bool = True) -> tuple[Verdict, str | None]:
    """판정 합치기. REVISE 가 한도를 넘거나 REVISE 를 못 하는 경로(조회)면 ABSTAIN. (판정, ABSTAIN 사유)."""
    results = result if isinstance(result, list) else [result]
    verdict = worst(r.verdict for r in results)
    reasons = [x for r in results if r.verdict != "PASS" for x in r.reasons]
    if verdict == "REVISE":
        if not allow_revise:
            return "ABSTAIN", "조회 경로는 다시 수집하지 않음: " + "; ".join(reasons)
        if revise_used >= rules()["max_revise"]:
            return "ABSTAIN", f"REVISE {rules()['max_revise']}회 넘음: " + "; ".join(reasons)
    if verdict == "ABSTAIN":
        return "ABSTAIN", "; ".join(reasons)
    return verdict, None
