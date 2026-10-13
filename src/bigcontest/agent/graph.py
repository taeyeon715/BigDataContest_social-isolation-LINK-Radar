"""LangGraph 연결 (에이전트 설계 §4). 노드 로직은 nodes.py, 여기는 연결·분기·interrupt 만.

끝점 다섯: 답변(`answer`), 보류 답변(`abstain_answer`), 후보 목록 발행(`publish_candidates`),
확정 기록·보류 기록(`finalize` 가 결정에 따라 기록).

LangGraph API (langgraph 1.2.x)
- `interrupt(value)`: await_approval 에서 멈춤. 재개하면 노드를 처음부터 다시 실행하고 interrupt 가 응답 값을 돌려줌
- `Command(resume=응답)`: 재개
- 체크포인터: `SqliteSaver`, config `{"configurable": {"thread_id": run_id}}`

이미 있는 run_id 로 다른 요청을 `start` 하면 `RunAlreadyExists` (같은 요청이면 다시 실행하지 않고 지금 상태). "추가 조사(more)" 재개는 멱등 키로 한 번만
(`run_resume_keys` 표에 키를 먼저 차지). 확정·보류는 finalize 단계의 기록이 1건만 남긴다.
승인 대기에서 추가 조사는 plan_more(담당자 focus → 도구 계획) → collect_signals, 검토 반영은
record_review → write_report. 끝점·승인 대기 직전에 노드·도구 소요 시간을 run_meta["perf"] 로 합침.
노드마다 소요 시간을 `bigcontest.agent` 로거에 JSON 칸(run_id, node, ms)으로 남긴다 (질문 원문은 안 남김).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from bigcontest.agent import nodes
from bigcontest.agent.checkpoint import RecordStore, open_checkpointer
from bigcontest.agent.state import DEFAULT_TOOLS_BACKEND, AgentState, initial_state
from bigcontest.runmeta import check_run_id

ENDPOINTS = ("answer", "abstain_answer", "publish_candidates", "finalize")
log = logging.getLogger("bigcontest.agent")
KST = timezone(timedelta(hours=9))


class RunAlreadyExists(ValueError):
    """같은 run_id 로 다른 요청을 start 함 (체크포인트 thread 가 섞이지 않게 막음)."""


# 같은 요청인지 볼 때 비교하는 시작 칸
_START_KEYS = ("mode", "question", "as_of", "analysis_mode", "snapshot_id", "scenario", "llm", "selection_budget",
               "tools_backend", "scope_candidate_ids")  # monitor_cities 는 노드가 채우므로 비교하지 않음


def _node(name: str, fn):
    """노드 함수 결과에 지나온 노드 이름과 판단 기록(decision_trace)을 붙이고, 소요 시간을 로그로 남김.

    판단 기록 한 칸: 노드, 고른 것, 분기 노드면 다음 노드, 규칙 ID 또는 LLM 이유, 입력 요약(질문 원문 없음), 시작 시각, ms.
    interrupt 로 멈춘 승인 대기는 재개해서 끝났을 때만 기록된다. 상태에는 최근 MAX_TRACE 칸만 둔다."""

    def run(state: dict) -> dict:
        started = datetime.now(KST).isoformat(timespec="milliseconds")
        t0 = time.perf_counter()
        upd = fn(state) or {}
        ms = (time.perf_counter() - t0) * 1000
        log.debug("node", extra={"run_id": state.get("run_id"), "node": name, "ms": round(ms, 1)})
        router = ROUTERS.get(name)
        try:
            nxt = router({**state, **upd}) if router else None
        except (KeyError, TypeError):
            nxt = None
        step = nodes.trace_step(name, state, upd, nxt, started, ms)
        trace = (list(state.get("decision_trace") or []) + [step])[-nodes.MAX_TRACE:]
        out = {**upd, "path": [name], "decision_trace": trace}
        if name in PERF_AT:  # 끝점·승인 대기 직전에 노드·도구 소요 시간을 run_meta 에 합침
            meta = {**(state.get("run_meta") or {}), **(upd.get("run_meta") or {})}
            out["run_meta"] = {**meta, "perf": nodes.perf_summary({**state, **upd, "decision_trace": trace})}
        return out

    run.__name__ = name
    return run


def _await_approval(state: dict) -> dict:
    resp = interrupt(nodes.approval_request(state))
    return nodes.apply_approval(state, resp)


# ---------------------------------------------------------------- 분기


def after_intake(state: dict) -> str:
    return "check_availability" if state["mode"] == "monitoring" else "classify"


def after_classify(state: dict) -> str:
    return "abstain_report" if state.get("route") == "out_of_scope" else "check_availability"


def after_availability(state: dict) -> str:
    if state.get("abstain_reason"):
        return "abstain_report"
    return "lookup" if state.get("route") == "lookup" else "collect_signals"


def after_guard_signal(state: dict) -> str:
    v = state.get("verdict")
    if v == "ABSTAIN":
        return "abstain_report"
    if v == "REVISE":
        return "collect_signals"
    return "build_card" if state.get("route") == "lookup" else "assign_level"


def after_guard_report(state: dict) -> str:
    v = state.get("verdict")
    if v == "ABSTAIN":
        return "abstain_report"
    if v == "REVISE":
        return "write_report"
    if state.get("route") == "lookup":
        return "answer"
    return "publish_candidates" if state["mode"] == "monitoring" else "await_approval"


def after_abstain(state: dict) -> str:
    if state.get("route") in ("lookup", "out_of_scope"):
        return "abstain_answer"
    return "publish_candidates" if state["mode"] == "monitoring" else "await_approval"


def after_approval(state: dict) -> str:
    if (state.get("approval") or {}).get("status") == "review":  # 검토 반영 → 보고서 다시 만들기
        return "record_review"
    d = state.get("decision")
    if d is None:  # 승인 거절(오래된 버전 등) → 다시 대기
        return "await_approval"
    if d == "more":
        return "plan_more"
    return "finalize"


def after_review(state: dict) -> str:
    return "abstain_report" if (state.get("report") or {}).get("is_abstain") else "write_report"


# 분기 노드 → 분기 함수 (판단 기록에 다음 노드를 남김)
ROUTERS = {
    "intake": after_intake,
    "classify": after_classify,
    "check_availability": after_availability,
    "guard_signal": after_guard_signal,
    "guard_report": after_guard_report,
    "abstain_report": after_abstain,
    "await_approval": after_approval,
    "record_review": after_review,
}
PERF_AT = set(ENDPOINTS) | {"guard_report", "abstain_report"}


# ---------------------------------------------------------------- 그래프


def build_graph(checkpointer=None, store: RecordStore | None = None):
    g = StateGraph(AgentState)
    plain = [
        "intake", "classify", "check_availability", "lookup", "collect_signals", "classify_candidate",
        "counter_evidence", "guard_signal", "assign_level", "assign_queue", "match_resources", "build_card",
        "write_report", "guard_report", "abstain_report", "answer", "abstain_answer", "plan_more", "record_review",
    ]
    for name in plain:
        g.add_node(name, _node(name, getattr(nodes, name)))
    g.add_node("publish_candidates", _node("publish_candidates", lambda s: nodes.publish_candidates(s, store)))
    g.add_node("finalize", _node("finalize", lambda s: nodes.finalize(s, store)))
    g.add_node("await_approval", _node("await_approval", _await_approval))

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", after_intake, ["classify", "check_availability"])
    g.add_conditional_edges("classify", after_classify, ["abstain_report", "check_availability"])
    g.add_conditional_edges("check_availability", after_availability, ["abstain_report", "lookup", "collect_signals"])
    g.add_edge("lookup", "guard_signal")
    g.add_edge("collect_signals", "classify_candidate")
    g.add_edge("classify_candidate", "counter_evidence")
    g.add_edge("counter_evidence", "guard_signal")
    g.add_conditional_edges("guard_signal", after_guard_signal,
                            ["abstain_report", "collect_signals", "build_card", "assign_level"])
    g.add_edge("assign_level", "assign_queue")
    g.add_edge("assign_queue", "match_resources")
    g.add_edge("match_resources", "build_card")
    g.add_edge("build_card", "write_report")
    g.add_edge("write_report", "guard_report")
    g.add_conditional_edges("guard_report", after_guard_report,
                            ["abstain_report", "write_report", "answer", "publish_candidates", "await_approval"])
    g.add_conditional_edges("abstain_report", after_abstain, ["abstain_answer", "publish_candidates", "await_approval"])
    g.add_conditional_edges("await_approval", after_approval, ["await_approval", "plan_more", "record_review", "finalize"])
    g.add_edge("plan_more", "collect_signals")
    g.add_conditional_edges("record_review", after_review, ["write_report", "abstain_report"])
    for end in ENDPOINTS:
        g.add_edge(end, END)
    return g.compile(checkpointer=checkpointer)


def config_for(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 100}


class Agent:
    """그래프 + SQLite 체크포인터 + 멱등 기록. API 가 이 객체를 쓴다."""

    def __init__(self, db_path: str | Path, tools_backend: str = DEFAULT_TOOLS_BACKEND, durability: str | None = None):
        """durability: 조사(qa) 실행의 체크포인트 저장 시점 (None = LangGraph 기본 async, 노드마다).
        정기 감시는 승인 대기가 없어 언제나 "exit" (끝날 때 한 번 저장)."""
        self.db_path = str(db_path)
        self.tools_backend = tools_backend
        self.durability = durability
        self.saver = open_checkpointer(self.db_path)
        self.store = RecordStore(self.db_path)
        self.app = build_graph(self.saver, self.store)

    def _invoke(self, inp, run_id: str, mode: str | None = None):
        if mode is None:
            mode = (self.app.get_state(config_for(run_id)).values or {}).get("mode")
        dur = "exit" if mode == "monitoring" else self.durability
        return self.app.invoke(inp, config_for(run_id), **({"durability": dur} if dur else {}))

    def close(self) -> None:
        self.saver.conn.close()
        self.store.close()

    def exists(self, run_id: str) -> bool:
        snap = self.app.get_state(config_for(run_id))
        return bool(snap.values) or bool(snap.next)

    def start(self, run_id: str, mode: str, as_of, **kw) -> dict:
        """실행 시작. 조사 경로면 승인 대기(interrupt)에서 멈춘 상태로 돌아옴.

        이미 있는 run_id: 같은 요청이면 다시 실행하지 않고 지금 상태를 돌려줌(멱등), 다른 요청이면 RunAlreadyExists.
        """
        check_run_id(run_id)
        kw.setdefault("tools_backend", self.tools_backend)
        init = initial_state(run_id, mode, as_of, **kw)
        if self.exists(run_id):
            old = self.app.get_state(config_for(run_id)).values
            if any(old.get(k) != init[k] for k in _START_KEYS):
                raise RunAlreadyExists(f"이미 있는 실행을 다른 요청으로 시작할 수 없음: {run_id}")
            if snap_next := self.app.get_state(config_for(run_id)).next:
                if "await_approval" not in snap_next:  # 도중에 멈춘 실행 (재시작 복구): 남은 노드를 이어서
                    self._invoke(None, run_id, mode)
            return self.status(run_id)
        self._invoke(init, run_id, mode)
        return self.status(run_id)

    def status(self, run_id: str) -> dict:
        snap = self.app.get_state(config_for(run_id))
        return {
            "values": snap.values,
            "waiting": bool(snap.interrupts),
            "interrupt": snap.interrupts[0].value if snap.interrupts else None,
            "next": snap.next,
        }

    def approve(self, run_id: str, decision: str, report_revision: int, approver: str = "담당자",
                idempotency_key: str | None = None, focus: dict | None = None) -> dict[str, Any]:
        """승인 / 보류 / 추가 조사. 같은 실행의 확정·보류 기록은 1건만.

        멱등 규칙 (API 계약과 같음, API 명세 §3.10)
        - 같은 키 + 같은 결정·판 → duplicate (첫 기록 그대로)
        - 같은 키 + 다른 결정·판 → conflict IDEMPOTENCY_KEY_REUSED
        - 확정·보류가 끝난 뒤 다른 키 (반대 결정·다른 판 포함) → conflict RUN_NOT_AWAITING_APPROVAL (기존 기록을 같이 줌)
        - 승인 대기가 아니고 기록도 없음 → not_waiting
        """
        key = idempotency_key or f"{run_id}:{decision}:{report_revision}"
        want = (decision, report_revision)

        def judge(rec: dict) -> dict[str, Any]:
            req = ((rec.get("payload") or {}).get("approval") or {}).get("request") or {}
            if req.get("idempotency_key") == key:
                if (req.get("decision"), req.get("report_revision")) == want:
                    return {"status": "duplicate", "record": rec}
                return {"status": "conflict", "code": "IDEMPOTENCY_KEY_REUSED", "record": rec}
            return {"status": "conflict", "code": "RUN_NOT_AWAITING_APPROVAL", "record": rec}

        used = self.store.get_resume(run_id, key)
        if used is not None and (used["payload"].get("decision"), used["payload"].get("report_revision")) != want:
            return {"status": "conflict", "code": "IDEMPOTENCY_KEY_REUSED", "record": used}
        existing = self.store.get(run_id, "finalize")
        if existing is not None:
            return judge(existing)
        if used is not None:  # 같은 키의 추가 조사는 이미 한 번 재개함
            return {"status": "duplicate", "decision": "more", "record": used}
        if not self.status(run_id)["waiting"]:
            existing = self.store.get(run_id, "finalize")  # 그 사이 다른 승인이 끝났을 수 있음
            return judge(existing) if existing else {"status": "not_waiting"}
        resp = {"decision": decision, "report_revision": report_revision, "approver": approver,
                "idempotency_key": key}
        if focus:
            resp["focus"] = focus
        if decision == "more":
            # 추가 조사는 부작용(도구 재실행)이 있으므로 같은 키의 재개는 한 번만.
            # 확정·보류는 finalize 기록으로 이미 1건만 남는다.
            rec, created = self.store.claim_resume(run_id, key, {k: v for k, v in resp.items() if k != "focus"})
            if not created:
                return {"status": "duplicate", "decision": "more", "record": rec}
        # 이 재개가 만든 상태를 봄 (동시에 다른 재개가 있으면 get_state 는 그쪽 상태일 수 있음)
        values = self._invoke(Command(resume=resp), run_id)
        approval = values.get("approval") or {}
        if approval.get("status") == "rejected":
            return {"status": "rejected", "code": approval.get("code"), "message": approval.get("message")}
        rec = values.get("final_record")
        if decision == "more" and approval.get("decision") == "more":
            return {"status": "accepted", "decision": "more", "report_revision": values.get("report_revision")}
        if rec and rec.get("duplicate"):
            return judge(rec)
        return {"status": "accepted", "decision": approval.get("decision"), "record": rec}

    def review(self, run_id: str, review: dict, report_revision: int | None, key: str) -> dict[str, Any]:
        """담당자 검토 반영: 승인 대기에서 재개 → record_review → 보고서 다시 만듦 → 다시 승인 대기.
        review = {judgements, checked_evidence_ids, follow_up_on, review_ids}. 같은 키는 한 번만."""
        if not self.status(run_id)["waiting"]:
            return {"status": "not_waiting"}
        resp = {"kind": "review", "report_revision": report_revision, "review": review}
        _, created = self.store.claim_resume(run_id, f"review:{key}", {"kind": "review",
                                                                       "report_revision": report_revision})
        if not created:
            return {"status": "duplicate"}
        values = self._invoke(Command(resume=resp), run_id)
        approval = values.get("approval") or {}
        if approval.get("status") == "rejected":
            return {"status": "rejected", "code": approval.get("code"), "message": approval.get("message")}
        return {"status": "accepted", "report_revision": values.get("report_revision")}

    def resume_pending(self, run_id: str, resp: dict) -> dict:
        """재시작 복구: 키를 이미 차지했지만 재개가 끝나지 않은 추가 조사·검토를 다시 이어 감 (API 만 씀)."""
        if not self.status(run_id)["waiting"]:
            return self.status(run_id)
        self._invoke(Command(resume=resp), run_id)
        return self.status(run_id)

    def continue_run(self, run_id: str) -> dict:
        """재시작 복구: 승인 대기가 아닌 곳에서 멈춘 체크포인트의 남은 노드를 이어 감 (API 만 씀)."""
        snap = self.app.get_state(config_for(run_id))
        if snap.next and not snap.interrupts:
            self._invoke(None, run_id)
        return self.status(run_id)

    def followup(self, run_id: str, question: str, llm: str | None = None) -> dict[str, Any]:
        """이어 묻기: 이 실행의 근거 카드·후보만 읽음. 새 도구를 부르지 않고 상태도 바꾸지 않음."""
        from bigcontest.agent.followup import answer_followup

        values = self.status(run_id)["values"]
        return answer_followup(values, question, llm if llm is not None else values.get("llm"))
