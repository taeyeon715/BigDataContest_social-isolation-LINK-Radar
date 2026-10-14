"""그래프 상태 (에이전트 설계 §2).

- 필드는 `schemas.RunState` 와 같다. 다만 체크포인터(SQLite)에 그대로 저장되도록 Pydantic 모델 대신
  JSON 으로 바꾼 dict·list·str 로 담는다 (날짜는 ISO 문자열). 노드는 꺼낼 때 Pydantic 으로 다시 검증한다.
- 골격 단계에서만 쓰는 칸(`EXTRA_FIELDS`)을 더했다: 가짜 도구 시나리오, LLM 변형, 지나온 노드, 끝점 결과 등.
"""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, TypedDict

from bigcontest.agent.schemas import RUN_STATE_FIELDS


class AgentState(TypedDict, total=False):
    # ---- 에이전트 설계 §2 (schemas.RunState 와 같은 이름, JSON 형태)
    run_id: str
    mode: str  # monitoring / qa
    question: str | None
    as_of: str  # ISO 날짜
    analysis_mode: str
    snapshot_id: str
    request: dict | None  # NormalizedRequest
    route: str  # lookup / investigate / out_of_scope
    contract_check: dict | None  # ContractResult
    evidence: list[dict]  # ToolResult
    candidates: list[dict]  # Candidate
    resources: list[dict]  # ResourceRecommendation 모양 (G-07 전에는 칸이 빠질 수 있음)
    evidence_card: dict | None  # EvidenceCard. 근거 대신 evidence_ids (card_of 로 펼침)
    report: dict | None  # Report. 카드 대신 card_id (report_of 로 펼침)
    guard: list[dict]  # GuardResult
    revise_count: dict[str, int]
    extra_rounds: int
    evidence_revision: int
    report_revision: int
    decision: str | None  # approved / held / more
    abstain_reason: str | None
    errors: list[dict[str, str]]
    # ---- 골격 단계 추가 칸
    scenario: str  # 가짜 도구 시나리오 (tools.stubs.SCENARIOS)
    llm: str  # "none" 또는 가짜 LLM 변형 (llm.VARIANTS)
    selection_budget: int  # 검토 예산 K
    path: Annotated[list[str], operator.add]  # 지나온 노드 (graph.py 가 붙임)
    tools_run: list[str]  # 이번 수집에서 실행한 도구
    llm_log: list[dict]  # LLM 호출 기록
    verdict: str | None  # 마지막 Guard 판정 (분기용)
    approval: dict | None  # 마지막 승인 요청 처리 결과
    final_record: dict | None  # finalize 기록 (확정 / 보류)
    published: dict | None  # publish_candidates 결과
    answer: dict | None  # 답변 / 보류 답변
    tools_backend: str  # real (고정 스냅샷의 실제 도구) / stub (가짜 도구)
    monitor_cities: list[str] | None  # 정기 감시 도시 (RunCreateRequest.city, None = 두 도시)
    run_meta_dir: str | None  # 실행 메타데이터 저장 폴더 (없으면 저장 안 함)
    run_meta: dict | None  # write_run_metadata 결과
    allow_scenario: bool  # R4·R6 상황 시연(미래 실측 사용) 허용. 없으면 끔
    decision_trace: list[dict]  # 노드별 판단 기록 (schemas.TraceStep). graph._node 가 붙임
    tool_timings: list[dict]  # 도구 호출마다 {tool, ms, status, n, round}
    focus: dict | None  # 추가 조사 범위 (schemas.MoreFocus, 메모 원문은 plan_more 가 쓰고 지움)
    extra_plan: dict | None  # 다음 회차 도구 계획 {tools, counter_checks, candidate_ids, source, reason}
    prev_round: dict | None  # 지난 회차 요약 (근거 열쇠 → 값, 후보 열쇠 → 등급, 도구)
    review: dict | None  # 담당자 검토 (자원 판정·확인한 근거·검토 ID). 메모 원문은 담지 않음
    # ---- G 추가 칸
    scope_candidate_ids: list[str] | None  # 후보로 연 조사: 이 후보만 남김 (등급은 도시 전체 근거로 정한 뒤 거름)
    extra_runs: list[dict]  # 추가 도구 실행 기록 {tool, city, requested}. 다음 회차에도 다시 돌려 근거가 말없이 빠지지 않게
    requested_evidence_ids: list[str]  # 담당자가 추가 조사로 요청한 도구가 낸 근거 (후보 카드에 넣음)
    place_scope: dict | None  # 질문한 동으로 거른 결과 {adm_cds, names, city, city_total, kept} (등급은 도시 전체로)
    # ---- H4 조사 루프 · Guard 처치
    investigation: dict | None  # 조사 루프 기록 {extra_round, questions, rounds, items, stopped} (investigation.py)
    investigation_evidence: list[dict]  # 조사 루프 도구가 낸 근거. 등급·반증 입력(evidence)과 따로 둔다
    evidence_tools: dict[str, str]  # 근거 ID → "도구|도시" (Guard 처치: 실패한 도구만 다시)
    remedy: dict | None  # 마지막 Guard 처치 {remedy, rule_ids, targets}


