"""실제 그래프 API 테스트: API ↔ LangGraph 에이전트를 한 계약으로.

- 대부분 가짜 도구(stub) + 가짜 LLM 으로 빠르게. 마지막 하나는 실제 도구(dev 스냅샷, 자료가 없으면 건너뜀)
- 유료 LLM 은 부르지 않는다
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app  # noqa: E402
from api.models import ReportResponse, RunHistoryResponse  # noqa: E402
from bigcontest.agent.schemas import (  # noqa: E402
    ApproveResponse,
    CandidateListResponse,
    FollowUpListResponse,
    MapResponse,
    OverviewResponse,
    ResourceListResponse,
    ErrorResponse,
    EvidenceResponse,
    FollowupResponse,
    QAResponse,
    ReviewResponse,
    RunAccepted,
    RunResourcesResponse,
    RunStatusResponse,
    SnapshotInfo,
)
from bigcontest.agent.tools.datasource import dev_available  # noqa: E402

Q = "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?"
AS_OF = "2025-09-26"


def make(tmp_path, name="state.sqlite", **kw):
    kw.setdefault("delay_s", 0)
    return create_app(db_path=tmp_path / name, backend="real", tools_backend=kw.pop("tools", "stub"),
                      llm=kw.pop("llm", "fake"), **kw)


def close(app):
    app.state.runs.close()
    app.state.store.close()


@pytest.fixture
def real(tmp_path):
    app = make(tmp_path)
    with TestClient(app) as c:
        yield c
    close(app)


def ok(resp, model, status=200):
    assert resp.status_code == status, resp.text
    if not resp.request.url.path.startswith("/data/capabilities"):
        assert resp.headers["X-Data-Source"] == "real", resp.request.url.path
    return model.model_validate(resp.json())


def err(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    ErrorResponse.model_validate(body)
    assert body["error_code"] == code, body
    return body


def start(c, **kw) -> RunAccepted:
    body = {"mode": "qa", "question": Q, "as_of": AS_OF, **kw}
    return ok(c.post("/runs", json=body), RunAccepted, 202)


def status(c, run_id) -> RunStatusResponse:
    return ok(c.get(f"/runs/{run_id}"), RunStatusResponse)


def approve(c, run_id, rev, key, decision="approved", **kw):
    return c.post(f"/runs/{run_id}/approve", json={"decision": decision, "report_revision": rev,
                                                   "approver": "lead-01", "idempotency_key": key, **kw})


def report(c, run_id) -> ReportResponse:
    r = c.get(f"/runs/{run_id}/report")
    assert r.status_code == 200, r.text
    return ReportResponse.model_validate(r.json())


def audit(c, run_id, action):
    return [json.loads(a["detail"]) for a in c.app.state.store.audit_rows(run_id) if a["action"] == action]


# ---------------------------------------------------------------- 그래프 상태 ↔ API 상태


def test_backend_marker_and_monitoring_publish(real):
    assert real.get("/health").headers["X-Data-Source"] == "real"
    assert real.get("/overview", params={"as_of": AS_OF, "city": "춘천"}).headers["X-Data-Source"] == "real"
    snap = ok(real.get("/snapshots/active"), SnapshotInfo)
    acc = ok(real.post("/runs", json={"mode": "monitoring", "as_of": AS_OF, "city": "춘천"}), RunAccepted, 202)
    assert acc.snapshot_id == snap.snapshot_id == "snap-stub-0001"
    st = status(real, acc.run_id)
    assert st.status == "published" and st.path[-1] == "publish_candidates" and st.candidates
    assert [t.node for t in st.decision_trace] == st.path  # 판단 기록이 지나온 노드와 같음
    assert st.report_revision == st.published_report_revision == report(real, acc.run_id).report.report_revision
    err(approve(real, acc.run_id, st.report_revision, "m-1"), 409, "RUN_NOT_AWAITING_APPROVAL")


def test_open_candidate_from_monitoring_run(real):
    mon = ok(real.post("/runs", json={"mode": "monitoring", "as_of": AS_OF, "city": "춘천"}), RunAccepted, 202)
    cid = status(real, mon.run_id).candidates[0].candidate_id
    acc = start(real, question=None, candidate_id=cid)
    st = status(real, acc.run_id)
    assert st.status == "awaiting_approval" and st.route == "investigate"
    assert {c.city for c in st.candidates} == {"춘천"}
    # 그 후보만 조사. 등급은 정기 감시와 같고 카드 제목은 후보 하나
    mon_c = status(real, mon.run_id).candidates[0]
    assert [c.candidate_id for c in st.candidates] == [cid]
    assert st.candidates[0].candidate_level == mon_c.candidate_level
    title = report(real, acc.run_id).report.evidence_card.title
    assert title.startswith(f"후보 {cid} ·") and "건" not in title
    assert real.app.state.runs._inputs(acc.run_id)["scope_candidate_ids"] == [cid]
    err(real.post("/runs", json={"mode": "qa", "candidate_id": cid, "candidate_ids": ["C-없음"], "as_of": AS_OF}),
        400, "CANDIDATE_NOT_IN_RUN")
    err(real.post("/runs", json={"mode": "qa", "candidate_id": "C-없음", "as_of": AS_OF}), 400, "INVALID_REQUEST")
    # 화면이 읽은 정기 감시 실행(monitoring_run_id)과 도시를 주면 그 실행·도시에서만 찾음
    pinned = start(real, question=None, candidate_id=cid, city="춘천", monitoring_run_id=mon.run_id)
    assert real.app.state.runs._inputs(pinned.run_id)["source_run_id"] == mon.run_id
    body = {"mode": "qa", "candidate_id": cid, "as_of": AS_OF}
    err(real.post("/runs", json={**body, "city": "강남"}), 400, "INVALID_REQUEST")
    err(real.post("/runs", json={**body, "monitoring_run_id": "run-없음"}), 400, "INVALID_REQUEST")


def test_investigate_awaits_with_guard_trace_and_pinned_snapshot(real):
    acc = start(real)
    st = status(real, acc.run_id)
    assert st.status == "awaiting_approval" and st.route == "investigate" and st.path[-1] == "guard_report"
    assert {g.guard for g in st.guard} == {"guard_signal", "guard_report"}
    assert st.decision_trace and st.decision_trace[-1].node == "guard_report"
    assert st.report_revision == st.published_report_revision and not st.report_stale
    ev = ok(real.get(f"/runs/{acc.run_id}/evidence"), EvidenceResponse)
    assert ev.snapshot_id == acc.snapshot_id
    assert {e.snapshot_id for e in ev.evidence_card.evidence} == {acc.snapshot_id}
    hist = real.get(f"/runs/{acc.run_id}/history").json()
    assert [r["reason"] for r in hist["report_revisions"]] == ["initial"]


def test_guard_revise_skips_unpublished_revision(tmp_path):
    """Guard 가 고친 초안(1판)은 발행하지 않음 → 발행 판 2, 승인도 2판으로만."""
    app = make(tmp_path, llm="fake_wrong_fact_once")
    with TestClient(app) as c:
        acc = start(c)
        st = status(c, acc.run_id)
        assert st.report_revision == st.published_report_revision == 2
        assert [g.verdict for g in st.guard if g.guard == "guard_report"][:1] == ["REVISE"]
        err(approve(c, acc.run_id, 1, "k1"), 409, "REPORT_REVISION_STALE")
        ok(approve(c, acc.run_id, 2, "k2"), ApproveResponse)
    close(app)


# ---------------------------------------------------------------- 자원·검토 (실행에 묶임)


def test_resources_bound_to_run_and_review_ids_checked(real):
    acc = start(real)
    res = ok(real.get(f"/runs/{acc.run_id}/resources"), RunResourcesResponse)
    assert res.snapshot_id == acc.snapshot_id and res.items
    assert all(x.candidate_id for x in res.items)
    ids = {x.resource_id for x in res.items}
    state_ids = {r["resource_id"] for r in real.app.state.runs.agent.status(acc.run_id)["values"]["resources"]}
    assert ids == state_ids  # 그래프가 고른 자원 그대로 (adm/group 으로 새로 찾지 않음)
    # 자원 하나에 한 줄 (판정은 자원 ID 단위), 같은 자원을 추천받은 후보는 candidate_ids 에 모두
    assert len(ids) == len(res.items)
    rows = real.app.state.runs.agent.status(acc.run_id)["values"]["resources"]
    for x in res.items:
        assert x.candidate_ids == list(dict.fromkeys(r["candidate_id"] for r in rows if r["resource_id"] == x.resource_id))
        assert x.candidate_id == x.candidate_ids[0]
    body = {"reviewer": "w", "resource_judgements": [{"resource_id": "R0739", "judgement": "적합"}]}
    err(real.post(f"/runs/{acc.run_id}/review", json=body), 400, "RESOURCE_NOT_IN_RUN")
    body = {"reviewer": "w", "checked_evidence_ids": ["EV-없음"]}
    err(real.post(f"/runs/{acc.run_id}/review", json=body), 400, "EVIDENCE_NOT_IN_RUN")
    assert [d["error_code"] for d in audit(real, acc.run_id, "review_rejected")] == [
        "RESOURCE_NOT_IN_RUN", "EVIDENCE_NOT_IN_RUN"]


def review(c, run_id, rid="STUB-R01", judgement="부적합", **kw):
    body = {"reviewer": "worker-01", "resource_judgements": [{"resource_id": rid, "judgement": judgement}],
            "memo": "메모 원문은 보고서에 안 들어감", "follow_up_on": "2025-10-01", **kw}
    return ok(c.post(f"/runs/{run_id}/review", json=body), ReviewResponse)


def test_review_regenerates_report_through_graph(real):
    acc = start(real)
    st = status(real, acc.run_id)
    r = review(real, acc.run_id, report_revision=st.report_revision)
    assert r.changed and not r.report_stale and r.report_revision > st.report_revision
    st2 = status(real, acc.run_id)
    assert st2.report_revision == st2.published_report_revision == r.report_revision
    assert st2.path[-4:] == ["await_approval", "record_review", "write_report", "guard_report"]
    rep = report(real, acc.run_id).report
    assert r.review_id in rep.review_ids and not rep.needs_regeneration
    text = "\n".join(rep.fact_sentences)
    assert "담당자 검토: 자원 STUB-R01" in text and "부적합" in text and "메모 원문" not in text
    assert all("STUB-R01" not in (a.action_plan.text if a.action_plan else "") for a in rep.actions)
    done = audit(real, acc.run_id, "report_regenerated")[-1]
    assert done["published_revision"] == r.report_revision and r.review_id in done["review_ids"]
    # 화면에 띄운 판(옛 판)으로는 승인 안 됨, 지금 판으로만
    err(approve(real, acc.run_id, st.report_revision, "old"), 409, "REPORT_REVISION_STALE")
    ok(approve(real, acc.run_id, r.report_revision, "new"), ApproveResponse)
    hist = ok(real.get(f"/runs/{acc.run_id}/history"), RunHistoryResponse)
    assert [x.reason for x in hist.report_revisions] == ["initial", "review_changed"]
    # 같은 판정 다시 → 그대로
    assert status(real, acc.run_id).status == "approved"


def test_review_saved_during_regeneration_is_merged(real):
    """다시 만드는 사이 들어온 검토도 발행 보고서에 반영될 때까지 다시 만든다."""
    svc = real.app.state.runs
    acc = start(real)
    orig = svc.agent.review
    calls = []

    def racing(run_id, payload, rev, key):
        calls.append(list(payload["review_ids"]))
        if len(calls) == 1:  # 첫 재생성 도중 다른 검토가 저장됨
            from api.store import new_id

            now = svc.clock()
            with svc.store.tx():
                svc.store.insert_review(new_id("rev", now), run_id, "worker-02", [], None, None, 2, 2,
                                        [{"resource_id": "STUB-R02", "judgement": "적합"}], now)
        return orig(run_id, payload, rev, key)

    svc.agent.review = racing
    r = review(real, acc.run_id)
    assert len(calls) == 2 and len(calls[1]) == 2
    rep = report(real, acc.run_id).report
    assert set(calls[1]) <= set(rep.review_ids)
    text = "\n".join(rep.fact_sentences)
    assert "STUB-R01" in text and "STUB-R02" in text
    st = status(real, acc.run_id)
    assert not st.report_stale and st.report_revision == st.published_report_revision > r.report_revision - 1


# ---------------------------------------------------------------- 승인 (지금 판만, 멱등)


def test_approval_idempotency_same_as_agent(real):
    acc = start(real)
    rev = status(real, acc.run_id).report_revision
    first = ok(approve(real, acc.run_id, rev, "k1"), ApproveResponse)
    again = ok(approve(real, acc.run_id, rev, "k1"), ApproveResponse)
    assert again.duplicate and again.approval_id == first.approval_id
    err(approve(real, acc.run_id, rev, "k1", "held"), 409, "IDEMPOTENCY_KEY_REUSED")
    assert "지금: 이미 승인됨" in err(approve(real, acc.run_id, rev, "k2", "held"), 409, "RUN_NOT_AWAITING_APPROVAL")["message"]
    agent = real.app.state.runs.agent
    assert agent.store.count(acc.run_id, "finalize") == 1
    # 에이전트도 같은 뜻: 반대 결정·다른 키 → 정의된 충돌 (예전에는 duplicate 로 돌려줌)
    assert agent.approve(acc.run_id, "held", rev, idempotency_key="k1")["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert agent.approve(acc.run_id, "held", rev, idempotency_key="k9")["code"] == "RUN_NOT_AWAITING_APPROVAL"
    assert agent.approve(acc.run_id, "approved", rev + 1, idempotency_key="k9")["status"] == "conflict"
    assert agent.approve(acc.run_id, "approved", rev, idempotency_key="k1")["status"] == "duplicate"
    rejected = audit(real, acc.run_id, "approval_rejected")
    assert [d["error_code"] for d in rejected] == ["IDEMPOTENCY_KEY_REUSED", "RUN_NOT_AWAITING_APPROVAL"]
    assert len(real.app.state.store.list_approvals(acc.run_id)) == 1
    assert status(real, acc.run_id).status == "approved"


def test_rejected_approvals_leave_audit_only(real):
    acc = start(real)
    rev = status(real, acc.run_id).report_revision
    err(approve(real, acc.run_id, rev + 5, "s1"), 409, "REPORT_REVISION_STALE")
    review(real, acc.run_id)  # 판이 올라감
    err(approve(real, acc.run_id, rev, "s2"), 409, "REPORT_REVISION_STALE")
    codes = [d["error_code"] for d in audit(real, acc.run_id, "approval_rejected")]
    assert codes == ["REPORT_REVISION_STALE", "REPORT_REVISION_STALE"]
    assert real.app.state.store.list_approvals(acc.run_id) == []
    assert real.app.state.runs.agent.store.count(acc.run_id, "finalize") == 0
    assert status(real, acc.run_id).status == "awaiting_approval"


def test_contract_abstain_blocks_approve_and_more(real):
    acc = start(real, analysis_mode="strict_as_of")
    st = status(real, acc.run_id)
    assert st.status == "awaiting_approval" and st.abstain_reason and st.contract_check.status == "ERROR"
    err(approve(real, acc.run_id, st.report_revision, "a"), 409, "APPROVAL_BLOCKED_BY_ABSTAIN")
    err(approve(real, acc.run_id, st.report_revision, "m", "more"), 409, "APPROVAL_BLOCKED_BY_ABSTAIN")
    ok(approve(real, acc.run_id, st.report_revision, "h", "held"), ApproveResponse)
    assert status(real, acc.run_id).status == "held"
    assert "collect_signals" not in status(real, acc.run_id).path


# ---------------------------------------------------------------- 추가 조사 (2회 제한)


def test_more_with_focus_runs_plan_and_shows_changes(real):
    acc = start(real)
    st = status(real, acc.run_id)
    cid = st.candidates[0].candidate_id
    err(approve(real, acc.run_id, st.report_revision, "bad", "more", focus={"candidate_ids": ["C-없음"]}), 400,
        "CANDIDATE_NOT_IN_RUN")
    err(approve(real, acc.run_id, st.report_revision, "bad2", "approved", focus={"tools": ["calc_heat_response"]}),
        400, "INVALID_REQUEST")
    err(approve(real, acc.run_id, st.report_revision, "bad3", "more", focus={"tools": ["run_sql"]}), 400,
        "INVALID_REQUEST")
    focus = {"candidate_ids": [cid], "counter_checks": ["강수"], "memo": "연휴 뒤 회복이랑 체류 인구도 봐줘"}
    ok(approve(real, acc.run_id, st.report_revision, "m1", "more", focus=focus), ApproveResponse)
    st2 = status(real, acc.run_id)
    assert st2.status == "awaiting_approval" and st2.extra_rounds == 1 and st2.evidence_revision == 2
    assert "plan_more" in st2.path
    plan_step = [t for t in st2.decision_trace if t.node == "plan_more"][-1]
    assert plan_step.source == "llm" and "calc_holiday_gap" in plan_step.choice
    # 등급은 그대로 (LLM·사람 계획이 등급을 바꾸지 않음)
    assert [c.candidate_level for c in st2.candidates] == [c.candidate_level for c in st.candidates]
    rep = report(real, acc.run_id).report
    assert rep.round_changes and rep.round_changes[0].startswith("1회차 대비 달라진 점")
    assert any("새 근거" in s and "없음" not in s for s in rep.round_changes)
    assert any(s.startswith(f"집중 확인 {cid}") for s in rep.round_changes)
    assert "연휴 뒤 회복" not in json.dumps(rep.model_dump(mode="json"), ensure_ascii=False)  # 메모 원문 없음
    hist = ok(real.get(f"/runs/{acc.run_id}/history"), RunHistoryResponse)
    assert [x.reason for x in hist.report_revisions] == ["initial", "extra_round"]
    ok(approve(real, acc.run_id, st2.report_revision, "m2", "more"), ApproveResponse)
    st3 = status(real, acc.run_id)
    assert st3.extra_rounds == 2
    err(approve(real, acc.run_id, st3.report_revision, "m3", "more"), 409, "EXTRA_ROUNDS_EXCEEDED")
    ok(approve(real, acc.run_id, st3.report_revision, "h", "held"), ApproveResponse)
    assert status(real, acc.run_id).status == "held"


# ---------------------------------------------------------------- 질의응답·이어 묻기


def test_qa_endpoint_runs_graph(real):
    r = ok(real.post("/qa", json={"question": "강남 1인세대 비율 상위 동은?", "as_of": "2025-10-20"}), QAResponse)
    assert r.status == "answered" and r.evidence_card and r.answer
    assert {g.check for g in r.guard} >= {"신호", "사실", "표현"}
    assert status(real, r.run_id).status == "answered"
    d4 = ok(real.post("/qa", json={"question": "강남 60대의 밤 활동은 어때?", "as_of": "2025-10-20"}), QAResponse)
    assert d4.status == "abstained" and d4.abstain_reason.startswith("GRAIN_FORBIDDEN_COMBO") and d4.needed_data
    who = ok(real.post("/qa", json={"question": "강남 김철수 할머니 고독사 위험 확인해야 해?", "as_of": "2025-10-20"}),
             QAResponse)
    assert who.status == "abstained" and who.abstain_reason.startswith("OUT_OF_SCOPE_INDIVIDUAL")


def test_followup_reads_card_only(real):
    acc = start(real)
    agent = real.app.state.runs.agent
    before = agent.status(acc.run_id)["values"]
    card_ids = {e.evidence_id for e in ok(real.get(f"/runs/{acc.run_id}/evidence"), EvidenceResponse).evidence_card.evidence}
    f = ok(real.post(f"/runs/{acc.run_id}/followup", json={"question": "남면은 왜 이 등급이야?"}), FollowupResponse)
    assert f.status == "answered" and f.source == "llm" and f.cited_evidence_ids
    assert set(f.cited_evidence_ids) <= card_ids and ("남면" in f.answer or "32010350" in f.answer)
    assert all(g.verdict == "PASS" for g in f.guard)
    after = agent.status(acc.run_id)["values"]
    assert after["path"] == before["path"] and after["tool_timings"] == before["tool_timings"]  # 도구 안 부름
    who = ok(real.post(f"/runs/{acc.run_id}/followup", json={"question": "남면 김철수 할머니 집 주소 알려줘"}),
             FollowupResponse)
    assert who.status == "abstained" and who.abstain_reason.startswith("OUT_OF_SCOPE_INDIVIDUAL")
    other = ok(real.post(f"/runs/{acc.run_id}/followup", json={"question": "강남은 어때?"}), FollowupResponse)
    assert other.status == "abstained" and other.abstain_reason.startswith("OUT_OF_SCOPE")
    assert [d["followup_id"] for d in audit(real, acc.run_id, "followup_answered")] == [f.followup_id]


def test_followup_guard_drops_bad_llm_sentence(tmp_path):
    app = make(tmp_path, llm="fake_diagnosis_once")
    with TestClient(app) as c:
        acc = start(c)
        f = ok(c.post(f"/runs/{acc.run_id}/followup", json={"question": "남면은 왜 이 등급이야?"}), FollowupResponse)
        assert f.status == "answered" and "고독사" not in f.answer
        assert f.source == "template" and any(g.verdict != "PASS" for g in f.guard)
    close(app)


# ---------------------------------------------------------------- 스냅샷 고정·재시작


def test_new_snapshot_does_not_change_pending_run(real):
    acc = start(real)
    before = ok(real.get(f"/runs/{acc.run_id}/evidence"), EvidenceResponse)
    snaps = real.app.state.snapshots
    old = snaps.active()
    snaps.publish(old.model_copy(update={"snapshot_id": "snap-stub-0002", "previous_snapshot_id": old.snapshot_id}))
    assert ok(real.get("/snapshots/active"), SnapshotInfo).snapshot_id == "snap-stub-0002"
    st = status(real, acc.run_id)
    assert st.snapshot_id == old.snapshot_id
    ok(approve(real, acc.run_id, st.report_revision, "m", "more"), ApproveResponse)  # 재수집도 고정 스냅샷으로
    after = ok(real.get(f"/runs/{acc.run_id}/evidence"), EvidenceResponse)
    assert {e.snapshot_id for e in after.evidence_card.evidence} == {old.snapshot_id}
    assert before.snapshot_id == after.snapshot_id
    new = start(real)
    assert new.snapshot_id == "snap-stub-0002"
    assert {e.snapshot_id for e in ok(real.get(f"/runs/{new.run_id}/evidence"),
                                      EvidenceResponse).evidence_card.evidence} == {"snap-stub-0002"}


def test_restart_recovers_runs_and_pending_work(tmp_path):
    app1 = make(tmp_path, "r.sqlite")
    svc = app1.state.runs
    with TestClient(app1) as c1:
        waiting = start(c1)
        rev = status(c1, waiting.run_id).report_revision
        svc._submit = lambda *a: None  # 이 뒤 그래프 작업은 "도중에 꺼짐"
        queued = start(c1)
        more = start(c1)
        stale = start(c1)
    # queued 는 아직 실행 전, more/stale 은 실행해 둔 뒤 작업만 끊김
    svc._submit = lambda fn, *a: fn(*a)
    svc._execute(more.run_id)
    svc._execute(stale.run_id)
    svc._submit = lambda *a: None
    with TestClient(app1) as c1:
        r_more = status(c1, more.run_id).report_revision
        ok(approve(c1, more.run_id, r_more, "mk", "more"), ApproveResponse)
        assert status(c1, more.run_id).status == "running"
        rv = review(c1, stale.run_id)
        assert rv.report_stale
        assert status(c1, queued.run_id).status == "queued"
    close(app1)

    app2 = make(tmp_path, "r.sqlite")  # 다시 켬: recover() 가 이어 감
    with TestClient(app2) as c2:
        st = status(c2, waiting.run_id)
        assert st.status == "awaiting_approval" and st.snapshot_id == waiting.snapshot_id
        assert status(c2, queued.run_id).status == "awaiting_approval"
        m = status(c2, more.run_id)
        assert m.status == "awaiting_approval" and m.extra_rounds == 1 and m.report_revision > r_more
        s = status(c2, stale.run_id)
        assert not s.report_stale and s.report_revision == s.published_report_revision
        assert rv.review_id in report(c2, stale.run_id).report.review_ids
        ok(approve(c2, waiting.run_id, rev, "after-restart"), ApproveResponse)
        assert status(c2, waiting.run_id).status == "approved"
    close(app2)


def test_background_run_then_poll(tmp_path):
    app = make(tmp_path, delay_s=1)
    with TestClient(app) as c:
        acc = start(c)
        assert acc.status == "queued"
        app.state.runs.wait_idle()
        st = status(c, acc.run_id)
        assert st.status == "awaiting_approval" and st.decision_trace
        ok(approve(c, acc.run_id, st.report_revision, "m", "more"), ApproveResponse)
        app.state.runs.wait_idle()
        assert status(c, acc.run_id).extra_rounds == 1
    close(app)


def test_llm_calls_logged_without_text(real):
    acc = start(real)
    rows = real.app.state.store._all("SELECT * FROM llm_calls WHERE run_id = ?", acc.run_id)
    assert rows and {r["node"] for r in rows} >= {"classify", "explain"}
    assert all(Q not in json.dumps(r, ensure_ascii=False) for r in rows)
    # 이어 묻기 행이 끼어도 그래프 기록(plan_more 등)을 빠뜨리지 않음
    ok(real.post(f"/runs/{acc.run_id}/followup", json={"question": "남면은 왜 이 등급이야?"}), FollowupResponse)
    rev = status(real, acc.run_id).report_revision
    ok(approve(real, acc.run_id, rev, "m", "more", focus={"memo": "연휴 뒤 회복도 봐줘"}), ApproveResponse)
    nodes = [r["node"] for r in real.app.state.store._all("SELECT node FROM llm_calls WHERE run_id = ?", acc.run_id)]
    graph_slots = [r["slot"] for r in real.app.state.runs.agent.status(acc.run_id)["values"]["llm_log"]]
    assert [n for n in nodes if n != "followup"] == graph_slots and "plan_more" in nodes and "followup" in nodes


def test_followup_on_abstain_report_abstains(real):
    acc = start(real, analysis_mode="strict_as_of")
    f = ok(real.post(f"/runs/{acc.run_id}/followup", json={"question": "왜 보류야?"}), FollowupResponse)
    assert f.status == "abstained" and f.abstain_reason.startswith("판단 보류된 보고서") and f.needed_data


# ---------------------------------------------------------------- 실제 도구 (dev 스냅샷)


# ---------------------------------------------------------------- 현황·지도·후보 목록·자원 (실제 모드)


def monitoring_runs(c):
    return [r for r in c.app.state.store.list_runs(None, 200) if r["mode"] == "monitoring"]


def test_dashboard_reads_published_monitoring_run(real):
    """현황·후보 목록·지도가 같은 정기 감시 실행을 읽고(없으면 한 번 돌려 발행), 후보 ID 를 /runs 가 그대로 받음."""
    q = {"as_of": AS_OF, "city": "춘천"}
    cl = ok(real.get("/candidates", params=q), CandidateListResponse)
    rid = cl.monitoring_run_id
    assert rid and cl.items and cl.selection_budget == 2
    assert [r["run_id"] for r in monitoring_runs(real)] == [rid]
    mon = status(real, rid)
    assert mon.status == "published"
    assert sorted(c.candidate_id for c in mon.candidates) == sorted(c.candidate_id for c in cl.items)
    levels = {c.candidate_id: c.candidate_level for c in mon.candidates}
    assert all(levels[c.candidate_id] == c.candidate_level for c in cl.items)  # 등급은 그래프 그대로
    ov = ok(real.get("/overview", params=q), OverviewResponse)
    mp = ok(real.get("/map", params=q), MapResponse)
    assert ov.monitoring_run_id == mp.monitoring_run_id == rid and ov.rule_version == "rules-v1.1"
    assert [t.rule for t in ov.triggers] == ["R1"]
    for kind, rng in ov.candidate_counts.items():
        assert rng.low <= rng.count <= rng.high
        assert rng.count == sum(c.candidate_type == kind and c.counter_result != "기각" for c in cl.items)
    assert ov.compared_as_of.isoformat() == "2025-09-19"
    assert set(ov.new_candidate_ids) <= {c.candidate_id for c in cl.items}
    # 같은 기준일·도시는 다시 돌리지 않음 (7일 전 비교용 실행 하나만 더)
    assert len(monitoring_runs(real)) == 2
    assert audit(real, rid, "run_created")[0]["origin"] == "dashboard"
    # 지도: 그 도시 행정동 모두, 행동 신호는 그 실행의 후보에서
    sched = {c.adm_cd for c in cl.items if c.candidate_type == "scheduled_access_review"}
    assert {a.adm_cd for a in mp.areas if a.behavior_signal == "예정된 점검"} == sched
    assert all(a.adm_cd.startswith("32010") for a in mp.areas) and mp.resources
    assert any(r.phone_based and r.lat is None for r in mp.resources)
    # 목록의 후보를 열어 조사 (같은 ID, 같은 도시)
    for c in cl.items[:2]:
        acc = start(real, question=None, candidate_id=c.candidate_id)
        st = status(real, acc.run_id)
        assert st.status == "awaiting_approval" and {x.city for x in st.candidates} == {"춘천"}
    # 거르기
    lv = ok(real.get("/candidates", params={**q, "level": "Level 3"}), CandidateListResponse)
    assert lv.items and all(c.candidate_level == "Level 3" for c in lv.items)
    err(real.get("/candidates", params={**q, "level": "높음"}), 400, "INVALID_REQUEST")


def test_dashboard_city_id_ranges_do_not_collide(real):
    """두 도시 정기 감시를 따로 돌려도 같은 기준일 후보 ID 가 겹치지 않음 (춘천 001~, 강남 501~)."""
    cc = ok(real.get("/candidates", params={"as_of": AS_OF, "city": "춘천"}), CandidateListResponse)
    gn = ok(real.get("/candidates", params={"as_of": AS_OF, "city": "강남"}), CandidateListResponse)
    assert not {c.candidate_id for c in cc.items} & {c.candidate_id for c in gn.items}
    assert all(int(c.candidate_id[-3:]) > 500 for c in gn.items)
    acc = start(real, question=None, candidate_id=gn.items[0].candidate_id)
    assert {x.city for x in status(real, acc.run_id).candidates} == {"강남"}
    # 도시를 함께 보내면 그 도시 후보만 찾음
    err(real.post("/runs", json={"mode": "qa", "candidate_id": gn.items[0].candidate_id, "as_of": AS_OF,
                                 "city": "춘천"}), 400, "INVALID_REQUEST")


def test_overview_released_with_reason(real):
    """7일 전 목록에만 있던 후보는 해제 + 사유 (가짜 도구: 10/03 에는 추석 D-7 트리거가 끝남)."""
    ov = ok(real.get("/overview", params={"as_of": "2025-10-03", "city": "춘천"}), OverviewResponse)
    assert ov.released_candidates and ov.released_candidate_ids == [r.candidate_id for r in ov.released_candidates]
    assert all(r.last_seen_as_of.isoformat() == AS_OF and r.release_reason.startswith("트리거 기간이 끝남")
               for r in ov.released_candidates)
    first = ok(real.get("/overview", params={"as_of": "2025-07-03", "city": "춘천"}), OverviewResponse)
    assert first.compared_as_of is None and not first.released_candidates  # 7일 전이 자료 기간 밖


def test_dashboard_waits_for_running_monitoring(real):
    """같은 열쇠의 정기 감시가 아직 도는 중이면 409 RUN_NOT_READY (두 번 돌리지 않음)."""
    from api.auth import DEV_IDENTITY

    svc = real.app.state.runs
    snap = real.app.state.snapshots.active()
    inputs = {"mode": "monitoring", "question": None, "city": "춘천", "as_of": AS_OF,
              "analysis_mode": "simulated_replay", "snapshot_id": snap.snapshot_id, "llm": "none",
              "tools_backend": "stub", "selection_budget": 2, "candidate_id": None, "source_run_id": None}
    svc._insert(inputs, snap, DEV_IDENTITY, svc.clock(), status="running")
    err(real.get("/candidates", params={"as_of": AS_OF, "city": "춘천"}), 409, "RUN_NOT_READY")
    assert len(monitoring_runs(real)) == 1


def test_resources_from_ledger_v2(real):
    """자원 조회: 원장 v2 + find_resources 와 같은 제외 규칙 (출처·확인일 없음, 관할 밖 물리 거점 빼기)."""
    res = ok(real.get("/resources", params={"adm_cd": "32010390", "group": "고령층"}), ResourceListResponse)
    assert res.ledger_version and res.ledger_version.startswith("ledger@sha256:")
    assert res.total >= len(res.items) > 0
    assert all(x.source_url and x.verified_date and x.city == "춘천" for x in res.items)
    assert not any(w.startswith("권역 외") and not x.phone_based for x in res.items for w in x.warnings)
    assert any(x.distance_km is not None for x in res.items)  # 동을 주면 거리
    city = ok(real.get("/resources", params={"city": "강남", "need_type": "난방", "limit": 3}), ResourceListResponse)
    assert 0 < len(city.items) <= 3 <= city.total and all(x.city == "강남" for x in city.items)
    crisis = ok(real.get("/resources", params={"city": "춘천", "need_type": "정신건강 상담", "limit": 200}),
                ResourceListResponse)
    tail = [any(w.startswith("묶음: 위급 시 안내") for w in x.warnings) for x in crisis.items]
    assert tail == sorted(tail)  # 위급 시 안내는 맨 아래
    err(real.get("/resources", params={"need_type": "없는 유형"}), 400, "INVALID_REQUEST")
    # G: 후보의 원인 유형으로 좁힘 (필요 유형에 맞는 total ≤ 관할·대상 scope_total)
    q = {"adm_cd": "32010350", "group": "고령층", "limit": 5}
    narrow = ok(real.get("/resources", params={**q, "cause_type": "계절 공동화형"}), ResourceListResponse)
    wide = ok(real.get("/resources", params=q), ResourceListResponse)
    assert narrow.need_types == ["안부·관계", "이동 지원", "생필품"] and narrow.cause_type == "계절 공동화형"
    assert narrow.scope_total == wide.total > narrow.total >= len(narrow.items) > 0
    err(real.get("/resources", params={**q, "cause_type": "없는형"}), 400, "INVALID_REQUEST")


def test_followups_aggregate_without_n_plus_one(real):
    """GET /followups: 후보마다 가장 최근 조사 실행의 마지막 후속 확인일 (도시·기간·상태)."""
    cl = ok(real.get("/candidates", params={"as_of": AS_OF, "city": "춘천"}), CandidateListResponse)
    cid = cl.items[0].candidate_id
    acc = start(real, question=None, candidate_id=cid)
    rid = ok(real.get(f"/runs/{acc.run_id}/resources"), RunResourcesResponse).items[0].resource_id
    review(real, acc.run_id, rid=rid, judgement="적합", follow_up_on="2025-10-01")
    review(real, acc.run_id, rid=rid, judgement="적합", follow_up_on="2025-10-05")
    f = ok(real.get("/followups", params={"as_of": AS_OF}), FollowUpListResponse)
    assert [(x.candidate_id, x.run_id, x.city, x.follow_up_on.isoformat(), x.state) for x in f.items] == [
        (cid, acc.run_id, "춘천", "2025-10-05", "나중")]
    assert f.items[0].memo and not f.items[0].approved and f.items[0].run_status == "awaiting_approval"
    assert ok(real.get("/followups", params={"city": "강남"}), FollowUpListResponse).items == []
    assert ok(real.get("/followups", params={"week": "2025-10-01"}), FollowUpListResponse).items
    assert ok(real.get("/followups", params={"from": "2025-10-06"}), FollowUpListResponse).items == []
    err(real.get("/followups", params={"week": "2025-10-01", "to": "2025-10-03"}), 400, "INVALID_REQUEST")
    # 같은 후보를 다시 열면 새 실행이 기준 (아직 후속 확인일 없음 → 빠짐)
    start(real, question=None, candidate_id=cid)
    assert ok(real.get("/followups"), FollowUpListResponse).items == []


@pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")
def test_real_tools_dashboard_d2(tmp_path):
    """실제 도구(dev 스냅샷): D2 춘천 12/20 현황·지도·후보 목록 → 목록 후보로 조사 시작. 구조 순위는 도시 전체 모집단."""
    app = make(tmp_path, tools="real", llm="fake")
    with TestClient(app) as c:
        q = {"as_of": "2025-12-20", "city": "춘천"}
        cl = ok(c.get("/candidates", params=q), CandidateListResponse)
        assert len(cl.items) == 3 and {x.candidate_level for x in cl.items} == {"Level 2"}
        ov = ok(c.get("/overview", params=q), OverviewResponse)
        assert "W" in {t.rule for t in ov.triggers} and ov.monitoring_run_id == cl.monitoring_run_id
        mp = ok(c.get("/map", params=q), MapResponse)
        ranked = [a for a in mp.areas if a.structure_rank is not None]
        assert len(ranked) >= 20 and sorted(a.structure_rank for a in ranked) == list(range(1, len(ranked) + 1))
        best = min(ranked, key=lambda a: a.structure_rank)
        assert best.structure_value == max(a.structure_value for a in ranked)
        assert any(a.structure_top for a in ranked) and any(a.nearest_hub_km for a in mp.areas)
        top = cl.items[0]
        assert all(x.cause_type and x.need_types for x in cl.items)  # G: 자원 미리 보기를 좁힐 원인 유형
        acc = start(c, question=None, candidate_id=top.candidate_id, as_of="2025-12-20")
        st = status(c, acc.run_id)
        assert st.status == "awaiting_approval" and {x.city for x in st.candidates} == {"춘천"}
        res = ok(c.get("/resources", params={"adm_cd": top.adm_cd or "32010390", "group": "고령층"}),
                 ResourceListResponse)
        assert res.items and res.total >= len(res.items)
    close(app)


@pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")
def test_real_tools_d2_through_api(tmp_path):
    """시연 D2 (춘천 12/20 읍·면 동절기): 실제 도구 + 가짜 LLM 으로 API 전체 흐름."""
    app = make(tmp_path, tools="real", llm="fake")
    with TestClient(app) as c:
        snap = ok(c.get("/snapshots/active"), SnapshotInfo)
        assert snap.snapshot_id.startswith("dev-")
        acc = start(c, question="춘천 읍·면 어르신 겨울철 점검은 어디부터 해야 해?", as_of="2025-12-20")
        st = status(c, acc.run_id)
        assert st.status == "awaiting_approval" and acc.snapshot_id == snap.snapshot_id
        assert len(st.candidates) == 4 and {x.candidate_level for x in st.candidates} == {"Level 2", "Level 3"}
        res = ok(c.get(f"/runs/{acc.run_id}/resources"), RunResourcesResponse)
        assert res.items and {x.city for x in res.items} == {"춘천"}
        f = ok(c.post(f"/runs/{acc.run_id}/followup", json={"question": "남면은 왜 Level 2야?"}),
               FollowupResponse)
        assert f.status == "answered"
        perf = c.app.state.runs.agent.status(acc.run_id)["values"]["run_meta"]["perf"]
        assert perf["tools"] and perf["tool_ms"] > 0  # 도구별 ms
        target = res.items[0]
        r = review(c, acc.run_id, rid=target.resource_id, judgement="적합")
        assert not r.report_stale
        ok(approve(c, acc.run_id, r.report_revision, "d2-ok"), ApproveResponse)
        assert status(c, acc.run_id).status == "approved"
    close(app)


def test_concurrent_more_runs_one_round(real):
    """같은 '추가 조사'를 동시에 여러 번 보내도 회차는 1번만 (같은 키는 duplicate, 다른 키는 409)."""
    import threading

    acc = start(real)
    rev = status(real, acc.run_id).report_revision
    out: list = []

    def go(key):
        out.append(approve(real, acc.run_id, rev, key, decision="more"))

    ts = [threading.Thread(target=go, args=(k,)) for k in ("m-same", "m-same", "m-same", "m-other")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    real.app.state.runs.wait_idle()
    fresh = [r for r in out if r.status_code == 200 and not r.json()["duplicate"]]
    assert len(fresh) == 1  # 나머지는 같은 키면 duplicate, 아니면 409 (먼저 온 쪽이 이김)
    assert all(r.status_code == 409 or r.json()["duplicate"] for r in out if r not in fresh)
    st = status(real, acc.run_id)
    assert st.extra_rounds == 1
    assert real.app.state.runs.agent.status(acc.run_id)["values"]["extra_rounds"] == 1
