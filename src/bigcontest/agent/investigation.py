"""조사 루프: 관찰 → 계획 → 실행 → 확인 (AG-03, 에이전트 설계 §4.1).

반증(counter_evidence) 뒤에 도는 짧은 루프다. 등급·순서·반증 판정은 이미 규칙이 정했고, 이 루프의 결과는
보고서의 "추가로 확인한 것" 칸에만 들어간다 (등급·반증 입력인 상태 `evidence` 에는 넣지 않고 `investigation_evidence` 에 따로 둠).

- open_questions (코드): 후보마다 아직 풀리지 않은 질문을 **고정 목록(CATALOG)** 에서 만든다. 질문마다 쓸 도구가 정해져 있다
- investigate (LLM): 질문 목록 + 후보 요약을 보고 한 회차에 많아야 max_picks 개를 고르고 이유를 쓴다 (enum 구조화 출력).
  LLM 이 없거나 실패하거나 예산을 넘으면 규칙(우선순위)으로 고른다. 코드가 화이트리스트 도구를 실행한다
- check_findings (코드): 질문마다 풀림(answered) / 여전히 모름(unanswered) / 자료 없음(no_data) / 도구 오류(error)를 정한다.
  못 푼 질문이 있고 남은 질문·회차·예산이 있으면 한 회차 더 (많아야 max_rounds)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from bigcontest.agent.schemas import Candidate, ToolResult

DEVIATION = "unexpected_activity_deviation"
SCHEDULED = "scheduled_access_review"
_HOLIDAY = re.compile(r"연휴|추석|설날|설 |명절|공휴일")
WINTER_MONTHS = (11, 12, 1, 2)
HEAT_MONTHS = (6, 7, 8)
STATUS_KO = {"answered": "풀림", "unanswered": "여전히 모름", "no_data": "자료 없음", "error": "도구 오류",
             "dropped": "Guard 가 뺌", "quarantined": "확인 못 함"}
# 격리 사유 코드 → 짧은 설명 (K-05). 근거 값은 싣지 않는다
QUARANTINE_KO = {"G-08": "작은 칸(인원이 적어 공개하지 않음)", "G-11": "폐기한 지표", "G-14": "지표 뜻 바꾸기",
                 "TOOL_ERROR": "도구 오류", "TOOL_TIMEOUT": "도구 시간 초과", "TOOL_IO": "도구 입출력 오류",
                 "DATA_UNAVAILABLE": "자료 없음"}


@dataclass(frozen=True)
class Q:
    qid: str
    label: str
    question: str  # {where} 자리에 도시·동
    tools: tuple[str, ...]  # 앞에서부터 쓸 수 있는 첫 도구
    priority: int  # 작을수록 먼저 (규칙 고르기)
    metric_hint: str | None = None  # 이 말이 든 지표의 근거만 답으로 봄
    per_dong: bool = False  # 동마다 질문 하나 (아니면 도시마다)


CATALOG: tuple[Q, ...] = (
    Q("HOLIDAY_RECOVERY", "연휴 회복 미확인", "{where}: 지난 연휴 중·뒤 결제가 평소 수준으로 돌아왔는지 아직 모름",
      ("calc_holiday_gap",), 1),
    Q("WEATHER_PART", "날씨로 설명되는 부분", "{where}: 이탈 중 날씨(강수)로 설명되는 부분이 있는지",
      ("calc_weather_effect", "check_confounders"), 1, metric_hint="강수"),
    Q("MEDICAL_ACCESS", "의료 접근", "{where}: 연휴 동안 문을 여는 의료 자원(휴일 운영 약국·병의원·응급)이 가까이 있는지",
      ("find_resources",), 2, per_dong=True),
    Q("WINTER", "동절기 수축", "{where}: 읍·면의 겨울 체류 수축이 이번에도 보이는지", ("calc_winter_contraction",), 2),
    Q("STAY_CHANGE", "먼저 볼 동의 체류 변화", "{where}: 먼저 볼 동이 있는 생활권의 체류 인구 구성", ("get_mobility_layer",), 3),
    Q("HEAT", "폭염 반응", "{where}: 폭염일 고령층 생필품 결제 반응 (탐색)", ("calc_heat_response",), 4),
)
BY_ID = {q.qid: q for q in CATALOG}
MEDICAL_NEED = "의료 접근"
MAX_DONGS = 3  # 동마다 질문은 도시마다 많아야 3곳


def _registered(tool: str, backend: str) -> bool:
    from bigcontest.agent.tools import TOOL_NAMES, _REAL

    if tool == "find_resources":
        return True
    if tool not in TOOL_NAMES:
        return False
    if backend == "stub":
        from bigcontest.agent.tools import stubs

        return tool in stubs._TOOLS
    return tool in _REAL


def tool_for(q: Q, backend: str) -> str | None:
    return next((t for t in q.tools if _registered(t, backend)), None)


def _holiday(c: Candidate) -> bool:
    return bool(_HOLIDAY.search(c.trigger or ""))


def _dong_name(adm: str | None) -> str:
    if not adm:
        return ""
    from bigcontest.agent.tools.report import adm_name

    return adm_name(adm) or adm


def _rural(adm: str | None) -> bool:
    n = _dong_name(adm)
    return n.endswith("읍") or n.endswith("면")


def questions_for(cands: list[Candidate], as_of: date, backend: str, max_open: int = 8) -> list[dict]:
    """후보 → 미해결 질문 목록 (코드, 고정 목록에서만). 같은 질문·도시·동은 하나로 묶고 후보 ID 를 모은다.
    우선순위 → 처음 나온 후보 순서로 정렬해 max_open 개까지."""
    found: dict[str, dict] = {}
    order: list[str] = []

    def add(q: Q, c: Candidate, adm: str | None = None, prio: int | None = None):
        tool = tool_for(q, backend)
        if tool is None:
            return
        key = f"{q.qid}|{c.city}|{adm or ''}"
        if key not in found:
            where = f"{c.city} {_dong_name(adm)}".strip() if adm else c.city
            found[key] = {"key": key, "qid": q.qid, "label": q.label, "question": q.question.format(where=where),
                          "city": c.city, "adm_cd": adm, "tool": tool, "priority": prio or q.priority,
                          "candidate_ids": []}
            order.append(key)
        item = found[key]
        item["priority"] = min(item["priority"], prio or q.priority)
        if c.candidate_id not in item["candidate_ids"]:
            item["candidate_ids"].append(c.candidate_id)

    dongs_per_city: dict[str, set[str]] = {}
    for c in cands:
        dev = c.candidate_type == DEVIATION
        if dev and c.counter_result != "기각":
            add(BY_ID["HOLIDAY_RECOVERY"], c)
        elif not dev and _holiday(c):
            add(BY_ID["HOLIDAY_RECOVERY"], c, prio=2)
        if dev and (c.counter_result == "설명 일부" or re.search(r"강수|날씨", c.counter_reason or "")):
            add(BY_ID["WEATHER_PART"], c)
        adm = c.adm_cd or (c.check_first_adm[0] if c.check_first_adm else None)
        if adm and (_holiday(c) or dev):
            seen = dongs_per_city.setdefault(c.city, set())
            if adm in seen or len(seen) < MAX_DONGS:
                seen.add(adm)
                add(BY_ID["MEDICAL_ACCESS"], c, adm)
        if c.city == "춘천" and as_of.month in WINTER_MONTHS and _rural(c.adm_cd):
            add(BY_ID["WINTER"], c)
        if dev and c.check_first_adm:
            add(BY_ID["STAY_CHANGE"], c)
        if as_of.month in HEAT_MONTHS:
            add(BY_ID["HEAT"], c)
    items = [found[k] for k in order]
    items.sort(key=lambda x: (x["priority"], order.index(x["key"])))
    return items[:max_open]


def rule_picks(questions: list[dict], k: int) -> list[dict]:
    """규칙 고르기: 우선순위 순으로 k 개 (LLM 이 없거나 실패·예산 초과)."""
    return [{"key": q["key"], "why": f"규칙: 우선순위 {q['priority']} ({q['label']})"} for q in questions[:k]]


def clean_picks(raw: Any, questions: list[dict], k: int) -> list[dict]:
    """LLM 이 고른 것 → 목록 안 key 만, 겹치지 않게, k 개까지. 이유는 Guard 표현 검사를 지난 것만 싣는다."""
    from bigcontest.agent import guard as G

    keys = {q["key"] for q in questions}
    out: list[dict] = []
    for p in raw or []:
        if not isinstance(p, dict) or p.get("key") not in keys or any(o["key"] == p["key"] for o in out):
            continue
        why = str(p.get("why") or "").strip()[:160] or None
        bad = G.reason_problems(why)
        if bad:
            why = f"LLM 이유는 Guard 검사({', '.join(bad)})에 걸려 싣지 않음"
        out.append({"key": p["key"], "why": why})
        if len(out) >= k:
            break
    return out


def judge(q: dict, results: list[ToolResult], n_items: int | None = None) -> tuple[str, str, list[str]]:
    """도구 결과 → (상태, 템플릿 결과 문장, 근거 ID). 근거가 없거나 비면 '여전히 모름'·'자료 없음'."""
    hint = BY_ID[q["qid"]].metric_hint
    rel = [r for r in results if not hint or hint in (r.metric or "")] or results
    ids = [r.evidence_id for r in rel]
    if not rel:
        return "no_data", "도구가 근거를 내지 않음", ids
    ok = [r for r in rel if r.status == "OK"]
    if q["tool"] == "find_resources":
        env = rel[0]
        if env.status == "ERROR":
            return "error", f"자원 찾기 오류 ({env.error_code})", ids
        n = n_items if n_items is not None else (env.value if isinstance(env.value, int) else 0)
        if n:
            return "answered", f"{MEDICAL_NEED} 자원 후보 {n}건 (출처·확인일은 근거 표에서 확인)", ids
        return "unanswered", f"{MEDICAL_NEED} 자원 후보 없음 (원장에 없거나 휴일 운영 미확인)", ids
    if ok:
        parts = []
        for r in ok[:2]:
            v = r.value if not isinstance(r.value, float) else f"{r.value:g}"
            parts.append(f"{r.metric} {v}" + (f"({r.unit})" if r.unit else ""))
        return "answered", "; ".join(parts), [r.evidence_id for r in ok]
    if any(r.status == "ERROR" for r in rel):
        bad = next(r for r in rel if r.status == "ERROR")
        return "error", f"{bad.metric} 도구 오류 ({bad.error_code})", ids
    why = next((w for r in rel for w in r.warnings if "stub" not in w and "가짜 도구" not in w), None)
    return "unanswered", f"{rel[0].metric}: 값 없음" + (f" ({why})" if why else ""), ids


def quarantined(codes: list[str]) -> tuple[str, str, list[str]]:
    """답이 될 근거가 모두 신호 Guard 에 걸려 격리됨 → ("quarantined", "확인 못 함" 문장, 근거 ID 없음).
    격리한 근거는 근거 표·보고서에 넣지 않으므로 인용도 없다. 등급·실행 끝 상태는 바뀌지 않는다."""
    why = ", ".join(f"{c} {QUARANTINE_KO.get(c, '검사 실패')}" for c in codes) or "검사 실패"
    return "quarantined", f"확인 못 함: 근거가 검사에 걸려 쓰지 않음 ({why})", []


def section(inv: dict | None, kept_candidates: set[str] | None = None) -> dict | None:
    """상태의 조사 기록 → 보고서 "추가로 확인한 것" (schemas.Investigation 모양).
    kept_candidates 가 있으면 남은 후보(동으로 거른 뒤)와 관련된 질문만."""
    if not inv:
        return None
    items = [i for i in inv.get("items") or []
             if kept_candidates is None or not i.get("candidate_ids") or set(i["candidate_ids"]) & kept_candidates]
    tried = {i["key"] for i in inv.get("items") or []}
    not_picked = [q["label"] + (f" ({q['city']})" if q.get("city") else "") for q in inv.get("questions") or []
                  if q["key"] not in tried][:8]
    fields = ("key", "qid", "label", "question", "city", "adm_cd", "candidate_ids", "tool", "round", "source", "why",
              "status", "finding", "evidence_ids", "reason_code")
    return {"rounds": len(inv.get("rounds") or []), "items": [{k: i.get(k) for k in fields} for i in items],
            "not_picked": not_picked, "stopped": inv.get("stopped"),
            "note": "추가로 확인한 것은 등급·순서·반증 판정에 쓰지 않음 (참고용)"}