EXTRA_FIELDS = (
    "scenario", "llm", "selection_budget", "path", "tools_run", "llm_log",
    "verdict", "approval", "final_record", "published", "answer",
    "tools_backend", "monitor_cities", "run_meta_dir", "run_meta", "decision_trace",
    "tool_timings", "focus", "extra_plan", "prev_round", "review", "scope_candidate_ids",
    "extra_runs", "requested_evidence_ids", "place_scope",
    "investigation", "investigation_evidence", "evidence_tools", "remedy",
)
DEFAULT_TOOLS_BACKEND = "real"
STUB_SNAPSHOT_ID = "snap-stub-0001"


def default_snapshot_id(tools_backend: str) -> str:
    """실행 시작 때 고정할 스냅샷. real 이면 지금 dev 스냅샷 (자료가 없으면 dev-unavailable → 도구가 ERROR)."""
    if tools_backend == "stub":
        return STUB_SNAPSHOT_ID
    from bigcontest.agent.tools.datasource import DataUnavailable, dev_snapshot_id

    try:
        return dev_snapshot_id()
    except DataUnavailable:
        return "dev-unavailable"


def initial_state(
    run_id: str,
    mode: str,
    as_of: date | str,
    question: str | None = None,
    analysis_mode: str = "simulated_replay",
    snapshot_id: str | None = None,
    scenario: str = "normal",
    llm: str = "none",
    selection_budget: int = 2,
    tools_backend: str = DEFAULT_TOOLS_BACKEND,
    city: str | None = None,
    run_meta_dir: str | None = None,
    scope_candidate_ids: list[str] | None = None,
) -> dict[str, Any]:
    """실행 시작 상태 (API 가 정하는 칸 + 기본값). city 는 정기 감시 도시 (None = 두 도시)."""
    if mode not in ("monitoring", "qa"):
        raise ValueError(f"mode 는 monitoring / qa: {mode}")
    if tools_backend not in ("real", "stub"):
        raise ValueError(f"tools_backend 는 real / stub: {tools_backend}")
    snapshot_id = snapshot_id or default_snapshot_id(tools_backend)
    return {
        "run_id": run_id,
        "mode": mode,
        "question": question,
        "as_of": str(as_of),
        "analysis_mode": analysis_mode,
        "snapshot_id": snapshot_id,
        "request": None,
        "contract_check": None,
        "evidence": [],
        "candidates": [],
        "resources": [],
        "evidence_card": None,
        "report": None,
        "guard": [],
        "revise_count": {"guard_signal": 0, "guard_report": 0},
        "extra_rounds": 0,
        "evidence_revision": 0,
        "report_revision": 0,
        "decision": None,
        "abstain_reason": None,
        "errors": [],
        "scenario": scenario,
        "llm": llm,
        "selection_budget": selection_budget,
        "path": [],
        "tools_run": [],
        "llm_log": [],
        "verdict": None,
        "approval": None,
        "final_record": None,
        "published": None,
        "answer": None,
        "tools_backend": tools_backend,
        "monitor_cities": [city] if city else None,
        "run_meta_dir": str(run_meta_dir) if run_meta_dir else None,
        "run_meta": None,
        "decision_trace": [],
        "tool_timings": [],
        "focus": None,
        "extra_plan": None,
        "prev_round": None,
        "review": None,
        "scope_candidate_ids": list(scope_candidate_ids) if scope_candidate_ids else None,
        "extra_runs": [],
        "requested_evidence_ids": [],
        "place_scope": None,
        "investigation": None,
        "investigation_evidence": [],
        "evidence_tools": {},
        "remedy": None,
    }


