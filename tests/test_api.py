"""가짜 API 테스트: 모든 엔드포인트 응답 모양, 202 흐름, 오래된 판·중복 승인, 재시작, 스냅샷 고정."""

from __future__ import annotations

import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app  # noqa: E402
from api.models import ReportResponse, RunHistoryResponse, RunListResponse  # noqa: E402
from bigcontest.agent.schemas import (  # noqa: E402
    DISCLAIMER,
    ApproveResponse,
    CandidateListResponse,
    CapabilitiesResponse,
    ErrorResponse,
    EvidenceResponse,
    HealthResponse,
    MapResponse,
    OverviewResponse,
    QAResponse,
    ResourceListResponse,
    ReviewResponse,
    RunAccepted,
    RunStatusResponse,
    SnapshotInfo,
)

KST_START = datetime(2026, 10, 13, 10, 0, 0)


class Clock:
    def __init__(self) -> None:
        self.now = KST_START

    def __call__(self) -> datetime:
        return self.now

    def tick(self, s: float) -> None:
        self.now += timedelta(seconds=s)


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "state.sqlite", delay_s=0)
    with TestClient(app) as c:
        yield c
    app.state.store.close()


@pytest.fixture
def slow(tmp_path):
    """끝나는 데 10초 걸리는 실행. 시계를 직접 움직인다."""
    clock = Clock()
    app = create_app(db_path=tmp_path / "slow.sqlite", delay_s=10, clock=clock)
    with TestClient(app) as c:
        c.clock = clock  # type: ignore[attr-defined]
        yield c
    app.state.store.close()


def ok(resp, model, status=200):
    assert resp.status_code == status, resp.text
    assert resp.headers["X-Data-Source"] in ("fake", "config")
    return model.model_validate(resp.json())


