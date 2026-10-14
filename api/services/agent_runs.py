"""실행·검토·승인·질의응답·이어 묻기 (실제 그래프). `LINKRADAR_BACKEND=real` 일 때 씀.

가짜(runs.py)와 같은 API 계약을 LangGraph 에이전트(agent/graph.py)로 지킨다. 표(store)는 가짜와 같고,
그래프 체크포인트는 따로 파일(<state>.agent.sqlite)에 둔다 (API 트랜잭션과 잠금이 섞이지 않게).

API ↔ 그래프 (한 계약, API 명세 §5)
- 시작: 활성 스냅샷과 입력(질문·도시·기준일·모드·LLM·도구)을 run_metadata 에 고정 → Agent.start.
  상태 queued → running → (그래프 끝 상태에 따라) awaiting_approval / published / answered / abstained / failed
- 보고서 판: 그래프가 승인 대기·끝점에 닿았을 때의 보고서를 report_revisions 에 "발행"한다 (Guard 가 고친 초안은
  발행하지 않으므로 판 번호는 건너뛸 수 있음). runs.report_revision 은 요청한 판(다시 만드는 중이면 다음 판 이상),
  발행된 판은 report_revisions 의 마지막 판. 근거·보고서·자원·검토는 모두 이 실행의 고정 스냅샷·발행 판으로 답한다
- abstain: 계약 위반·Guard ABSTAIN 은 abstain_reason. 승인은 409 APPROVAL_BLOCKED_BY_ABSTAIN, 계약 위반에 추가
  조사도 같은 오류 (그래프가 보류로 바꾸기 전에 API 가 막음)
- interrupt: 승인 대기 = awaiting_approval. 승인·보류는 Command(resume) → finalize 1건, 추가 조사는
  plan_more → collect_signals (최대 2회, 넘으면 409 EXTRA_ROUNDS_EXCEEDED)
- 검토: 자원 판정이 바뀌면 report_stale=1 → 그래프 record_review → write_report → 새 판. 끝난 뒤 발행 판의
  review_ids 가 지금까지의 검토를 모두 담았는지 확인하고, 그 사이 새 검토가 있으면 한 번 더 다시 만든다
- 재시작: queued·running 실행은 고정 입력으로 다시 이어 가고(체크포인트가 있으면 이어서, 없으면 처음부터),
  다시 만들던 보고서(report_stale=1)도 이어서 만든다
- 두 DB 맞추기: 승인·추가 조사는 API 표 approval_intents 에 의도(추가 조사는 범위·계획)를 먼저
  적고 그래프를 재개한다. 중간에 멈추면 _reconcile / recover 가 그래프 확정 기록과 의도를 대조해 같은 결정으로 끝냄

동시성: 실행마다 잠금 하나. 그래프 실행은 작업자 스레드 1개(sync=True 면 요청 안에서 바로).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Callable

from bigcontest.agent.budget import BUDGET_EXCEEDED, run_budget
from bigcontest.agent.graph import Agent
from bigcontest.agent.state import card_of, report_dict
from bigcontest.agent.schemas import (
    ApproveRequest,
    ApproveResponse,
    Candidate,
    ContractResult,
    FollowupRequest,
    FollowupResponse,
    GuardResult,
    QARequest,
    QAResponse,
    Report,
    ReviewRequest,
    ReviewResponse,
    RunAccepted,
    RunCreateRequest,
    RunResource,
    RunResourcesResponse,
    RunStatusResponse,
    TraceStep,
)

from api.auth import DEV_IDENTITY, Identity
from api.errors import ApiError, not_awaiting
from api.models import RunListResponse, RunSummary
from api.services.runs import RunService
from api.store import Store, mask_text, new_id

log = logging.getLogger("linkradar.api")
SELECTION_BUDGET = 2
RESOURCE_FIELDS = set(RunResource.model_fields)


_CAUTION = {"적합": 0, "확인 필요": 1, "부적합": 2}


def group_resources(rows: list[dict], judged: dict) -> list[RunResource]:
    """그래프 상태의 자원(후보마다 한 줄) → 자원 하나에 한 줄 + candidate_ids. 판정은 자원 ID 단위라 한 번만.
    거리는 가장 가까운 값, 자격은 가장 조심스러운 값, 경고는 합침. 순서는 처음 나온 순서 (후보 검토 배정 순),
    위기 전화(위급 시 안내)는 맨 아래."""
    out: dict[str, dict] = {}
    for r in rows:
        rid = r.get("resource_id")
        if not rid:
            continue
        cid = r.get("candidate_id")
        g = out.get(rid)
        if g is None:
            g = out[rid] = {k: x for k, x in r.items() if k in RESOURCE_FIELDS}
            g["warnings"] = list(r.get("warnings") or [])
            g["candidate_ids"] = []
        else:
            d = [x for x in (g.get("distance_km"), r.get("distance_km")) if x is not None]
            g["distance_km"] = min(d) if d else None
            if _CAUTION.get(r.get("eligibility"), 1) > _CAUTION.get(g.get("eligibility"), 1):
                g["eligibility"] = r.get("eligibility")
            g["warnings"] += [w for w in r.get("warnings") or [] if w not in g["warnings"]]
        if cid and cid not in g["candidate_ids"]:
            g["candidate_ids"].append(cid)
    # 위기 전화("묶음: 위급 시 안내")는 후보마다 맨 아래였으므로 합친 뒤에도 맨 아래 (안정 정렬)
    crisis = [rid for rid, g in out.items() if any(str(w).startswith("묶음: 위급 시 안내") for w in g["warnings"])]
    order = sorted(out, key=lambda rid: rid in crisis)
    return [RunResource(**{**out[rid], "candidate_id": (out[rid]["candidate_ids"] or [None])[0],
                           "judgement": judged.get(rid, (None, None))[0]}) for rid in order]


def phase_of(st: dict) -> str:
    """그래프 상태 → API 실행 상태."""
    v = st.get("values") or {}
    if st.get("waiting"):
        return "awaiting_approval"
    if not v:
        return "queued"
    if st.get("next"):
        return "running"
    if v.get("final_record"):
        return "approved" if v["final_record"].get("kind") == "확정" else "held"
    if v.get("published"):
        return "published"
    if v.get("answer"):
        return "answered" if v["answer"].get("status") == "answered" else "abstained"
    return "failed"


class AgentRunService(RunService):
    """RunService(가짜)와 같은 메서드. 저장 규칙(store)·검토 검사·이력은 그대로 물려받고 실행·재개만 그래프로."""

    source = "real"

    def __init__(self, store: Store, snapshots, agent: Agent, clock: Callable[[], datetime], sync: bool = False,
                 llm: str = "none", tools_backend: str = "real"):
        super().__init__(store, snapshots, 0.0, clock)
        self.agent = agent
        self.sync = sync
        self.llm = llm
        self.tools_backend = tools_backend
        self._pool = None if sync else ThreadPoolExecutor(max_workers=1, thread_name_prefix="linkradar-agent")
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._locks_guard = threading.Lock()
        self._scope_cache: dict[str, set[str]] = {}

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
        self.agent.close()

    def _lock(self, run_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks[run_id]

    def _submit(self, fn, *args) -> None:
        if self._pool is None:
            fn(*args)
        else:
            self._pool.submit(self._guarded, fn, *args)

    def _guarded(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 — 작업자 스레드 오류는 로그로 (실행은 failed 로 남김)
            log.exception("agent job failed")

    def wait_idle(self) -> None:
        """테스트용: 지금까지 넣은 그래프 작업이 끝날 때까지 기다림."""
        if self._pool is not None:
            self._pool.submit(lambda: None).result()

    # ------------------------------------------------------------------ 고정 입력

    def _inputs(self, run_id: str) -> dict:
        meta = self.store.get_run_metadata(run_id)
        if meta is None:
            raise ApiError(404, "RUN_NOT_FOUND", "없는 실행입니다.")
        import json

        return json.loads(meta["body"])["inputs"]

    def _values(self, run_id: str) -> dict:
        return self.agent.status(run_id)["values"] or {}

    def _budget(self, run_id: str):
        """이 실행의 LLM 예산 (configs/llm.yaml budget). 이미 llm_calls 에 옮긴 호출 수·비용에서 시작하고,
        아직 옮기지 않은 그래프 기록(llm_log)은 그래프 상태에서 더함."""
        row = self.store._one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls WHERE run_id = ? AND node != 'init'"
            " AND COALESCE(error_code, '') != ?", run_id, BUDGET_EXCEEDED)
        calls, cost = int(row["n"]), float(row["c"])
        try:
            log_ = (self._values(run_id).get("llm_log") or []) if self.agent.exists(run_id) else []
        except Exception:  # noqa: BLE001 — 예산 계산이 실행을 막지 않게
            log_ = []
        unsynced = [r for r in log_[self.store.count_llm_calls(run_id):]
                    if r.get("slot") != "init" and r.get("error_code") != BUDGET_EXCEEDED]
        return run_budget(calls + len(unsynced), cost + sum(float(r.get("cost_usd") or 0) for r in unsynced))

    # ------------------------------------------------------------------ 시작

    def create(self, req: RunCreateRequest, who: Identity = DEV_IDENTITY) -> RunAccepted:
        snap = self.snapshots.active()  # 없으면 503. 여기서 고정한 id 를 끝까지 씀
        now = self.clock()
        question, source_run, city, scope = req.question, None, req.city, None
        if req.mode == "qa" and req.candidate_id:
            cand, source_run = self._find_candidate(req.candidate_id, req.as_of, req.city, req.monitoring_run_id)
            city = cand["city"]
            question = question or f"{cand['city']} 어르신 어디부터 확인해야 해?"
            # 그 후보(+ 함께 볼 후보)만 조사. 같은 정기 감시 실행·같은 도시의 후보만 받음
            same = {c["candidate_id"] for c in self._values(source_run).get("candidates") or [] if c["city"] == city}
            bad = [c for c in req.candidate_ids if c not in same]
            if bad:
                raise ApiError(400, "CANDIDATE_NOT_IN_RUN",
                               "같은 정기 감시 실행·같은 도시의 후보가 아닙니다: " + ", ".join(bad[:5]))
            scope = list(dict.fromkeys([req.candidate_id, *req.candidate_ids]))
        # 그래프에도 가린 질문만 넘김 → 체크포인트·실행 기록 어디에도 원문이 남지 않음. 가린 표시([이름]·[상세주소] 등)는
        # 개인 지목 규칙에 걸리므로 이름·주소가 든 질문은 그대로 판단 보류(OUT_OF_SCOPE_INDIVIDUAL)
        inputs = {"mode": req.mode, "question": mask_text(question), "city": city, "as_of": req.as_of.isoformat(),
                  "analysis_mode": req.analysis_mode, "snapshot_id": snap.snapshot_id, "llm": self.llm,
                  "tools_backend": self.tools_backend, "selection_budget": SELECTION_BUDGET,
                  "candidate_id": req.candidate_id, "source_run_id": source_run, "scope_candidate_ids": scope}
        run_id = self._insert(inputs, snap, who, now)
        self._submit(self._execute, run_id)
        return RunAccepted(run_id=run_id, status="queued", snapshot_id=snap.snapshot_id, status_url=f"/runs/{run_id}")

    def _insert(self, inputs: dict, snap, who: Identity, now: datetime, status: str = "queued",
                origin: str | None = None) -> str:
        with self.store.tx():
            run_id = self.store.next_run_id(now)
            self.store.insert_run(
                run_id=run_id, mode=inputs["mode"], question=inputs["question"], candidate_id=inputs.get("candidate_id"),
                as_of=inputs["as_of"], analysis_mode=inputs["analysis_mode"], snapshot_id=snap.snapshot_id,
                status=status, route=None, evidence_revision=0, report_revision=0, report_stale=0, extra_rounds=0,
                abstain_reason=None, error_code=None, created_at=now.isoformat(), updated_at=now.isoformat())
            self.store.insert_run_metadata(
                run_id, snap.snapshot_id, snap.cleaning_rule_version, snap.rule_version, snap.ledger_version,
                {"source": "real", "inputs": inputs, "code_commit": None, "code_dirty": False}, now)
            self.store.audit(who.actor(), "run_created", run_id,
                             {"mode": inputs["mode"], "snapshot_id": snap.snapshot_id,
                              **({"origin": origin} if origin else {})}, now)
            self.store.audit("system", "snapshot_pinned", run_id, {"snapshot_id": snap.snapshot_id}, now)
        return run_id

    def _find_candidate(self, candidate_id: str, as_of: date, city: str | None = None,
                        monitoring_run_id: str | None = None) -> tuple[dict, str]:
        """후보를 열어 조사: 같은 기준일에 발행된 정기 감시 실행에서 그 후보를 찾음 (없으면 400).
        monitoring_run_id 를 주면 그 실행에서만 (화면이 읽은 바로 그 실행). 아니면 활성 스냅샷의 실행을 먼저,
        그다음 최근 실행부터 (현황·후보 목록 화면이 읽은 실행이 앞). 도시를 주면 그 도시 후보만."""
        active = self.snapshots.active_id
        runs = [r for r in self.store.monitoring_runs(as_of.isoformat()) if r["status"] == "published"]
        if monitoring_run_id:
            runs = [r for r in runs if r["run_id"] == monitoring_run_id]
            if not runs:
                raise ApiError(400, "INVALID_REQUEST", "이 기준일에 발행된 정기 감시 실행이 아닙니다. 후보 목록을 다시 불러오세요.")
        runs.sort(key=lambda r: r["snapshot_id"] != active)  # 안정 정렬: 같은 쪽 안에서는 최근 것부터
        for r in runs:
            for c in self._values(r["run_id"]).get("candidates") or []:
                if c["candidate_id"] == candidate_id and (city is None or c["city"] == city):
                    return c, r["run_id"]
        raise ApiError(400, "INVALID_REQUEST", "없는 후보 ID입니다.")

    def monitor_now(self, as_of: date, city: str, analysis_mode: str, who: Identity = DEV_IDENTITY) -> str:
        """현황·지도·후보 목록용 정기 감시 실행을 만들어 요청 안에서 끝까지 돌림 (도시 하나, 검토 예산 K)."""
        snap = self.snapshots.active()
        inputs = {"mode": "monitoring", "question": None, "city": city, "as_of": as_of.isoformat(),
                  "analysis_mode": analysis_mode, "snapshot_id": snap.snapshot_id, "llm": "none",
                  "tools_backend": self.tools_backend, "selection_budget": SELECTION_BUDGET,
                  "candidate_id": None, "source_run_id": None}
        run_id = self._insert(inputs, snap, who, self.clock(), origin="dashboard")
        self._execute(run_id)
        return run_id

    def _execute(self, run_id: str) -> None:
        """고정 입력으로 그래프를 처음부터(또는 체크포인트에서 이어서) 돌림."""
        with self._lock(run_id):
            inp = self._inputs(run_id)
            with self.store.tx():
                self.store.update_run(run_id, self.clock(), status="running")
            try:
                with self._budget(run_id):
                    self.agent.start(run_id, inp["mode"], inp["as_of"], question=inp["question"],
                                     analysis_mode=inp["analysis_mode"], snapshot_id=inp["snapshot_id"],
                                     llm=inp["llm"], selection_budget=inp["selection_budget"],
                                     tools_backend=inp["tools_backend"],
                                     city=inp["city"] if inp["mode"] == "monitoring" else None,
                                     scope_candidate_ids=inp.get("scope_candidate_ids"))
            except Exception:  # noqa: BLE001 — 사유는 로그에만 (API 명세 §4)
                log.exception("agent start failed", extra={"run_id": run_id})
                with self.store.tx():
                    self.store.update_run(run_id, self.clock(), status="failed", error_code="AGENT_ERROR")
                return
            self._sync(run_id, "initial")

    def _sync(self, run_id: str, reason: str) -> dict:
        """그래프 상태 → runs 행. 승인 대기·끝점이면 그때의 보고서를 새 판으로 발행 (같은 판은 한 번만)."""
        st = self.agent.status(run_id)
        v = st["values"] or {}
        phase = phase_of(st)
        now = self.clock()
        with self.store.tx():
            run = self.store.get_run(run_id)
            fields: dict[str, Any] = {"status": phase, "route": v.get("route"),
                                      "evidence_revision": v.get("evidence_revision") or 0,
                                      "extra_rounds": v.get("extra_rounds") or 0,
                                      "abstain_reason": v.get("abstain_reason")}
            rep = report_dict(v)  # 상태에는 카드 ID 만 있음. 보고서 판에는 카드 전체를 남김
            if rep and phase not in ("queued", "running"):
                rev = v["report_revision"]
                if not self.store.has_report_revision(run_id, rev):
                    first = self.store.latest_report(run_id) is None
                    self.store.insert_report_revision(run_id, rev, v.get("evidence_revision") or 1,
                                                      run["snapshot_id"], rep, "initial" if first else reason, now)
                if not run["report_stale"]:  # 다시 만드는 중이면 요청한 판을 그대로 둠 (_regenerate 가 맞춤)
                    fields["report_revision"] = rev
            self.store.update_run(run_id, now, **fields)
            logged = self.store.count_llm_calls(run_id)
            for rec in (v.get("llm_log") or [])[logged:]:
                self.store.insert_llm_call(run_id, rec.get("slot") or "-", rec, now)
            if phase == "failed":
                self.store.update_run(run_id, now, error_code="AGENT_ERROR")
        return st

    # ------------------------------------------------------------------ 조회

    def advance(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run is None:
            raise ApiError(404, "RUN_NOT_FOUND", "없는 실행입니다.")
        return run

    def status(self, run_id: str) -> RunStatusResponse:
        run = self.advance(run_id)
        v = self._values(run_id)
        done = run["status"] not in ("queued", "running")
        return RunStatusResponse(
            run_id=run_id, status=run["status"], mode=run["mode"], route=run["route"] or v.get("route"),
            as_of=date.fromisoformat(run["as_of"]), analysis_mode=run["analysis_mode"], snapshot_id=run["snapshot_id"],
            path=list(v.get("path") or []),
            guard=[GuardResult.model_validate(g) for g in v.get("guard") or []] if done else [],
            contract_check=ContractResult.model_validate(v["contract_check"]) if v.get("contract_check") else None,
            candidates=[Candidate.model_validate(c) for c in v.get("candidates") or []] if done else [],
            evidence_revision=run["evidence_revision"] or None, report_revision=run["report_revision"] or None,
            report_stale=bool(run["report_stale"]), abstain_reason=run["abstain_reason"],
            errors=([{"error_code": run["error_code"]}] if run["error_code"] else [])
            + [{k: str(x) for k, x in e.items()} for e in v.get("errors") or []],
            decision_trace=[TraceStep.model_validate(t) for t in v.get("decision_trace") or []],
            published_report_revision=self._published(run_id), extra_rounds=run["extra_rounds"],
        )

    def list(self, candidate_id: str | None, limit: int) -> RunListResponse:
        items = []
        for r in self.store.list_runs(candidate_id, limit):
            meta = self.store.get_run_metadata(r["run_id"]) or {}
            try:
                city = self._inputs(r["run_id"]).get("city")
            except (ApiError, KeyError, ValueError):
                city = None
            items.append(RunSummary(
                run_id=r["run_id"], mode=r["mode"], status=r["status"], route=r["route"], question=r["question"],
                candidate_id=r["candidate_id"], city=city, as_of=r["as_of"], analysis_mode=r["analysis_mode"],
                snapshot_id=r["snapshot_id"], cleaning_rule_version=meta.get("cleaning_rule_version"),
                rule_version=meta.get("rule_version"), ledger_version=meta.get("ledger_version"),
                evidence_revision=r["evidence_revision"], report_revision=r["report_revision"],
                report_stale=bool(r["report_stale"]), extra_rounds=r["extra_rounds"], created_at=r["created_at"],
                updated_at=r["updated_at"]))
        return RunListResponse(items=items)

    def _run_city(self, run_id: str) -> str | None:
        try:
            return self._inputs(run_id).get("city")
        except (ApiError, KeyError, ValueError):
            return None

    def _run_resources(self, run_id: str) -> list[dict]:
        return list(self._values(run_id).get("resources") or [])

    def resources(self, run_id: str) -> RunResourcesResponse:
        """이 실행이 고정 스냅샷으로 고른 추천 자원 (그래프 상태, G-07 을 지난 것). 새로 찾지 않는다."""
        run = self.advance(run_id)
        if run["status"] in ("queued", "running"):
            raise ApiError(409, "RUN_NOT_READY", "실행이 아직 끝나지 않았습니다. 잠시 뒤 다시 보세요.")
        judged = self.store.current_judgements(run_id)
        items = group_resources(self._run_resources(run_id), judged)
        return RunResourcesResponse(run_id=run_id, snapshot_id=run["snapshot_id"],
                                    evidence_revision=run["evidence_revision"] or None,
                                    report_revision=self._published(run_id), items=items)

    def _resource_scope_of(self, run_id: str) -> set[str]:
        return {r.get("resource_id") for r in self._run_resources(run_id)}

    def _candidate_ids(self, run_id: str) -> set[str]:
        return {c["candidate_id"] for c in self._values(run_id).get("candidates") or []}

    def _run_candidates(self, run_id: str) -> list[dict]:
        return list(self._values(run_id).get("candidates") or [])

    # ------------------------------------------------------------------ 검토

    def _check_review(self, run: dict, req: ReviewRequest) -> ApiError | None:
        if run["status"] != "awaiting_approval":
            return not_awaiting(run["status"], "검토를 저장할")
        if req.report_revision is not None and req.report_revision != run["report_revision"]:
            return ApiError(409, "REVIEW_REVISION_STALE",
                            f"보고서가 {run['report_revision']}판으로 바뀌었습니다. 새 판을 확인한 뒤 검토를 저장하세요.")
        latest = self.store.latest_report(run["run_id"])
        card_ids = ({e.evidence_id for e in Report.model_validate_json(latest["body"]).evidence_card.evidence}
                    if latest else set())
        bad_ev = [e for e in req.checked_evidence_ids if e not in card_ids]
        if bad_ev:
            return ApiError(400, "EVIDENCE_NOT_IN_RUN", "이 실행의 근거 카드에 없는 근거 ID입니다: " + ", ".join(bad_ev[:5]))
        scope = self._scope_cache.pop(run["run_id"], set())
        bad_res = [j.resource_id for j in req.resource_judgements if j.resource_id not in scope]
        if bad_res:
            return ApiError(400, "RESOURCE_NOT_IN_RUN", "이 실행의 추천 범위에 없는 자원 ID입니다: " + ", ".join(bad_res[:5]))
        return None

    def review(self, run_id: str, req: ReviewRequest, who: Identity = DEV_IDENTITY) -> ReviewResponse:
        self.advance(run_id)
        with self._lock(run_id):
            self._reconcile(run_id)  # 끝나지 않은 승인 의도가 있으면 먼저 끝냄 (검토가 확정된 실행을 바꾸지 않게)
        # 자원 범위는 그래프 상태에서 미리 읽음 (store 트랜잭션 안에서 그래프 DB 를 읽지 않음)
        self._scope_cache[run_id] = self._resource_scope_of(run_id)
        out = super().review(run_id, req, who)
        if out.changed:
            self._submit(self._regenerate, run_id)
            saved = self.store.get_run(run_id)
            out = out.model_copy(update={"report_stale": bool(saved["report_stale"]),
                                         "report_revision": saved["report_revision"]})
        return out

    def _schedule_regenerate(self, run_id: str, now: datetime) -> None:
        """가짜의 예약 대신 그래프로 다시 만듦 (review 가 끝난 뒤 _regenerate)."""

    def _review_payload(self, run_id: str) -> dict:
        reviews = self.store.list_reviews(run_id)
        judged = self.store.current_judgements(run_id)
        checked: list[str] = []
        for r in reviews:
            checked += [e for e in r["checked_evidence_ids"] if e not in checked]
        follow = next((r["follow_up_on"] for r in reversed(reviews) if r["follow_up_on"]), None)
        return {"judgements": [{"resource_id": k, "judgement": j} for k, (j, _m) in sorted(judged.items())],
                "checked_evidence_ids": checked, "follow_up_on": follow,
                "review_ids": [r["review_id"] for r in reviews]}

    def _regenerate(self, run_id: str) -> None:
        """검토를 그래프에 넣어 보고서를 다시 만듦. 발행 판이 지금까지의 검토를 모두 담을 때까지 (최대 3번)."""
        with self._lock(run_id):
            for _ in range(3):
                run = self.store.get_run(run_id)
                if not run["report_stale"] or run["status"] != "awaiting_approval":
                    return
                payload = self._review_payload(run_id)
                v = self._values(run_id)
                if set(payload["review_ids"]) - set((v.get("report") or {}).get("review_ids") or []):
                    with self._budget(run_id):
                        res = self.agent.review(run_id, payload, v.get("report_revision"),
                                                key=payload["review_ids"][-1])
                    if res["status"] not in ("accepted", "duplicate"):
                        log.warning("review resume %s", res.get("code") or res["status"])
                    self._sync(run_id, "review_changed")
                v = self._values(run_id)
                reflected = set((v.get("report") or {}).get("review_ids") or [])
                if set(self._review_payload(run_id)["review_ids"]) <= reflected:
                    now = self.clock()
                    with self.store.tx():
                        requested = self.store.get_run(run_id)["report_revision"]
                        self.store.update_run(run_id, now, report_stale=0, report_revision=v["report_revision"])
                        self.store.audit("system", "report_regenerated", run_id,
                                         {"requested_revision": requested, "published_revision": v["report_revision"],
                                          "review_ids": sorted(reflected)}, now)
                    return
            log.warning("review not reflected after retries", extra={"run_id": run_id})

    # ------------------------------------------------------------------ 승인
    #
    # 두 DB (API 표 / 그래프 체크포인트·확정 기록)는 한 트랜잭션으로 묶이지 않는다. 그래서
    # 1) API 가 승인 의도(approval_intents)를 먼저 적고  2) 그래프를 재개하고  3) API 승인·확정 기록을 적고
    # 4) 상태를 맞춘다(_sync). 어느 단계에서 멈춰도 _reconcile 이 의도와 그래프 확정 기록을 보고 같은 결정으로 끝낸다.

    def _judge_key(self, run_id: str, req: ApproveRequest) -> tuple[ApproveResponse | None, ApiError | None, dict | None]:
        """멱등 키 판정: (같은 요청의 첫 응답, 오류, 아직 끝나지 않은 같은 요청의 의도)."""
        first = self.store.get_approval_by_key(req.idempotency_key)
        if first is not None:
            if (first["run_id"], first["decision"], first["report_revision"]) == (
                    run_id, req.decision, req.report_revision):
                return ApproveResponse(run_id=run_id, decision=first["decision"],
                                       report_revision=first["report_revision"],
                                       approval_id=first["approval_id"], duplicate=True,
                                       approver=first.get("approver")), None, None
            return None, ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 멱등 키로 다른 결정을 보냈습니다."), None
        it = self.store.get_intent(req.idempotency_key)
        if it is not None and it["state"] == "pending":
            if (it["run_id"], it["decision"], it["report_revision"]) == (run_id, req.decision, req.report_revision):
                return None, None, it
            return None, ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 멱등 키로 다른 결정을 보냈습니다."), None
        return None, None, None

    def approve(self, run_id: str, req: ApproveRequest, who: Identity = DEV_IDENTITY) -> ApproveResponse:
        self.advance(run_id)
        name, actor, extra = self._who(who, req.approver)
        with self._lock(run_id):
            self._reconcile(run_id)  # 지난번에 끝나지 않은 승인 의도·그래프 확정 기록을 먼저 맞춤
            cands = self._candidate_ids(run_id)
            contract_broken = (self._values(run_id).get("contract_check") or {}).get("status") == "ERROR"
            now = self.clock()
            intent: dict | None = None
            with self.store.tx():
                run = self.store.get_run(run_id)
                dup, rejected, intent = self._judge_key(run_id, req)
                if dup is not None:
                    return dup
                if rejected is None and intent is None:
                    rejected = self._check(run, req) or self._check_focus(req, cands)
                    if rejected is None and req.decision == "more" and contract_broken:
                        rejected = ApiError(409, "APPROVAL_BLOCKED_BY_ABSTAIN",
                                            "계약 위반으로 보류된 실행은 추가 조사로 풀 수 없습니다.")
                if rejected is not None:
                    self._audit_rejected(actor, run_id, rejected, req, extra, now)
                elif intent is None and req.decision != "more":
                    intent = self.store.put_intent(
                        key=req.idempotency_key, run_id=run_id, approval_id=new_id("apr", now), decision=req.decision,
                        report_revision=req.report_revision, approver=name, memo=req.memo, actor=actor,
                        audit_extra=extra, focus=None, now=now)
            if rejected is not None:
                raise rejected
            if req.decision == "more":
                return self._accept_more(run_id, req, name, actor, extra, now)
            approval_id = self._apply_intent(intent)
        return ApproveResponse(run_id=run_id, decision=req.decision, report_revision=req.report_revision,
                               approval_id=approval_id, duplicate=False, approver=name)

    def _apply_intent(self, it: dict) -> str:
        """승인·보류 의도 하나를 끝까지: 그래프 재개 → API 승인·확정 기록 → 상태 맞춤. 승인 ID 를 돌려줌.
        그래프가 거절하면 의도를 rejected 로 두고 오류를 냄 (거절 감사 로그와 함께)."""
        run_id, key = it["run_id"], it["idempotency_key"]
        with self._budget(run_id):
            res = self.agent.approve(run_id, it["decision"], it["report_revision"], approver=it["approver"],
                                     idempotency_key=key)
        if res["status"] not in ("accepted", "duplicate"):
            code = res.get("code") or "RUN_NOT_AWAITING_APPROVAL"
            if code == "RUN_NOT_AWAITING_APPROVAL":
                done = (((res.get("record") or {}).get("payload") or {}).get("approval") or {}).get("decision")
                err = not_awaiting(done if done in ("approved", "held") else self.store.get_run(run_id)["status"],
                                   "승인·보류·추가 조사를 할")
            else:
                err = ApiError(409, code if code != "STALE_REPORT_REVISION" else "REPORT_REVISION_STALE",
                               "승인을 처리할 수 없습니다. 실행 상태를 다시 확인하세요.")
            now = self.clock()
            with self.store.tx():
                self.store.audit(it["actor"], "approval_rejected", run_id,
                                 {"error_code": err.code, "decision": it["decision"],
                                  "report_revision": it["report_revision"], **it["audit_extra"],
                                  "agent": res["status"]}, now)
                self.store.set_intent(key, "rejected", err.code, now)
            raise err
        approval_id = self._record_final(run_id, it, res.get("record") or {})
        self._sync(run_id, "final")
        return approval_id

    def _record_final(self, run_id: str, it: dict, rec: dict, recovered: bool = False) -> str:
        """API 승인·확정 기록 (한 트랜잭션). 이미 있으면 그대로 두고 빠진 것만 채움. 승인 ID 를 돌려줌.
        승인 행은 의도의 원래 값(결정·판·승인자·멱등 키·받은 시각)으로 적는다."""
        now = self.clock()
        key = it["idempotency_key"]
        kind = rec.get("kind") or ("확정" if it["decision"] == "approved" else "보류")
        with self.store.tx():
            have = self.store.get_approval_by_key(key)
            if have is None:
                approval_id = it["approval_id"]
                self.store.insert_approval(approval_id, run_id, key, it["decision"], it["report_revision"],
                                           it["approver"], it.get("memo"), datetime.fromisoformat(it["created_at"]))
                self.store.audit(it["actor"], "approval_received", run_id,
                                 {"approval_id": approval_id, "decision": it["decision"],
                                  "report_revision": it["report_revision"], **(it.get("audit_extra") or {}),
                                  **({"recovered": True} if recovered else {})}, now)
            else:
                approval_id = have["approval_id"]
            self.store.finalize(run_id, "finalize", {"decision": it["decision"], "approval_id": approval_id,
                                                     "report_revision": it["report_revision"], "kind": kind}, now)
            if self.store.get_intent(key) is not None:
                self.store.set_intent(key, "applied", kind, now)
        return approval_id

    def _reconcile(self, run_id: str) -> None:
        """그래프 확정 기록 ↔ API 승인·확정 이력·상태를 맞춤. 실행 잠금 안에서 부름.

        - 그래프에 확정·보류 기록이 있으면: 그 기록의 요청(멱등 키)으로 의도를 찾아 빠진 승인·확정 행을 원래 값으로
          채우고, 같은 실행의 다른 대기 의도는 거절로 닫고, 상태를 맞춤. 결정·판은 그래프 기록 그대로 (뒤집지 않음)
        - 그래프 기록이 없고 승인·보류 의도가 대기 중이면: 그 의도로 그래프를 재개해 끝냄 (먼저 적힌 의도부터)
        """
        rec = self.agent.store.get(run_id, "finalize")
        if rec is None:
            for it in self.store.pending_intents(run_id):
                if it["decision"] == "more":
                    continue  # 추가 조사는 recover 의 _more 가 이어 감
                try:
                    self._apply_intent(it)
                except ApiError:
                    pass  # 거절로 닫힘 (감사 로그에 남음)
            return
        req = ((rec.get("payload") or {}).get("approval") or {}).get("request") or {}
        key = req.get("idempotency_key")
        now = self.clock()
        if key:
            it = self.store.get_intent(key)
            if it is None or it["run_id"] != run_id:
                # 의도 없이 그래프만 확정된 경우 (이 표가 생기기 전 파일): 그래프 기록의 요청 값으로 채움
                it = {"idempotency_key": key, "run_id": run_id, "approval_id": new_id("apr", now),
                      "decision": req.get("decision"), "report_revision": req.get("report_revision"),
                      "approver": req.get("approver") or "-", "memo": None, "actor": "system", "audit_extra": {},
                      "created_at": rec.get("created_at") or now.isoformat()}
            if self.store.get_approval_by_key(key) is None or not self.store.list_finalizations(run_id):
                self._record_final(run_id, it, rec, recovered=True)
        done = "approved" if rec.get("kind") == "확정" else "held"
        with self.store.tx():
            for other in self.store.pending_intents(run_id):
                if other["idempotency_key"] == key:
                    continue
                err = not_awaiting(done, "승인·보류·추가 조사를 할")
                self.store.audit(other["actor"], "approval_rejected", run_id,
                                 {"error_code": err.code, "decision": other["decision"],
                                  "report_revision": other["report_revision"], **other["audit_extra"],
                                  "recovered": True}, now)
                self.store.set_intent(other["idempotency_key"], "rejected", err.code, now)
        if self.store.get_run(run_id)["status"] != phase_of(self.agent.status(run_id)):
            self._sync(run_id, "final")

    def _reconcile_job(self, run_id: str) -> None:
        with self._lock(run_id):
            try:
                self._reconcile(run_id)
            except Exception:  # noqa: BLE001 — 다음 재시작·다음 요청에서 다시 맞춤
                log.exception("approval reconcile failed", extra={"run_id": run_id})

    def _audit_rejected(self, actor, run_id, err: ApiError, req: ApproveRequest, extra: dict, now) -> None:
        self.store.audit(actor, "approval_rejected", run_id,
                         {"error_code": err.code, "decision": req.decision, "report_revision": req.report_revision,
                          **extra}, now)

    def _resolve_focus(self, run_id: str, focus: dict | None) -> dict | None:
        """추가 조사 범위를 받을 때 계획까지 정함. 저장·재개에 쓰는 값: 검증한 후보·도구·반증 + 계획
        (+ 계획을 정할 때의 LLM 기록). 메모 원문은 넣지 않는다 (마스킹해 계획을 정하는 데만 씀)."""
        if not focus:
            return None
        from bigcontest.agent.nodes import make_plan

        v = self._values(run_id)
        with self._budget(run_id):
            plan, recs = make_plan({"candidates": v.get("candidates") or [], "llm": v.get("llm"),
                                    "llm_log": v.get("llm_log") or []}, focus)
        return {"candidate_ids": list(focus.get("candidate_ids") or []), "tools": list(focus.get("tools") or []),
                "counter_checks": list(focus.get("counter_checks") or []), "memo_given": bool(focus.get("memo")),
                "plan": plan, "plan_llm_log": recs}

    def _accept_more(self, run_id: str, req: ApproveRequest, name: str, actor: str, extra: dict,
                     now: datetime) -> ApproveResponse:
        """추가 조사 접수: 계획을 정하고 → 승인 행 + 의도(범위·계획) + running 을 한 트랜잭션으로 → 그래프 작업.
        계획을 정하는 사이 상태가 바뀌었을 수 있어 트랜잭션 안에서 한 번 더 확인."""
        focus = self._resolve_focus(run_id, req.focus.model_dump() if req.focus else None)
        now = self.clock()
        approval_id = new_id("apr", now)
        with self.store.tx():
            run = self.store.get_run(run_id)
            dup, rejected, _ = self._judge_key(run_id, req)
            if dup is not None:
                return dup
            rejected = rejected or self._check(run, req)
            if rejected is not None:
                self._audit_rejected(actor, run_id, rejected, req, extra, now)
            else:
                self.store.insert_approval(approval_id, run_id, req.idempotency_key, "more", req.report_revision, name,
                                           req.memo, now)
                self.store.put_intent(key=req.idempotency_key, run_id=run_id, approval_id=approval_id,
                                      decision="more", report_revision=req.report_revision, approver=name,
                                      memo=req.memo, actor=actor, audit_extra=extra, focus=focus, now=now)
                self.store.audit(actor, "approval_received", run_id,
                                 {"approval_id": approval_id, "decision": "more", "report_revision": req.report_revision,
                                  "focus": bool(req.focus), **extra}, now)
                self.store.update_run(run_id, now, status="running", extra_rounds=run["extra_rounds"] + 1)
        if rejected is not None:
            raise rejected
        self._submit(self._more, run_id, req.report_revision, name, req.idempotency_key, focus)
        return ApproveResponse(run_id=run_id, decision="more", report_revision=req.report_revision,
                               approval_id=approval_id, duplicate=False, approver=name)

    def _more(self, run_id: str, rev: int, name: str, key: str, focus: dict | None) -> None:
        """추가 조사 회차. focus 는 받을 때 정해 저장한 값 (재시작 복구도 같은 값)."""
        resp = {"decision": "more", "report_revision": rev, "approver": name, "idempotency_key": key,
                **({"focus": focus} if focus else {})}
        with self._lock(run_id):
            try:
                with self._budget(run_id):
                    res = self.agent.approve(run_id, "more", rev, approver=name, idempotency_key=key, focus=focus)
                    st = self.agent.status(run_id)
                    if res["status"] == "duplicate" and st["waiting"] \
                            and (st["interrupt"] or {}).get("report_revision") == rev:
                        # 키는 차지했지만 재개가 끝나지 않음 (재시작 복구): 같은 범위·계획으로 이어 감
                        self.agent.resume_pending(run_id, resp)
                    elif not st["waiting"] and st["next"]:
                        # 재개했지만 회차 도중에 멈춘 체크포인트: 남은 노드를 이어서 (계획은 상태에 있음)
                        self.agent.continue_run(run_id)
            except Exception:  # noqa: BLE001
                log.exception("extra round failed", extra={"run_id": run_id})
                with self.store.tx():
                    self.store.update_run(run_id, self.clock(), status="failed", error_code="AGENT_ERROR")
                return
            self._sync(run_id, "extra_round")
            st = self.agent.status(run_id)
            if not (st["waiting"] and (st["interrupt"] or {}).get("report_revision") == rev):
                with self.store.tx():
                    self.store.set_intent(key, "applied", "more", self.clock())

    # ------------------------------------------------------------------ 재시작 복구

    def recover(self) -> list[str]:
        """API 를 다시 켰을 때: 끝나지 않은 실행·다시 만들던 보고서·승인 의도를 고정 입력으로 이어 감.

        - 승인 대기인데 그래프에 확정 기록이 있거나 승인·보류 의도가 대기 중 → _reconcile
        - 승인·보류로 끝난 실행인데 API 승인·확정 행이 빠졌거나 대기 의도가 남음 → _reconcile
        - 추가 조사 중(running)이고 그래프가 그 판에서 대기 → 받을 때 저장한 범위·계획으로 _more
        - 추가 조사 중이고 그래프가 회차 도중에 멈춤 → 남은 노드를 이어서 (_execute)
        """
        todo = []
        for run in self.store.runs_with_status(("queued", "running", "awaiting_approval", "approved", "held")):
            meta = self.store.get_run_metadata(run["run_id"]) or {}
            if '"source": "real"' not in (meta.get("body") or ""):
                continue
            run_id = run["run_id"]
            final = self.agent.store.get(run_id, "finalize")
            pending = self.store.pending_intents(run_id)
            if run["status"] in ("approved", "held"):
                req = ((final or {}).get("payload") or {}).get("approval") or {}
                key = (req.get("request") or {}).get("idempotency_key")
                if pending or (final and (not self.store.list_finalizations(run_id)
                                          or (key and self.store.get_approval_by_key(key) is None))):
                    todo.append(run_id)
                    self._submit(self._reconcile_job, run_id)
                continue
            if run["status"] == "awaiting_approval":
                if final is not None or any(i["decision"] != "more" for i in pending):
                    todo.append(run_id)
                    self._submit(self._reconcile_job, run_id)
                if run["report_stale"]:
                    todo.append(run_id)
                    self._submit(self._regenerate, run_id)
                continue
            todo.append(run_id)
            st = self.agent.status(run_id)
            mores = [i for i in pending if i["decision"] == "more"]
            waiting_rev = (st["interrupt"] or {}).get("report_revision")
            legacy = [a for a in self.store.list_approvals(run_id)
                      if a["decision"] == "more" and a["report_revision"] == waiting_rev]
            if mores:
                # 받을 때 저장한 범위·계획으로: 재개 전이면 재개, 키만 차지했으면 이어서, 회차 도중이면 남은 노드
                it = mores[-1]
                self._submit(self._more, run_id, it["report_revision"], it["approver"], it["idempotency_key"],
                             it["focus"])
            elif st["waiting"] and legacy:  # 의도 표가 생기기 전 파일: 범위 없이 이어 감
                a = legacy[-1]
                self._submit(self._more, run_id, a["report_revision"], a["approver"], a["idempotency_key"], None)
            elif st["waiting"]:
                self._submit(self._sync, run_id, "initial")
            else:
                self._submit(self._execute, run_id)
        return list(dict.fromkeys(todo))

    # ------------------------------------------------------------------ 질의응답

    def qa(self, req: QARequest, who: Identity = DEV_IDENTITY) -> QAResponse:
        """질의응답: 그래프를 요청 안에서 끝까지 돌리고 Guard 를 지난 문장만 한 번에 응답."""
        snap = self.snapshots.active()
        now = self.clock()
        q = req.question if (req.city is None or req.city in req.question) else f"{req.city} {req.question}"
        q = mask_text(q)  # 저장·그래프 모두 가린 질문 (create 와 같음)
        inputs = {"mode": "qa", "question": q, "city": req.city, "as_of": req.as_of.isoformat(),
                  "analysis_mode": req.analysis_mode, "snapshot_id": snap.snapshot_id, "llm": self.llm,
                  "tools_backend": self.tools_backend, "selection_budget": SELECTION_BUDGET,
                  "candidate_id": None, "source_run_id": None}
        run_id = self._insert(inputs, snap, who, now)
        self._execute(run_id)
        run = self.store.get_run(run_id)
        v = self._values(run_id)
        guard = [GuardResult.model_validate(g) for g in v.get("guard") or []]
        rep = v.get("report") or {}
        ans = v.get("answer") or {}
        if run["status"] == "failed":
            raise ApiError(500, "INTERNAL_ERROR", "처리 중 문제가 생겼습니다.")
        if run["status"] == "abstained" or rep.get("is_abstain") or not rep:
            from bigcontest.agent.nodes import needed_data

            return QAResponse(run_id=run_id, status="abstained", guard=guard, abstain_reason=v.get("abstain_reason"),
                              needed_data=needed_data(v))
        text = ans.get("text") or "\n".join(list(rep.get("fact_sentences") or [])
                                           + ([rep["narrative"]] if rep.get("narrative") else []))
        return QAResponse(run_id=run_id, status="answered", evidence_card=card_of(v),
                          answer=text, guard=guard)

    # ------------------------------------------------------------------ 이어 묻기

    def followup(self, run_id: str, req: FollowupRequest, who: Identity = DEV_IDENTITY) -> FollowupResponse:
        run = self.advance(run_id)
        if run["status"] in ("queued", "running") or self.store.latest_report(run_id) is None:
            raise ApiError(409, "RUN_NOT_READY", "보고서가 나온 실행에만 이어 물을 수 있습니다.")
        with self._budget(run_id):
            out = self.agent.followup(run_id, req.question, llm=self._inputs(run_id).get("llm"))
        return self._followup_out(run_id, out, who)