# ---------------------------------------------------------------- 근거 카드·보고서 한 번만 담기
#
# 근거(ToolResult)는 상태의 `evidence` 에만 둔다. `evidence_card` 에는 근거 대신 근거 ID 목록(`evidence_ids`)을,
# `report` 에는 카드 대신 카드 ID(`card_id`)만 둔다. 카드·보고서 전체가 필요하면 card_of·report_of 로 다시 짠다.
# 예전 체크포인트(카드·보고서 안에 근거가 통째로 들어 있음)도 그대로 읽는다.


def pack_card(card) -> dict:
    """EvidenceCard → 상태용 dict (근거 대신 근거 ID)."""
    d = card.model_dump(mode="json")
    d["evidence_ids"] = [e["evidence_id"] for e in d.pop("evidence")]
    return d


def pack_report(report) -> dict:
    """Report → 상태용 dict (카드 대신 카드 ID). 카드는 상태의 evidence_card."""
    d = report.model_dump(mode="json")
    d["card_id"] = d.pop("evidence_card")["card_id"]
    return d


def card_dict(state: dict) -> dict | None:
    """상태의 카드(근거 ID) + 상태 근거 → 카드 전체 dict. 예전 모양(근거가 들어 있음)은 그대로."""
    packed = state.get("evidence_card")
    if not packed:
        return None
    if "evidence" in packed:
        return packed
    by_id: dict[str, dict] = {}
    # 등급 입력 근거 + 조사 루프 근거 ("추가로 확인한 것"에 인용한 것만 카드에 들어 있음)
    for e in list(state.get("evidence") or []) + list(state.get("investigation_evidence") or []):
        by_id.setdefault(e["evidence_id"], e)
    ids = list(packed.get("evidence_ids") or [])
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise KeyError(f"상태 근거에 없는 카드 근거 ID: {missing[:5]}")
    out = {k: v for k, v in packed.items() if k != "evidence_ids"}
    out["evidence"] = [by_id[i] for i in ids]
    return out


def card_of(state: dict):
    """상태의 근거 카드 (EvidenceCard, 없으면 None)."""
    from bigcontest.agent.schemas import EvidenceCard

    d = card_dict(state)
    return EvidenceCard(**d) if d else None


def report_dict(state: dict) -> dict | None:
    """상태의 보고서 전체 dict (카드 포함). API 가 보고서 판을 저장할 때 씀. 예전 모양은 그대로."""
    rep = state.get("report")
    if not rep:
        return None
    if "evidence_card" in rep:
        return rep
    card = card_dict(state)
    if card is None or card.get("card_id") != rep.get("card_id"):
        raise KeyError(f"보고서 카드 {rep.get('card_id')} 가 상태 카드와 다름")
    return {**{k: v for k, v in rep.items() if k != "card_id"}, "evidence_card": card}


def report_of(state: dict):
    """상태의 보고서 (Report, 없으면 None)."""
    from bigcontest.agent.schemas import Report

    d = report_dict(state)
    return Report(**d) if d else None


# 설계 §2 칸이 모두 있는지 (schemas.RunState 와 어긋나면 import 할 때 바로 알 수 있게)
_missing = set(RUN_STATE_FIELDS) - set(AgentState.__annotations__)
assert not _missing, f"AgentState 에 없는 RunState 칸: {_missing}"