def err(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    ErrorResponse.model_validate(body)
    assert set(body) == {"error_code", "message", "details"}
    assert body["error_code"] == code
    assert "Traceback" not in body["message"]
    return body


def start_run(c, **kw):
    body = {"mode": "qa", "candidate_id": "C-20250926-001", "as_of": "2025-09-26", **kw}
    return ok(c.post("/runs", json=body), RunAccepted, 202)


# ---------------------------------------------------------------- 조회 엔드포인트


def test_health_snapshot_capabilities(client):
    h = ok(client.get("/health"), HealthResponse)
    assert h.status == "ok" and h.active_snapshot_id.startswith("snap-fake-")
    s = ok(client.get("/snapshots/active"), SnapshotInfo)
    assert s.snapshot_id == h.active_snapshot_id
    r = client.get("/data/capabilities")
    cap = ok(r, CapabilitiesResponse)
    assert r.headers["X-Data-Source"] == "config"
    assert {x.source for x in cap.sources} >= {"card2"}


def test_fake_marker_on_every_response(client):
    for path in ["/health", "/overview?as_of=2025-09-26&city=춘천", "/runs", "/nope"]:
        assert client.get(path).headers["X-Data-Source"] == "fake"


@pytest.mark.parametrize("city", ["강남", "춘천"])
@pytest.mark.parametrize("as_of", ["2025-07-01", "2025-08-01", "2025-09-26", "2025-10-13", "2025-11-14", "2025-12-31"])
def test_overview_map_candidates_shapes(client, city, as_of):
    ov = ok(client.get("/overview", params={"as_of": as_of, "city": city}), OverviewResponse)
    assert ov.disclaimer == DISCLAIMER
    assert ov.time_label  # simulated_replay 기본 → 가정한 공개 시점 표시
    for rng in ov.candidate_counts.values():
        assert rng.low is not None and rng.low <= rng.count <= rng.high
    mp = ok(client.get("/map", params={"as_of": as_of, "city": city}), MapResponse)
    assert len(mp.areas) == (22 if city == "강남" else 25)
    assert "위험지도 아님" in mp.note
    cl = ok(client.get("/candidates", params={"as_of": as_of, "city": city}), CandidateListResponse)
    for c in cl.items:
        assert c.city == city
        if c.selected_for_review:
            assert c.queue_rank <= cl.selection_budget


def test_overview_retrospective_has_no_time_label(client):
    ov = ok(client.get("/overview", params={"as_of": "2025-09-26", "city": "춘천", "analysis_mode": "retrospective"}),
            OverviewResponse)
    assert ov.time_label is None


def test_candidates_cover_types_levels_and_states(client):
    seen_levels, seen_counter, partial = set(), set(), False
    for as_of in ["2025-09-26", "2025-10-13", "2025-11-14", "2025-12-20"]:
        for city in ["강남", "춘천"]:
            cl = ok(client.get("/candidates", params={"as_of": as_of, "city": city}), CandidateListResponse)
            for c in cl.items:
                seen_levels.add(c.candidate_level)
                seen_counter.add(c.counter_result)
                partial |= c.input_status == "부분 판정"
                if c.counter_result == "기각":
                    assert c.candidate_level == "Level 1"
    assert {"Level 1", "Level 2", "Level 3", "판단 보류"} <= seen_levels
    assert {"기각", "보류", "통과", "해당 없음"} <= seen_counter
    assert partial


def test_candidate_filters(client):
    p = {"as_of": "2025-09-26", "city": "춘천"}
    cl = ok(client.get("/candidates", params={**p, "type": "unexpected_activity_deviation"}), CandidateListResponse)
    assert cl.items and all(c.candidate_type == "unexpected_activity_deviation" for c in cl.items)
    cl = ok(client.get("/candidates", params={**p, "level": "판단 보류"}), CandidateListResponse)
    assert [c.candidate_id for c in cl.items] == ["C-20250926-004"]
    err(client.get("/candidates", params={**p, "type": "risk"}), 400, "INVALID_REQUEST")


def test_resources(client):
    r = ok(client.get("/resources", params={"adm_cd": "32010390", "group": "고령층"}), ResourceListResponse)
    assert r.items[0].phone_based  # 전화형 먼저
    assert any(x.eligibility == "확인 필요" for x in r.items)
    assert any(any(w.startswith("위치 확인 필요") for w in x.warnings) for x in r.items)
    g = ok(client.get("/resources", params={"adm_cd": "11230520", "group": "중장년 남성 1인가구"}), ResourceListResponse)
    assert any(any(w.startswith("권역 외") for w in x.warnings) for x in g.items)
    assert all(x.source_url and x.verified_date for x in g.items)  # G-07
    err(client.get("/resources", params={"group": "노인"}), 400, "INVALID_REQUEST")
    # G: 원인 유형으로 좁힘 (가짜도 같은 칸)
    n = ok(client.get("/resources", params={"adm_cd": "32010390", "group": "고령층", "cause_type": "1인가구 밀집형"}),
           ResourceListResponse)
    assert n.need_types == ["안부·관계", "정신건강 상담"] and n.scope_total == len(r.items) >= n.total == len(n.items)
    err(client.get("/resources", params={"cause_type": "없는형"}), 400, "INVALID_REQUEST")


def test_invalid_query_is_400_with_plain_message(client):
    b = err(client.get("/overview", params={"as_of": "2025-09-26", "city": "서울"}), 400, "INVALID_REQUEST")
    assert b["message"] == "도시는 강남, 춘천만 고를 수 있습니다."
    err(client.get("/overview", params={"as_of": "2026-01-05", "city": "춘천"}), 400, "INVALID_REQUEST")
    err(client.get("/overview", params={"as_of": "어제", "city": "춘천"}), 400, "INVALID_REQUEST")
    err(client.get("/overview", params={"city": "춘천"}), 400, "INVALID_REQUEST")
    err(client.post("/runs", json={"mode": "qa", "as_of": "2025-09-26"}), 400, "INVALID_REQUEST")
    err(client.post("/runs", json={"mode": "qa", "candidate_id": "C-없음", "as_of": "2025-09-26"}), 400,
        "INVALID_REQUEST")
    err(client.get("/runs/run-없음"), 404, "RUN_NOT_FOUND")
    err(client.get("/runs/run-없음/evidence"), 404, "RUN_NOT_FOUND")


def test_no_active_snapshot(client):
    client.app.state.snapshots.unpublish_all()
    assert ok(client.get("/health"), HealthResponse).status == "degraded"
    err(client.get("/snapshots/active"), 503, "NO_ACTIVE_SNAPSHOT")
    err(client.post("/runs", json={"mode": "monitoring", "as_of": "2025-09-26"}), 503, "NO_ACTIVE_SNAPSHOT")


def test_cors_only_web_origin(client):
    ok_origin = client.options("/runs", headers={"Origin": "http://localhost:5173",
                                                 "Access-Control-Request-Method": "POST"})
    assert ok_origin.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


# ---------------------------------------------------------------- 실행 202 흐름


def test_run_202_then_poll(slow):
    acc = start_run(slow)
    assert acc.status == "queued" and acc.status_url == f"/runs/{acc.run_id}"
    st = ok(slow.get(acc.status_url), RunStatusResponse)
    assert st.status == "queued" and st.path == []
    err(slow.get(f"/runs/{acc.run_id}/evidence"), 409, "RUN_NOT_READY")
    slow.clock.tick(4)
    assert ok(slow.get(acc.status_url), RunStatusResponse).status == "running"
    slow.clock.tick(7)
    st = ok(slow.get(acc.status_url), RunStatusResponse)
    assert st.status == "awaiting_approval" and st.path[-1] == "await_approval"
    assert st.report_revision == 1 and not st.report_stale
    assert st.candidates[0].candidate_id == "C-20250926-001"
    assert st.snapshot_id == acc.snapshot_id


def test_evidence_report_history_list(client):
    acc = start_run(client)
    ev = ok(client.get(f"/runs/{acc.run_id}/evidence"), EvidenceResponse)
    card = ev.evidence_card
    assert card.candidate_id == "C-20250926-001" and card.disclaimer == DISCLAIMER
    assert {f.evidence_id for f in card.facts} <= {e.evidence_id for e in card.evidence}
    assert card.time_label  # simulated_replay
    rep = ok(client.get(f"/runs/{acc.run_id}/report"), ReportResponse)
    assert rep.report.report_revision == 1 and rep.report.fact_sentences
    hist = ok(client.get(f"/runs/{acc.run_id}/history"), RunHistoryResponse)
    assert [r.reason for r in hist.report_revisions] == ["initial"]
    runs = ok(client.get("/runs", params={"candidate_id": "C-20250926-001"}), RunListResponse)
    assert runs.items[0].run_id == acc.run_id and runs.items[0].rule_version == "rules-v0.1"
    assert runs.items[0].ledger_version and runs.items[0].cleaning_rule_version == "clean-v1"


def test_spec_example_requests_accepted(client):
    acc = ok(client.post("/runs", json={"mode": "qa", "question": "이번 추석 전에 춘천에서 어디부터 확인해야 해?",
                                        "candidate_id": None, "as_of": "2025-09-26",
                                        "analysis_mode": "simulated_replay"}), RunAccepted, 202)
    st = ok(client.get(f"/runs/{acc.run_id}"), RunStatusResponse)
    assert st.status == "awaiting_approval" and st.route == "investigate"
    assert st.candidates and st.candidates[0].city == "춘천"


def test_monitoring_run_publishes_without_approval(client):
    acc = ok(client.post("/runs", json={"mode": "monitoring", "as_of": "2025-11-14", "city": "춘천"}), RunAccepted, 202)
    st = ok(client.get(f"/runs/{acc.run_id}"), RunStatusResponse)
    assert st.status == "published" and st.path[-1] == "publish_candidates"
    assert st.candidates and all(c.city == "춘천" for c in st.candidates)
    a = client.post(f"/runs/{acc.run_id}/approve", json={"decision": "approved", "report_revision": 1,
                                                         "approver": "lead-01", "idempotency_key": "m1"})
    err(a, 409, "RUN_NOT_AWAITING_APPROVAL")


# ---------------------------------------------------------------- 검토·승인


def review(c, run_id, judgement="적합"):
    body = {"reviewer": "worker-01", "checked_evidence_ids": [],
            "resource_judgements": [{"resource_id": "R0739", "judgement": judgement, "memo": "전화형"}],
            "memo": "이장님께 먼저 연락", "follow_up_on": "2025-10-01"}
    return ok(c.post(f"/runs/{run_id}/review", json=body), ReviewResponse)


def approve(c, run_id, rev, key, decision="approved"):
    return c.post(f"/runs/{run_id}/approve", json={"decision": decision, "report_revision": rev,
                                                   "approver": "lead-01", "idempotency_key": key})


def test_review_makes_report_stale_and_locks_approval(slow):
    acc = start_run(slow)
    slow.clock.tick(11)
    r = review(slow, acc.run_id)
    assert r.report_stale and r.report_revision == 2
    st = ok(slow.get(f"/runs/{acc.run_id}"), RunStatusResponse)
    assert st.report_stale and st.report_revision == 2
    # 다시 만드는 중: 새 판 번호로도, 옛 판 번호로도 승인 거절
    err(approve(slow, acc.run_id, 2, "k-a"), 409, "REPORT_REVISION_STALE")
    err(approve(slow, acc.run_id, 1, "k-b"), 409, "REPORT_REVISION_STALE")
    slow.clock.tick(11)
    st = ok(slow.get(f"/runs/{acc.run_id}"), RunStatusResponse)
    assert not st.report_stale and st.report_revision == 2
    rep = ok(slow.get(f"/runs/{acc.run_id}/report"), ReportResponse)
    assert rep.report.report_revision == 2 and "외로움안녕120 적합" in rep.report.narrative
    # 오래된 판으로 승인 → 거절, 지금 판 → 승인
    b = err(approve(slow, acc.run_id, 1, "k-c"), 409, "REPORT_REVISION_STALE")
    assert "2판" in b["message"]
    a = ok(approve(slow, acc.run_id, 2, "k-d"), ApproveResponse)
    assert a.decision == "approved" and not a.duplicate
    hist = ok(slow.get(f"/runs/{acc.run_id}/history"), RunHistoryResponse)
    assert [x.reason for x in hist.report_revisions] == ["initial", "review_changed"]
    assert hist.reviews[0].follow_up_on == date(2025, 10, 1)
    assert hist.reviews[0].report_revision_before == 1 and hist.reviews[0].report_revision_after == 2


def test_same_judgement_does_not_bump_revision(client):
    acc = start_run(client)
    assert review(client, acc.run_id).report_revision == 2
    again = review(client, acc.run_id)  # 같은 판정 → 보고서 그대로
    assert again.report_revision == 2 and not again.report_stale


def test_duplicate_approval_one_record(client):
    acc = start_run(client)
    first = ok(approve(client, acc.run_id, 1, "run:approve:1"), ApproveResponse)
    second = ok(approve(client, acc.run_id, 1, "run:approve:1"), ApproveResponse)
    assert second.duplicate and second.approval_id == first.approval_id
    hist = ok(client.get(f"/runs/{acc.run_id}/history"), RunHistoryResponse)
    assert len(hist.approvals) == 1 and hist.finalized == ["finalize"]
    assert ok(client.get(f"/runs/{acc.run_id}"), RunStatusResponse).status == "approved"
    # 같은 키로 다른 결정 → 거절, 다른 키로 다시 승인 → 승인 대기 아님
    err(approve(client, acc.run_id, 1, "run:approve:1", "held"), 409, "IDEMPOTENCY_KEY_REUSED")
    again = approve(client, acc.run_id, 1, "other-key")
    err(again, 409, "RUN_NOT_AWAITING_APPROVAL")
    assert "지금: 이미 승인됨" in again.json()["message"]  # 지금 상태를 말해 줌


def test_concurrent_approval_one_record(client):
    acc = start_run(client)
    results = []

    def go():
        results.append(approve(client, acc.run_id, 1, "same-key").status_code)

    ts = [threading.Thread(target=go) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert results == [200] * 6
    hist = ok(client.get(f"/runs/{acc.run_id}/history"), RunHistoryResponse)
    assert len(hist.approvals) == 1 and hist.finalized == ["finalize"]


def test_hold_and_extra_rounds_limit(client):
    acc = start_run(client)
    ok(approve(client, acc.run_id, 1, "m-1", "more"), ApproveResponse)
    st = ok(client.get(f"/runs/{acc.run_id}"), RunStatusResponse)
    assert st.status == "awaiting_approval" and st.report_revision == 2 and st.evidence_revision == 2
    ok(approve(client, acc.run_id, 2, "m-2", "more"), ApproveResponse)
    err(approve(client, acc.run_id, 3, "m-3", "more"), 409, "EXTRA_ROUNDS_EXCEEDED")
    ok(approve(client, acc.run_id, 3, "h-1", "held"), ApproveResponse)
    assert ok(client.get(f"/runs/{acc.run_id}"), RunStatusResponse).status == "held"
    hist = ok(client.get(f"/runs/{acc.run_id}/history"), RunHistoryResponse)
    assert [x.reason for x in hist.report_revisions] == ["initial", "extra_round", "extra_round"]


def test_review_only_while_awaiting(client):
    acc = start_run(client)
    ok(approve(client, acc.run_id, 1, "x"), ApproveResponse)
    r = client.post(f"/runs/{acc.run_id}/review", json={"reviewer": "w", "resource_judgements": []})
    err(r, 409, "RUN_NOT_AWAITING_APPROVAL")


# ---------------------------------------------------------------- 재시작·스냅샷 고정


def test_restart_keeps_awaiting_run(tmp_path):
    db = tmp_path / "restart.sqlite"
    clock = Clock()
    app1 = create_app(db_path=db, delay_s=10, clock=clock)
    with TestClient(app1) as c1:
        acc = start_run(c1)
        clock.tick(11)
        assert ok(c1.get(f"/runs/{acc.run_id}"), RunStatusResponse).status == "awaiting_approval"
        review(c1, acc.run_id)  # 다시 만드는 중에 종료
    app1.state.store.close()

    app2 = create_app(db_path=db, delay_s=10, clock=clock)
    with TestClient(app2) as c2:
        st = ok(c2.get(f"/runs/{acc.run_id}"), RunStatusResponse)
        assert st.status == "awaiting_approval" and st.report_stale
        clock.tick(11)  # 예약도 이어짐
        st = ok(c2.get(f"/runs/{acc.run_id}"), RunStatusResponse)
        assert not st.report_stale
        ok(approve(c2, acc.run_id, st.report_revision, "after-restart"), ApproveResponse)
        assert ok(c2.get(f"/runs/{acc.run_id}"), RunStatusResponse).status == "approved"
    app2.state.store.close()


def test_new_snapshot_does_not_change_pending_run(client):
    acc = start_run(client, candidate_id="C-20250926-004")
    before = ok(client.get(f"/runs/{acc.run_id}/evidence"), EvidenceResponse)
    st_before = ok(client.get(f"/runs/{acc.run_id}"), RunStatusResponse)
    old = client.app.state.snapshots.active()
    client.app.state.snapshots.publish(old.model_copy(update={
        "snapshot_id": "snap-fake-20261014-090000-ffff", "previous_snapshot_id": old.snapshot_id}))
    assert ok(client.get("/snapshots/active"), SnapshotInfo).snapshot_id == "snap-fake-20261014-090000-ffff"
    after = ok(client.get(f"/runs/{acc.run_id}/evidence"), EvidenceResponse)
    st_after = ok(client.get(f"/runs/{acc.run_id}"), RunStatusResponse)
    assert after.snapshot_id == old.snapshot_id == st_after.snapshot_id
    assert after.evidence_card == before.evidence_card
    assert st_after.candidates == st_before.candidates
    assert st_after.candidates[0].candidate_level == "판단 보류"
    # 새 실행은 새 스냅샷
    assert start_run(client).snapshot_id == "snap-fake-20261014-090000-ffff"


# ---------------------------------------------------------------- 질의응답


def test_qa_abstain_d4(client):
    r = ok(client.post("/qa", json={"question": "강남 60대의 밤 활동은 어때?", "as_of": "2025-10-20",
                                    "analysis_mode": "simulated_replay", "city": "강남"}), QAResponse)
    assert r.status == "abstained" and r.answer is None and r.evidence_card is None
    assert r.abstain_reason.startswith("GRAIN_FORBIDDEN_COMBO")
    assert r.needed_data
    st = ok(client.get(f"/runs/{r.run_id}"), RunStatusResponse)
    assert st.status == "abstained" and st.contract_check.status == "ERROR"
    err(approve(client, r.run_id, 1, "q"), 409, "APPROVAL_BLOCKED_BY_ABSTAIN")


def test_qa_answer_after_guard(client):
    r = ok(client.post("/qa", json={"question": "강남 1인세대 비율 상위 동은?", "as_of": "2025-10-20"}), QAResponse)
    assert r.status == "answered" and r.evidence_card is not None
    assert {g.check for g in r.guard} == {"신호", "사실", "표현"}
    assert all(g.verdict == "PASS" for g in r.guard if g.check == "사실")
    for f in r.evidence_card.facts:
        assert f"[{f.evidence_id}]" in r.answer
    assert "역삼1동" in r.answer
    ev = ok(client.get(f"/runs/{r.run_id}/evidence"), EvidenceResponse)
    assert ev.evidence_card == r.evidence_card


@pytest.mark.parametrize("q,code", [
    ("춘천 중장년 외로움은?", "CITY_NOT_COVERED"),
    ("논현1동 동별 카드 결제는?", "DIMENSION_NOT_AVAILABLE"),
    ("누가 고립 위험이야?", "OUT_OF_SCOPE"),
    ("오늘 날씨 어때", "OUT_OF_SCOPE"),
])
def test_qa_other_abstains(client, q, code):
    r = ok(client.post("/qa", json={"question": q, "as_of": "2025-10-20"}), QAResponse)
    assert r.status == "abstained" and r.abstain_reason.startswith(code) and r.needed_data


def test_fake_overview_release_reason_and_followups(client):
    """추가 칸 (가짜에도): 해제된 후보 + 사유, 후속 확인 모아 보기 GET /followups."""
    ov = ok(client.get("/overview", params={"as_of": "2025-10-03", "city": "춘천"}), OverviewResponse)
    assert ov.released_candidate_ids == [r.candidate_id for r in ov.released_candidates] != []
    assert all("기간이 끝남" in r.release_reason and r.last_seen_as_of == date(2025, 9, 26)
               for r in ov.released_candidates)
    acc = start_run(client)
    review(client, acc.run_id)
    r = client.get("/followups", params={"as_of": "2025-09-26", "city": "춘천"})
    assert r.status_code == 200 and r.headers["X-Data-Source"] == "fake"
    items = r.json()["items"]
    assert [(x["candidate_id"], x["run_id"], x["follow_up_on"], x["state"]) for x in items] == [
        ("C-20250926-001", acc.run_id, "2025-10-01", "이번 주")]
    assert client.get("/followups", params={"city": "강남"}).json()["items"] == []
