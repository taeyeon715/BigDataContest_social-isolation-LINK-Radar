"""실행·검토·승인·질의응답 (가짜 백엔드).

실행 흐름
1. `create` : 시작할 때 활성 스냅샷을 고정하고, 끝났을 때 보여 줄 결과(경로·Guard·후보·첫 보고서)를 미리 만들어
   `run_results`에 둔다. 상태는 `queued`, 예약(`run_jobs`)으로 `running` → 끝 상태.
2. 조회할 때마다 `advance`가 기한이 지난 예약을 처리한다. 그래서 API를 다시 켜도 같은 run_id로 이어진다.
3. 검토로 자원 판정이 바뀌면 보고서 판이 올라가고 `report_stale=true`. 예약된 시간이 지나면 새 판이 만들어지고
   `report_stale=false` (진짜는 그래프의 write_report·guard_report가 함).
4. 승인은 최신 판 + 멱등 키로만. 같은 키 → 첫 결과 그대로(duplicate=true), 확정 기록은 `finalizations`에 1건.
   거절도 감사 로그(`approval_rejected`)에 남는다. 거절 기록을 커밋한 뒤에 오류를 돌려준다.
5. 검토자·승인자: 인증을 켜면 토큰의 이름을 기록한다 (요청 본문의 이름은 감사 detail `claimed_actor`로만 남김).
   개발 모드(인증 끔)에서는 본문 이름을 기록하고, 감사 로그 actor는 `dev-unauthenticated`.

실제 그래프는 api/services/agent_runs.py (이 클래스를 물려받아 `create`·`advance`·승인·검토를 그래프 실행·재개로 바꿈,
저장 규칙(store)은 그대로). 판단 기록(decision_trace)은 가짜도 지나온 노드마다 채운다 (`fake_trace`).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Callable

from bigcontest.agent.schemas import (
    ApproveRequest,
    ApproveResponse,
    Candidate,
    ContractIssue,
    ContractResult,
    EvidenceCard,
    EvidenceResponse,
    Fact,
    FollowUpItem,
    FollowUpListResponse,
    FollowupRequest,
    FollowupResponse,
    GuardResult,
    OutcomeListResponse,
    OutcomeRecord,
    OutcomeRequest,
    OutcomeResponse,
    OutcomeSummaryResponse,
    OutcomeSummaryRow,
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
    ToolResult,
    TraceStep,
)

from bigcontest.rules import outcomes as O

from api.auth import DEV_IDENTITY, Identity
from api.errors import ApiError, not_awaiting
from api.models import (
    ApprovalRecord,
    ReportResponse,
    ReportRevisionInfo,
    ReviewRecord,
    RunHistoryResponse,
    RunListResponse,
    RunSummary,
)
from api.services import fake_data as fd
from api.services.snapshots import SnapshotService
from api.store import Store, new_id

FINAL_STATES = {"published", "answered", "abstained", "approved", "held", "failed"}


class RunService:
    def __init__(self, store: Store, snapshots: SnapshotService, delay_s: float, clock: Callable[[], datetime]):
        self.store = store
        self.snapshots = snapshots
        self.delay = timedelta(seconds=delay_s)
        self.clock = clock

    # ------------------------------------------------------------------ 시작

    def create(self, req: RunCreateRequest, who: Identity = DEV_IDENTITY) -> RunAccepted:
        snap = self.snapshots.active()  # 없으면 503
        now = self.clock()
        with self.store.tx():
            run_id = self.store.next_run_id(now)
            result = self._plan(run_id, req, snap.snapshot_id)
            self.store.insert_run(
                run_id=run_id, mode=req.mode, question=req.question, candidate_id=result.get("candidate_id"),
                as_of=req.as_of.isoformat(), analysis_mode=req.analysis_mode, snapshot_id=snap.snapshot_id,
                status="queued", route=None, evidence_revision=0, report_revision=0, report_stale=0, extra_rounds=0,
                abstain_reason=None, error_code=None, created_at=now.isoformat(), updated_at=now.isoformat())
            self.store.insert_run_metadata(
                run_id, snap.snapshot_id, snap.cleaning_rule_version, snap.rule_version, snap.ledger_version,
                {"source": "fake", "code_commit": None, "code_dirty": False, "city": result.get("city"),
                 "grain_version": "grain-v1", "selection_budget": fd.SELECTION_BUDGET}, now)
            self.store.put_result(run_id, result)
            self.store.add_job(run_id, "start", now + self.delay * 0.3)
            self.store.add_job(run_id, "finish", now + self.delay)
            self.store.audit(who.actor(), "run_created", run_id, {"mode": req.mode, "snapshot_id": snap.snapshot_id},
                             now)
            self.store.audit("system", "snapshot_pinned", run_id, {"snapshot_id": snap.snapshot_id}, now)
        if self.delay.total_seconds() <= 0:
            self.advance(run_id)
        return RunAccepted(run_id=run_id, status="queued", snapshot_id=snap.snapshot_id, status_url=f"/runs/{run_id}")

    def _plan(self, run_id: str, req: RunCreateRequest, snapshot_id: str) -> dict:
        """끝났을 때의 결과를 미리 만든다 (같은 스냅샷 고정)."""
        mode = req.analysis_mode
        if req.mode == "monitoring":
            cities = [req.city] if req.city else ["강남", "춘천"]
            cands = [c for city in cities for c in fd.candidates_for(city, req.as_of)]
            card = self._monitor_card(run_id, cands, cities, req.as_of, mode, snapshot_id)
            return dict(final_status="published", route="investigate", path=fd.MONITOR_PATH,
                        guard=[g.model_dump(mode="json") for g in fd.standard_guard()], contract_check=None,
                        candidates=[c.model_dump(mode="json") for c in cands], candidate_id=None,
                        city=req.city, abstain_reason=None,
                        report=fd.build_report(card, 1).model_dump(mode="json"))
        # qa: 후보를 열어 조사 / 질문
        cand: Candidate | None = None
        if req.candidate_id:
            cand = fd.find_candidate(req.candidate_id, req.as_of)
            if cand is None or (req.city and cand.city != req.city):
                raise ApiError(400, "INVALID_REQUEST", "없는 후보 ID입니다.")
        else:
            q = classify_question(req.question or "", None)
            if q.kind != "investigate":
                lookup = self._lookup(run_id, req.question or "", req.as_of, mode, snapshot_id, None)
                return dict(final_status=lookup.status, route="out_of_scope" if q.kind == "out_of_scope" else "lookup",
                            path=fd.LOOKUP_PATH if lookup.status == "answered" else fd.ABSTAIN_PATH,
                            guard=[g.model_dump(mode="json") for g in lookup.guard],
                            contract_check=q.contract.model_dump(mode="json") if q.contract else None,
                            candidates=[], candidate_id=None, city=q.city, abstain_reason=lookup.abstain_reason,
                            report=(fd.build_report(lookup.evidence_card, 1).model_dump(mode="json")
                                    if lookup.evidence_card else None))
            sel = [c for c in fd.candidates_for(q.city or "춘천", req.as_of) if c.selected_for_review]
            if not sel:
                sel = fd.candidates_for(q.city or "춘천", req.as_of)[:1]
            if not sel:
                return dict(final_status="abstained", route="investigate", path=fd.ABSTAIN_PATH, guard=[],
                            contract_check=None, candidates=[], candidate_id=None, city=q.city,
                            abstain_reason="TIME_NO_USABLE_DATA: 이 기준일에는 후보가 없습니다", report=None)
            cand = sel[0]
        card = fd.evidence_card_for(cand, run_id, req.as_of, mode, snapshot_id)
        names = {r.resource_id: r.name for r in fd.RESOURCES}
        return dict(final_status="awaiting_approval", route="investigate", path=fd.INVESTIGATE_PATH,
                    guard=[g.model_dump(mode="json") for g in fd.standard_guard(
                        revise_first=cand.candidate_type == "unexpected_activity_deviation")],
                    contract_check=fd.contract_for(cand).model_dump(mode="json"),
                    candidates=[cand.model_dump(mode="json")], candidate_id=cand.candidate_id, city=cand.city,
                    abstain_reason=None, resource_names=names, resource_scope=_scope_for(cand),
                    report=fd.build_report(card, 1).model_dump(mode="json"))

    def _monitor_card(self, run_id: str, cands: list[Candidate], cities: list[str], as_of: date, mode: str,
                      snapshot_id: str) -> EvidenceCard:
        evidence: list[ToolResult] = []
        facts: list[Fact] = []
        for c in [c for c in cands if c.selected_for_review]:
            ev = fd.candidate_evidence(c, as_of, mode, snapshot_id)
            evidence += ev.items
            facts += ev.facts
        return EvidenceCard(card_id=f"CARD-{run_id}-1", run_id=run_id, title=f"정기 감시 · {'·'.join(cities)} · {as_of}",
                            snapshot_id=snapshot_id, analysis_mode=mode, as_of=as_of, evidence_revision=1,
                            facts=facts, evidence=evidence, limitations=["개인 판단 아님", "검토 배정(K) 후보의 근거만 담음"])

    # ------------------------------------------------------------------ 진행

    def advance(self, run_id: str) -> dict:
        """기한이 지난 예약을 처리하고 실행 행을 돌려준다. 처리할 예약이 없으면 쓰기 트랜잭션을 열지 않는다."""
        now = self.clock()
        run = self.store.get_run(run_id)
        if run is None:
            raise ApiError(404, "RUN_NOT_FOUND", "없는 실행입니다.")
        if not self.store.due_jobs(run_id, now):
            return run
        with self.store.tx():
            run = self.store.get_run(run_id)
            if run is None:
                raise ApiError(404, "RUN_NOT_FOUND", "없는 실행입니다.")
            for job in self.store.due_jobs(run_id, now):
                self._apply(run, job["action"], now)
                self.store.delete_job(job["id"])
                run = self.store.get_run(run_id)
            return run  # type: ignore[return-value]

    def _apply(self, run: dict, action: str, now: datetime) -> None:
        run_id = run["run_id"]
        res = self.store.get_result(run_id) or {}
        if action == "start" and run["status"] == "queued":
            self.store.update_run(run_id, now, status="running", route=res.get("route"))
        elif action == "finish" and run["status"] in ("queued", "running"):
            report = res.get("report")
            if report:
                self.store.insert_report_revision(run_id, 1, 1, run["snapshot_id"], report, "initial", now)
            self.store.update_run(run_id, now, status=res["final_status"], route=res.get("route"),
                                  evidence_revision=1 if report else 0, report_revision=1 if report else 0,
                                  abstain_reason=res.get("abstain_reason"))
        elif action == "regenerate" and run["report_stale"]:
            latest = self.store.latest_report(run_id)
            old = Report.model_validate_json(latest["body"]) if latest else None
            if old is not None:
                judgements = self.store.current_judgements(run_id)
                new = fd.build_report(old.evidence_card, run["report_revision"], judgements, res.get("resource_names"))
                self.store.insert_report_revision(run_id, run["report_revision"], run["evidence_revision"],
                                                  run["snapshot_id"], new.model_dump(mode="json"), "review_changed", now)
            self.store.update_run(run_id, now, report_stale=0)
            self.store.audit("system", "report_regenerated", run_id, {"report_revision": run["report_revision"]}, now)
        elif action == "extra_round" and run["status"] == "running":
            latest = self.store.latest_report(run_id)
            if latest:
                old = Report.model_validate_json(latest["body"])
                card = old.evidence_card.model_copy(update={
                    "evidence_revision": run["evidence_revision"],
                    "card_id": f"CARD-{run_id}-{run['evidence_revision']}",
                })
                new = fd.build_report(card, run["report_revision"], self.store.current_judgements(run_id),
                                      res.get("resource_names"))
                self.store.insert_report_revision(run_id, run["report_revision"], run["evidence_revision"],
                                                  run["snapshot_id"], new.model_dump(mode="json"), "extra_round", now)
            self.store.update_run(run_id, now, status="awaiting_approval", report_stale=0)

    # ------------------------------------------------------------------ 조회

    def status(self, run_id: str) -> RunStatusResponse:
        run = self.advance(run_id)
        res = self.store.get_result(run_id) or {}
        done = run["status"] not in ("queued", "running") or run["extra_rounds"] > 0
        path = res.get("path", [])
        if run["status"] == "queued":
            path = []
        elif run["status"] == "running" and run["extra_rounds"] == 0:
            path = path[: max(1, len(path) // 2)]
        return RunStatusResponse(
            run_id=run_id, status=run["status"], mode=run["mode"], route=run["route"],
            as_of=date.fromisoformat(run["as_of"]), analysis_mode=run["analysis_mode"], snapshot_id=run["snapshot_id"],
            path=path, guard=[GuardResult.model_validate(g) for g in res.get("guard", [])] if done else [],
            contract_check=ContractResult.model_validate(res["contract_check"]) if res.get("contract_check") else None,
            candidates=[Candidate.model_validate(c) for c in res.get("candidates", [])] if done else [],
            evidence_revision=run["evidence_revision"] or None, report_revision=run["report_revision"] or None,
            report_stale=bool(run["report_stale"]), abstain_reason=run["abstain_reason"],
            errors=[{"error_code": run["error_code"]}] if run["error_code"] else [],
            decision_trace=fake_trace(path, res, run) if path else [],
            published_report_revision=self._published(run_id), extra_rounds=run["extra_rounds"],
        )

    def _published(self, run_id: str) -> int | None:
        latest = self.store.latest_report(run_id)
        return latest["report_revision"] if latest else None

    # ------------------------------------------------------------------ 실행에 묶인 자원 · 이어 묻기

    def resources(self, run_id: str) -> RunResourcesResponse:
        """이 실행의 추천 자원 (시작할 때 고정한 범위). adm/group 으로 새로 찾지 않는다."""
        run = self.advance(run_id)
        if run["status"] in ("queued", "running"):
            raise ApiError(409, "RUN_NOT_READY", "실행이 아직 끝나지 않았습니다. 잠시 뒤 다시 보세요.")
        res = self.store.get_result(run_id) or {}
        scope = self._resource_scope(res)
        judged = self.store.current_judgements(run_id)
        items: list[RunResource] = []
        if res.get("candidate_id"):
            c = Candidate.model_validate(res["candidates"][0])
            for adm in [a for a in [c.adm_cd, *c.check_first_adm] if a] or [None]:
                for r in fd.resources_for(c.city, adm, None, None):
                    if r.resource_id in scope and r.resource_id not in {x.resource_id for x in items}:
                        items.append(RunResource(**r.model_dump(), candidate_id=c.candidate_id,
                                                 candidate_ids=[c.candidate_id], judgement=judged.get(r.resource_id, (None, None))[0]))
        return RunResourcesResponse(run_id=run_id, snapshot_id=run["snapshot_id"],
                                    evidence_revision=run["evidence_revision"] or None,
                                    report_revision=self._published(run_id), items=items)

    def followup(self, run_id: str, req: FollowupRequest, who: Identity = DEV_IDENTITY) -> FollowupResponse:
        """이어 묻기: 발행된 보고서의 근거 카드만 읽음 (가짜도 같은 Guard 를 지남)."""
        from bigcontest.agent.followup import answer_followup

        run = self.advance(run_id)
        latest = self.store.latest_report(run_id)
        if run["status"] in ("queued", "running") or latest is None:
            raise ApiError(409, "RUN_NOT_READY", "보고서가 나온 실행에만 이어 물을 수 있습니다.")
        res = self.store.get_result(run_id) or {}
        values = {"report": Report.model_validate_json(latest["body"]).model_dump(mode="json"),
                  "candidates": res.get("candidates") or [], "report_revision": latest["report_revision"]}
        return self._followup_out(run_id, answer_followup(values, req.question, None), who)

    def _followup_out(self, run_id: str, out: dict, who: Identity) -> FollowupResponse:
        now = self.clock()
        fid = new_id("fup", now)
        with self.store.tx():
            for rec in out.get("llm_log") or []:
                self.store.insert_llm_call(run_id, "followup", rec, now)
            self.store.audit(who.actor(), "followup_" + out["status"], run_id,
                             {"followup_id": fid, "cited": len(out["cited_evidence_ids"]),
                              "source": out["source"], "report_revision": out["report_revision"]}, now)
        return FollowupResponse(run_id=run_id, followup_id=fid,
                                **{k: v for k, v in out.items() if k != "llm_log"})

    def _latest_report(self, run_id: str) -> tuple[dict, Report]:
        run = self.advance(run_id)
        latest = self.store.latest_report(run_id)
        if latest is None:
            if run["status"] in ("queued", "running"):
                raise ApiError(409, "RUN_NOT_READY", "실행이 아직 끝나지 않았습니다. 잠시 뒤 다시 보세요.")
            raise ApiError(404, "EVIDENCE_NOT_FOUND", "이 실행에는 근거 카드가 없습니다.")
        return run, Report.model_validate_json(latest["body"])

    def evidence(self, run_id: str) -> EvidenceResponse:
        run, report = self._latest_report(run_id)
        return EvidenceResponse(run_id=run_id, snapshot_id=run["snapshot_id"], evidence_card=report.evidence_card)

    def report(self, run_id: str) -> ReportResponse:
        run, report = self._latest_report(run_id)
        return ReportResponse(run_id=run_id, snapshot_id=run["snapshot_id"], report_stale=bool(run["report_stale"]),
                              report=report)

    def history(self, run_id: str) -> RunHistoryResponse:
        self.advance(run_id)
        return RunHistoryResponse(
            run_id=run_id,
            report_revisions=[ReportRevisionInfo(**{k: r[k] for k in ReportRevisionInfo.model_fields})
                              for r in self.store.list_report_revisions(run_id)],
            reviews=[ReviewRecord(**{k: r[k] for k in ReviewRecord.model_fields}) for r in self.store.list_reviews(run_id)],
            approvals=[ApprovalRecord(**{k: r[k] for k in ApprovalRecord.model_fields})
                       for r in self.store.list_approvals(run_id)],
            finalized=[f["step"] for f in self.store.list_finalizations(run_id)],
        )

    def list(self, candidate_id: str | None, limit: int) -> RunListResponse:
        items = []
        for r in self.store.list_runs(candidate_id, limit):
            r = self.advance(r["run_id"])
            meta = self.store.get_run_metadata(r["run_id"]) or {}
            res = self.store.get_result(r["run_id"]) or {}
            items.append(RunSummary(
                run_id=r["run_id"], mode=r["mode"], status=r["status"], route=r["route"], question=r["question"],
                candidate_id=r["candidate_id"], city=res.get("city"), as_of=r["as_of"],
                analysis_mode=r["analysis_mode"], snapshot_id=r["snapshot_id"],
                cleaning_rule_version=meta.get("cleaning_rule_version"), rule_version=meta.get("rule_version"),
                ledger_version=meta.get("ledger_version"), evidence_revision=r["evidence_revision"],
                report_revision=r["report_revision"], report_stale=bool(r["report_stale"]),
                extra_rounds=r["extra_rounds"], created_at=r["created_at"], updated_at=r["updated_at"]))
        return RunListResponse(items=items)

    # ------------------------------------------------------------------ 후속 확인 모아 보기

    def _run_city(self, run_id: str) -> str | None:
        return (self.store.get_result(run_id) or {}).get("city")

    def followups(self, city: str | None = None, date_from: date | None = None, date_to: date | None = None,
                  as_of: date | None = None) -> FollowUpListResponse:
        """후보마다 가장 최근 조사 실행의 마지막 후속 확인일 (화면이 실행마다 /history 를 부르지 않게).
        도시·기간으로 거르고, as_of 를 주면 지남 / 이번 주(7일 안) / 나중을 붙인다. 후속 확인일 순."""
        latest: dict[str, dict] = {}
        for r in self.store.candidate_runs():
            latest.setdefault(r["candidate_id"], r)
        items = []
        for cid, r in latest.items():
            rev = self.store.last_follow_up(r["run_id"])
            if rev is None:
                continue
            on = date.fromisoformat(rev["follow_up_on"])
            if (date_from and on < date_from) or (date_to and on > date_to):
                continue
            run_city = self._run_city(r["run_id"])
            if city and run_city != city:
                continue
            status = self.advance(r["run_id"])["status"]
            state = None
            if as_of is not None:
                d = (on - as_of).days
                state = "지남" if d < 0 else ("이번 주" if d <= 7 else "나중")
            items.append(FollowUpItem(candidate_id=cid, run_id=r["run_id"], city=run_city, follow_up_on=on,
                                      memo=rev["memo"], review_id=rev["review_id"], reviewer=rev["reviewer"],
                                      run_status=status, approved=status == "approved", state=state))
        items.sort(key=lambda x: (x.follow_up_on, x.candidate_id))
        return FollowUpListResponse(as_of=as_of, city=city, date_from=date_from, date_to=date_to, items=items)

    # ------------------------------------------------------------------ 현장 확인 결과

    def _run_candidates(self, run_id: str) -> list[dict]:
        return list((self.store.get_result(run_id) or {}).get("candidates") or [])

    def record_outcome(self, run_id: str, req: OutcomeRequest, who: Identity = DEV_IDENTITY) -> OutcomeResponse:
        """승인된 조사 실행의 후보(동·집단) 하나에 현장 확인 건수를 적음. 같은 후보를 다시 적으면 새 기록이 기준.
        같은 멱등 키 + 같은 내용 → 첫 기록 (duplicate), 같은 키 + 다른 내용 → 409. 거절도 감사 로그에."""
        run = self.advance(run_id)
        cands = {c["candidate_id"]: c for c in self._run_candidates(run_id)}
        now = self.clock()
        body = req.model_dump(exclude={"idempotency_key"})
        with self.store.tx():
            first = self.store.get_outcome_by_key(req.idempotency_key)
            rejected: ApiError | None = None
            if first is not None:
                same = first["run_id"] == run_id and all(first[k] == v for k, v in body.items())
                if same:
                    return OutcomeResponse(run_id=run_id, outcome=_outcome(first), duplicate=True)
                rejected = ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 멱등 키로 다른 결과를 보냈습니다.")
            elif self.store.get_run(run_id)["status"] != "approved":
                rejected = ApiError(409, "RUN_NOT_APPROVED",
                                    "승인된 조사 실행에만 현장 확인 결과를 적을 수 있습니다.")
            elif req.candidate_id not in cands:
                rejected = ApiError(400, "CANDIDATE_NOT_IN_RUN", f"이 실행에 없는 후보 ID입니다: {req.candidate_id}")
            elif (msg := O.check_counts(body)) is not None:
                rejected = ApiError(400, "INVALID_REQUEST", msg)
            if rejected is not None:
                self.store.audit(who.actor(), "outcome_rejected", run_id,
                                 {"error_code": rejected.code, "candidate_id": req.candidate_id}, now)
            else:
                c = cands[req.candidate_id]
                oid = new_id("out", now)
                self.store.insert_outcome(
                    outcome_id=oid, run_id=run_id, candidate_id=req.candidate_id, city=c["city"],
                    adm_cd=c.get("adm_cd") or ((c.get("check_first_adm") or [None])[0]),
                    group_label=c.get("service_eligibility_group"), trigger_code=O.trigger_code_of(c),
                    event_id=c.get("event_id"), **{k: body[k] for k in O.COUNT_FIELDS},
                    recorded_by=who.name, idempotency_key=req.idempotency_key, created_at=now.isoformat())
                # 건수는 개인 정보가 아니라 감사 로그에 그대로 둠 (고쳐 적은 흐름을 볼 수 있게)
                self.store.audit(who.actor(), "outcome_recorded", run_id,
                                 {"outcome_id": oid, "candidate_id": req.candidate_id,
                                  **{k: body[k] for k in O.COUNT_FIELDS}}, now)
                saved = self.store.get_outcome_by_key(req.idempotency_key)
        if rejected is not None:
            raise rejected
        return OutcomeResponse(run_id=run_id, outcome=_outcome(saved))

    def outcomes(self, run_id: str) -> OutcomeListResponse:
        """실행 하나의 결과 (그 실행 상세 화면용이라 10 미만도 그대로). 후보마다 가장 최근 기록."""
        self.advance(run_id)
        rows = self.store.list_outcomes(run_id=run_id)
        return OutcomeListResponse(run_id=run_id, items=[_outcome(r) for r in O.latest(rows)],
                                   history_count=len(rows))

    def outcome_summary(self, city: str | None, by: str) -> OutcomeSummaryResponse:
        """모아 보기: 도시 × 트리거(또는 사건)별 합계. 10 미만 칸은 숫자 없이 (small_cells)."""
        rows = self.store.list_outcomes(city=city)
        return OutcomeSummaryResponse(city=city, by=by, small_cell=O.SMALL_CELL,
                                      rows=[OutcomeSummaryRow(**r) for r in O.summarize(rows, by)])

    # ------------------------------------------------------------------ 검토

    @staticmethod
    def _who(who: Identity, claimed: str) -> tuple[str, str, dict]:
        """(기록할 이름, 감사 actor, 감사 detail 추가분)."""
        name = who.name if who.authenticated else claimed
        extra = {"claimed_actor": claimed} if claimed != who.actor() else {}
        return name, who.actor(), extra

    def review(self, run_id: str, req: ReviewRequest, who: Identity = DEV_IDENTITY) -> ReviewResponse:
        """검토 저장. 확인한 근거는 이 실행 카드의 근거 ID, 자원은 이 실행의 추천 범위 안이어야 함.

        응답의 report_stale 은 저장 뒤 실행의 실제 상태, changed 는 이번 저장으로 판정이 바뀌었는지.
        거절도 감사 로그(review_rejected)에 남긴다.
        """
        self.advance(run_id)
        now = self.clock()
        name, actor, extra = self._who(who, req.reviewer)
        rejected: ApiError | None = None
        with self.store.tx():
            run = self.store.get_run(run_id)
            assert run is not None
            rejected = self._check_review(run, req)
            if rejected is not None:
                self.store.audit(actor, "review_rejected", run_id,
                                 {"error_code": rejected.code, "report_revision": req.report_revision, **extra}, now)
            else:
                prev = self.store.current_judgements(run_id)
                changed = any(prev.get(j.resource_id, (None, None))[0] != j.judgement for j in req.resource_judgements)
                before = run["report_revision"]
                after = before
                if changed:
                    if not run["report_stale"]:
                        after = before + 1
                    self.store.update_run(run_id, now, report_revision=after, report_stale=1)
                    self._schedule_regenerate(run_id, now)
                review_id = new_id("rev", now)
                self.store.insert_review(review_id, run_id, name, req.checked_evidence_ids, req.memo,
                                         req.follow_up_on.isoformat() if req.follow_up_on else None, before, after,
                                         [j.model_dump() for j in req.resource_judgements], now)
                self.store.audit(actor, "review_saved", run_id,
                                 {"review_id": review_id, "report_revision": after, "changed": changed,
                                  "reviewed_report_revision": before, "evidence_revision": run["evidence_revision"],
                                  **extra}, now)
                saved = self.store.get_run(run_id)
        if rejected is not None:
            raise rejected
        return ReviewResponse(run_id=run_id, review_id=review_id, report_revision=after,
                              report_stale=bool(saved["report_stale"]), changed=changed,
                              evidence_revision=saved["evidence_revision"] or None)

    def _schedule_regenerate(self, run_id: str, now: datetime) -> None:
        self.store.clear_jobs(run_id, "regenerate")
        self.store.add_job(run_id, "regenerate", now + self.delay)

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
        scope = self._resource_scope(self.store.get_result(run["run_id"]) or {})
        bad_res = [j.resource_id for j in req.resource_judgements if j.resource_id not in scope]
        if bad_res:
            return ApiError(400, "RESOURCE_NOT_IN_RUN", "이 실행의 추천 범위에 없는 자원 ID입니다: " + ", ".join(bad_res[:5]))
        return None

    @staticmethod
    def _resource_scope(res: dict) -> set[str]:
        """이 실행에서 판정할 수 있는 자원: 시작할 때 고정한 범위 (없으면 후보의 도시·동으로 다시 계산)."""
        if res.get("resource_scope") is not None:
            return set(res["resource_scope"])
        cands = res.get("candidates") or []
        if not cands:
            return set()
        return set(_scope_for(Candidate.model_validate(cands[0])))

    # ------------------------------------------------------------------ 승인

    def approve(self, run_id: str, req: ApproveRequest, who: Identity = DEV_IDENTITY) -> ApproveResponse:
        self.advance(run_id)
        now = self.clock()
        name, actor, extra = self._who(who, req.approver)
        rejected: ApiError | None = None
        with self.store.tx():
            run = self.store.get_run(run_id)
            assert run is not None
            first = self.store.get_approval_by_key(req.idempotency_key)
            if first is not None:
                if (first["run_id"], first["decision"], first["report_revision"]) == (
                        run_id, req.decision, req.report_revision):
                    return ApproveResponse(run_id=run_id, decision=first["decision"],
                                           report_revision=first["report_revision"], approval_id=first["approval_id"],
                                           duplicate=True)
                rejected = ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 멱등 키로 다른 결정을 보냈습니다.")
            else:
                rejected = self._check(run, req) or self._check_focus(req, self._candidate_ids(run_id))
            if rejected is not None:
                # 거절 기록은 이 트랜잭션과 함께 커밋하고, 오류는 with 블록을 나온 뒤에 낸다.
                self.store.audit(actor, "approval_rejected", run_id,
                                 {"error_code": rejected.code, "decision": req.decision,
                                  "report_revision": req.report_revision, **extra}, now)
            else:
                approval_id = new_id("apr", now)
                self.store.insert_approval(approval_id, run_id, req.idempotency_key, req.decision,
                                           req.report_revision, name, req.memo, now)
                self.store.audit(actor, "approval_received", run_id,
                                 {"approval_id": approval_id, "decision": req.decision,
                                  "report_revision": req.report_revision, **extra}, now)
                if req.decision in ("approved", "held"):
                    self.store.finalize(run_id, "finalize", {"decision": req.decision, "approval_id": approval_id,
                                                             "report_revision": req.report_revision}, now)
                    self.store.update_run(run_id, now, status=req.decision)
                else:
                    self.store.update_run(run_id, now, status="running", extra_rounds=run["extra_rounds"] + 1,
                                          evidence_revision=run["evidence_revision"] + 1,
                                          report_revision=run["report_revision"] + 1)
                    self.store.add_job(run_id, "extra_round", now + self.delay)
        if rejected is not None:
            raise rejected
        if self.delay.total_seconds() <= 0:
            self.advance(run_id)
        return ApproveResponse(run_id=run_id, decision=req.decision, report_revision=req.report_revision,
                               approval_id=approval_id, duplicate=False)

    def _candidate_ids(self, run_id: str) -> set[str]:
        return {c["candidate_id"] for c in (self.store.get_result(run_id) or {}).get("candidates") or []}

    @staticmethod
    def _check_focus(req: ApproveRequest, run_candidates: set[str]) -> ApiError | None:
        """추가 조사 범위의 후보 ID 는 이 실행의 후보만 (검토 저장과 같은 규칙)."""
        bad = [c for c in (req.focus.candidate_ids if req.focus else []) if c not in run_candidates]
        if bad:
            return ApiError(400, "CANDIDATE_NOT_IN_RUN", "이 실행에 없는 후보 ID입니다: " + ", ".join(bad[:5]))
        return None

    @staticmethod
    def _check(run: dict, req: ApproveRequest) -> ApiError | None:
        """승인할 수 없으면 오류를 돌려준다 (raise 하지 않음)."""
        if run["status"] == "abstained" or (run["abstain_reason"] and req.decision == "approved"):
            return ApiError(409, "APPROVAL_BLOCKED_BY_ABSTAIN", "계약 위반으로 보류된 실행은 승인으로 풀 수 없습니다.")
        if run["status"] != "awaiting_approval":
            return not_awaiting(run["status"], "승인·보류·추가 조사를 할")
        if run["report_stale"]:
            return ApiError(409, "REPORT_REVISION_STALE",
                            f"보고서를 {run['report_revision']}판으로 다시 만드는 중입니다. 새 판을 확인한 뒤 승인하세요.")
        if req.report_revision != run["report_revision"]:
            return ApiError(409, "REPORT_REVISION_STALE",
                            f"보고서가 {run['report_revision']}판으로 바뀌었습니다. 다시 확인한 뒤 승인하세요.")
        if req.decision == "more" and run["extra_rounds"] >= 2:
            return ApiError(409, "EXTRA_ROUNDS_EXCEEDED", "추가 조사는 2번까지입니다. 승인하거나 보류하세요.")
        return None

    # ------------------------------------------------------------------ 질의응답

    def qa(self, req: QARequest, who: Identity = DEV_IDENTITY) -> QAResponse:
        snap = self.snapshots.active()
        now = self.clock()
        with self.store.tx():
            run_id = self.store.next_run_id(now)
            out = self._lookup(run_id, req.question, req.as_of, req.analysis_mode, snap.snapshot_id, req.city)
            q = classify_question(req.question, req.city)
            self.store.insert_run(
                run_id=run_id, mode="qa", question=req.question, candidate_id=None, as_of=req.as_of.isoformat(),
                analysis_mode=req.analysis_mode, snapshot_id=snap.snapshot_id, status=out.status,
                route="out_of_scope" if q.kind == "out_of_scope" else "lookup",
                evidence_revision=1 if out.evidence_card else 0, report_revision=1 if out.evidence_card else 0,
                report_stale=0, extra_rounds=0, abstain_reason=out.abstain_reason, error_code=None,
                created_at=now.isoformat(), updated_at=now.isoformat())
            self.store.insert_run_metadata(run_id, snap.snapshot_id, snap.cleaning_rule_version, snap.rule_version,
                                           snap.ledger_version, {"source": "fake", "city": q.city}, now)
            self.store.put_result(run_id, dict(
                final_status=out.status, route="lookup", city=q.city,
                path=fd.LOOKUP_PATH if out.status == "answered" else fd.ABSTAIN_PATH,
                guard=[g.model_dump(mode="json") for g in out.guard],
                contract_check=q.contract.model_dump(mode="json") if q.contract else None, candidates=[]))
            if out.evidence_card:
                self.store.insert_report_revision(run_id, 1, 1, snap.snapshot_id,
                                                  fd.build_report(out.evidence_card, 1).model_dump(mode="json"),
                                                  "initial", now)
            self.store.audit(who.actor(), "run_created", run_id, {"mode": "qa", "status": out.status}, now)
        return out

    def _lookup(self, run_id: str, question: str, as_of: date, mode: str, snapshot_id: str,
                city: str | None) -> QAResponse:
        q = classify_question(question, city)
        if q.kind in ("abstain", "out_of_scope"):
            return QAResponse(run_id=run_id, status="abstained", abstain_reason=q.reason, needed_data=q.needed)
        city = q.city or "강남"
        if q.kind == "structure":
            ranks = fd.structure_ranks(city)
            top = sorted(ranks.items(), key=lambda x: x[1])[:3]
            month_end = date(as_of.year, as_of.month, 1) - timedelta(days=1)
            evidence, facts = [], []
            for i, (cd, rank) in enumerate(top, start=1):
                a = fd.areas()[cd]
                v = fd.structure_value(a)
                eid = f"EV-{as_of:%Y%m%d}-{run_id[-4:]}{i}"
                evidence.append(ToolResult(
                    evidence_id=eid, metric="1인세대 비율", value=v, unit="비율", denominator="전체 세대수 (주민등록)",
                    n_obs=1, measured_grain="행정동 × 월", applied_scope=f"{city} {a.adm_nm}",
                    analysis_mode=mode, as_of=as_of, observed_from=month_end, observed_to=month_end,
                    simulated_available_at=min(as_of, month_end + timedelta(days=10)), availability_basis="assumed",
                    snapshot_id=snapshot_id, interpretation_status="직접 확인",
                    valid_claims=[f"1인세대 비율 {city} 내 {rank}위"], forbidden_claims=["1인가구 (개념이 다름)"],
                    status="OK", value_state="observed"))
                facts.append(Fact(metric="1인세대 비율", region=f"{city} {a.adm_nm}", period=f"{month_end:%Y-%m}",
                                  value=v, unit="비율", evidence_id=eid))
            card = EvidenceCard(card_id=f"CARD-{run_id}-1", run_id=run_id, title=f"{city} 1인세대 비율 상위 동",
                                snapshot_id=snapshot_id, analysis_mode=mode, as_of=as_of, evidence_revision=1,
                                facts=facts, evidence=evidence, limitations=["주민등록 1인세대는 1인가구와 다름"])
            answer = " ".join(f"{month_end.year}년 {month_end.month}월 주민등록 기준 {f.region}의 1인세대 비율은 {f.value}입니다 [{f.evidence_id}]."
                              for f in facts)
        else:  # investigate 요약: 배정 후보의 근거
            sel = [c for c in fd.candidates_for(city, as_of) if c.selected_for_review]
            if not sel:
                return QAResponse(run_id=run_id, status="abstained",
                                  abstain_reason=f"TIME_NO_USABLE_DATA: {as_of} {city}에는 검토 배정된 후보가 없음",
                                  needed_data=["다른 기준일 (예: 2025-09-26 추석 D-7, 2025-11-14 동절기)"])
            c = sel[0]
            card = fd.evidence_card_for(c, run_id, as_of, mode, snapshot_id)
            names = ", ".join(fd.candidate_title(x).split(" · ")[2] for x in sel)
            answer = " ".join(fd.build_report(card, 1).fact_sentences)
            answer += f" 검토 배정된 후보: {names}. 자세한 근거와 승인은 후보 상세에서 하세요."
        guard = fd.standard_guard()
        return QAResponse(run_id=run_id, status="answered", evidence_card=card, answer=answer, guard=guard)


def _outcome(row: dict) -> OutcomeRecord:
    return OutcomeRecord(**{**{k: v for k, v in row.items() if k not in ("group_label", "idempotency_key", "seq")},
                            "group": row.get("group_label")})


def fake_trace(path: list[str], res: dict, run: dict) -> list[TraceStep]:
    """가짜 실행의 판단 기록. 진짜 그래프와 같은 칸: 노드·고른 것·누가 정했나·규칙 ID."""
    guards = [GuardResult.model_validate(g) for g in res.get("guard", [])]
    cands = [Candidate.model_validate(c) for c in res.get("candidates", [])]
    cc = res.get("contract_check") or {}
    levels: dict[str, int] = {}
    for c in cands:
        levels[c.candidate_level or "-"] = levels.get(c.candidate_level or "-", 0) + 1
    seen: dict[str, int] = {}
    out = []
    for i, node in enumerate(path):
        k = seen[node] = seen.get(node, 0) + 1
        step = TraceStep(node=node, next=path[i + 1] if i + 1 < len(path) else None, source="rule",
                         reason="가짜 응답: 미리 만든 결과")
        if node == "classify":
            step.choice = run["route"]
        elif node == "check_availability":
            step.choice = cc.get("status") or "OK"
            step.rule_ids = [x["code"] for x in cc.get("issues", [])]
        elif node in ("guard_signal", "guard_report"):
            mine = [g for g in guards if g.guard == node and g.attempt == k] or [g for g in guards if g.guard == node]
            step.source = "guard"
            step.choice = "ABSTAIN" if any(g.verdict == "ABSTAIN" for g in mine) else (
                "REVISE" if any(g.verdict == "REVISE" for g in mine) else "PASS")
            step.rule_ids = sorted({r for g in mine for r in g.rule_ids})
        elif node == "assign_level":
            step.choice = " · ".join(f"{lv} {n}" for lv, n in levels.items()) or "없음"
        elif node == "abstain_report":
            step.choice, step.source = "판단 보류", "template"
            step.reason = (run["abstain_reason"] or "")[:200] or None
        elif node == "write_report":
            step.choice, step.source = f"보고서 {run['report_revision'] or 1}판", "template"
        elif node == "await_approval":
            step.source, step.choice = "human", run["status"]
        out.append(step)
    return out


def _scope_for(c: Candidate) -> list[str]:
    """후보의 동(없으면 먼저 확인할 동, 그것도 없으면 도시)에 추천되는 자원 ID. 화면의 자원 목록을 모두 포함."""
    adms = [a for a in [c.adm_cd, *c.check_first_adm] if a] or [None]
    ids: list[str] = []
    for adm in adms:
        for r in fd.resources_for(c.city, adm, None, None):
            if r.resource_id not in ids:
                ids.append(r.resource_id)
    return ids


# ---------------------------------------------------------------------- 질문 분류 (가짜 규칙)


class _Q:
    def __init__(self, kind: str, city: str | None, reason: str | None = None, needed: list[str] | None = None,
                 contract: ContractResult | None = None):
        self.kind = kind
        self.city = city
        self.reason = reason
        self.needed = needed or []
        self.contract = contract


_AGE = r"(\d0대|노인|고령|어르신|청년|중장년)"
_NIGHT = r"(밤|야간|새벽|저녁|시간대|심야)"


def classify_question(question: str, city: str | None) -> _Q:
    q = question.strip()
    if city is None:
        city = "강남" if "강남" in q else "춘천" if "춘천" in q else None

    def abstain(code: str, rule: str, msg: str, needed: list[str]) -> _Q:
        contract = ContractResult(status="ERROR", issues=[ContractIssue(code=code, rule=rule, message=msg)])
        return _Q("abstain", city, f"{code}: {msg}", needed, contract)

    if re.search(_AGE + r".*" + _NIGHT, q) or re.search(_NIGHT + r".*" + _AGE, q):
        return abstain("GRAIN_FORBIDDEN_COMBO", "G-02",
                       "체류인구: 연령 × 시간대 조합은 지원하지 않음. 통신 성연령 표와 시간대 표는 따로라서 "
                       "\"특정 연령의 시간대 체류\"는 알 수 없음",
                       ["연령 × 시간대가 함께 있는 통신 자료", "또는: 카드 데이터1의 60대 18~23시 결제(도시 단위, 결제 기준)"])
    if city == "춘천" and re.search(r"(시민생활|외로움|소통)", q):
        return abstain("CITY_NOT_COVERED", "G-01", "춘천은 서울 시민생활 데이터가 없음",
                       ["춘천 시민생활 데이터 (없음)", "대신 볼 수 있는 것: 주민등록 1인세대 비율"])
    if re.search(r"(동별|행정동|동마다|이 동)", q) and re.search(r"(결제|카드|소비)", q):
        return abstain("DIMENSION_NOT_AVAILABLE", "G-01", "카드 데이터에는 행정동 차원이 없음 (가맹점 시군구 단위)",
                       ["행정동 단위 카드 자료 (없음)", "대신: 도시 × 코호트 결제 + 구조 취약성 순위로 '먼저 확인할 동'"])
    if re.search(r"(누가|누구|개인|사람).*(고립|위험)", q):
        return _Q("out_of_scope", city, "OUT_OF_SCOPE: 개인을 판정하지 않습니다. 동·집단 단위로만 답합니다",
                  ["질문을 동·집단 단위로 바꿔 주세요 (예: \"강남 1인세대 비율 상위 동은?\")"])
    if "1인세대" in q or "1인 세대" in q:
        return _Q("structure", city)
    if re.search(r"(어디부터|먼저|확인해야|점검|추석|연휴|동절기|겨울)", q):
        return _Q("investigate", city)
    return _Q("out_of_scope", city, "OUT_OF_SCOPE: 지원하는 지표로 바꿀 수 없는 질문",
              ["지원 지표: 1인세대 비율, 카드 결제(도시 단위), 체류인구(행정동 단위), 복지자원",
               "예: \"강남 1인세대 비율 상위 동은?\", \"이번 추석 전에 춘천에서 어디부터 확인해야 해?\""])
