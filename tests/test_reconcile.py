"""회귀 시험: 장애 뒤 재시작해도 승인·추가 조사가 같은 결과로 끝나는지.

승인: 그래프 확정과 API 기록은 서로 다른 DB 라 한 트랜잭션이 아니다. 그래프 재개 전/후, API 승인 INSERT 전/후,
      _sync 전/후에 실패를 넣고 다시 켜면 같은 결정으로 끝나고, 승인·확정은 각각 1건, 반대 결정·다른 판으로 바뀌지 않는다.
추가 조사: 추가 조사 범위(후보·도구·메모로 정한 계획)를 받은 직후·재개 키를 차지한 직후·도구 실행 중에 멈춰도
      다시 켜면 같은 계획이 한 번만 돈다. 메모 원문은 API·그래프 DB 어디에도 남지 않는다.

가짜 도구(stub) + 가짜 LLM. 유료 호출 없음.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app  # noqa: E402
from api.auth import Auth  # noqa: E402

Q = "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?"
AS_OF = "2025-09-26"
MEMO = "병원 결제도 확인"


def make(tmp_path, name="state.sqlite", llm="fake"):
    return create_app(db_path=tmp_path / name, backend="real", tools_backend="stub", llm=llm, delay_s=0,
                      auth=Auth({}))


def close(app):
    app.state.runs.close()
    app.state.store.close()


def start(c) -> tuple[str, dict]:
    r = c.post("/runs", json={"mode": "qa", "question": Q, "as_of": AS_OF})
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    return rid, c.get(f"/runs/{rid}").json()


def approve(c, rid, rev, key, decision="approved", **kw):
    return c.post(f"/runs/{rid}/approve", json={"decision": decision, "report_revision": rev, "approver": "lead-01",
                                                "idempotency_key": key, **kw})


def converged(app, c, rid, decision, rev, key):
    """API 상태·승인·확정 이력·그래프 확정 기록이 모두 같은 결정 하나로 맞는지."""
    store, agent = app.state.store, app.state.runs.agent
    assert c.get(f"/runs/{rid}").json()["status"] == decision
    apr = store.list_approvals(rid)
    assert [(a["decision"], a["report_revision"], a["approver"], a["idempotency_key"]) for a in apr] == [
        (decision, rev, "lead-01", key)]
    fin = store.list_finalizations(rid)
    assert len(fin) == 1
    import json

    body = json.loads(fin[0]["result"])
    assert body["decision"] == decision and body["approval_id"] == apr[0]["approval_id"]
    assert body["report_revision"] == rev
    assert agent.store.count(rid, "finalize") == 1
    rec = agent.store.get(rid, "finalize")
    assert rec["payload"]["decision"] == decision and rec["payload"]["report_revision"] == rev
    assert len([a for a in store.audit_rows(rid) if a["action"] == "approval_received"]) == 1
    hist = c.get(f"/runs/{rid}/history").json()
    assert [a["approval_id"] for a in hist["approvals"]] == [apr[0]["approval_id"]]
    assert hist["finalized"] == ["finalize"]
    assert not store.pending_intents(rid)
    return apr[0]["approval_id"]


class Crash(RuntimeError):
    pass


def _fail_after(fn):
    """원래 함수를 끝까지 실행한 뒤 실패 (예: _sync 는 됐는데 응답 전에 꺼짐)."""

    def wrapped(*a, **kw):
        fn(*a, **kw)
        raise Crash("주입: 실행 뒤 실패")

    return wrapped


# 실패 주입 지점: (이름, app → patch 컨텍스트)
POINTS = {
    "before_intent": lambda app: patch.object(app.state.store, "put_intent", side_effect=Crash("주입")),
    "before_graph_resume": lambda app: patch.object(app.state.runs.agent, "approve", side_effect=Crash("주입")),
    "after_graph_resume": lambda app: patch.object(app.state.store, "insert_approval", side_effect=Crash("주입")),
    "after_approval_insert": lambda app: patch.object(app.state.store, "finalize", side_effect=Crash("주입")),
    "before_sync": lambda app: patch.object(app.state.runs, "_sync", side_effect=Crash("주입")),
    "after_sync": lambda app: patch.object(app.state.runs, "_sync", _fail_after(app.state.runs._sync)),
}


@pytest.mark.parametrize("point", list(POINTS))
@pytest.mark.parametrize("decision", ["approved", "held"])
def test_approval_failure_points_converge_after_restart(tmp_path, point, decision):
    app = make(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as c:
        rid, st = start(c)
        rev = st["report_revision"]
        with POINTS[point](app):
            r = approve(c, rid, rev, f"{rid}:{decision}", decision)
        assert r.status_code == 500, (point, r.text)
    close(app)

    app = make(tmp_path)  # 다시 켬: recover() 가 맞춤
    with TestClient(app) as c:
        if point == "before_intent":
            # 의도를 적기 전에 실패: 아무 것도 일어나지 않음 → 그대로 승인 대기, 다시 보낼 수 있음
            assert c.get(f"/runs/{rid}").json()["status"] == "awaiting_approval"
            assert not app.state.store.list_approvals(rid) and app.state.runs.agent.store.count(rid) == 0
            assert approve(c, rid, rev, f"{rid}:{decision}", decision).status_code == 200
        aid = converged(app, c, rid, decision, rev, f"{rid}:{decision}")
        # 같은 키로 다시 보내면 첫 기록 그대로, 반대 결정은 409 (뒤집히지 않음)
        again = approve(c, rid, rev, f"{rid}:{decision}", decision)
        assert again.status_code == 200 and again.json()["duplicate"] and again.json()["approval_id"] == aid
        other = "held" if decision == "approved" else "approved"
        flip = approve(c, rid, rev, f"{rid}:{other}", other)
        assert flip.status_code == 409 and flip.json()["error_code"] == "RUN_NOT_AWAITING_APPROVAL"
        converged(app, c, rid, decision, rev, f"{rid}:{decision}")
    close(app)


def test_review_repro_insert_approval_crash_restores_original_record(tmp_path):
    """재현(audit_integrity.py): 그래프 확정 뒤 insert_approval 실패 → 500. 다시 켜면 원래 승인자·키·판으로 복원."""
    app = make(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as c:
        rid, st = start(c)
        req = {"decision": "approved", "report_revision": st["report_revision"], "approver": "lead-01",
               "idempotency_key": rid + ":approve"}
        with patch.object(app.state.store, "insert_approval", side_effect=RuntimeError("crash after graph")):
            assert c.post(f"/runs/{rid}/approve", json=req).status_code == 500
        assert app.state.runs.agent.status(rid)["values"].get("final_record")
        intent = app.state.store.get_intent(rid + ":approve")
        assert intent["state"] == "pending"
    close(app)
    app = make(tmp_path)
    with TestClient(app) as c:
        aid = converged(app, c, rid, "approved", st["report_revision"], rid + ":approve")
        assert aid == intent["approval_id"]  # 미리 정한 승인 ID 그대로
        assert app.state.store.list_approvals(rid)[0]["created_at"] == intent["created_at"]  # 받은 시각 그대로
        rec = [a for a in app.state.store.audit_rows(rid) if a["action"] == "approval_received"][0]
        assert '"recovered": true' in rec["detail"]
    close(app)


def test_retry_without_restart_and_opposite_decision_do_not_flip(tmp_path):
    """다시 켜지 않아도: 다음 승인 요청이 먼저 맞춤 → 같은 키는 duplicate, 다른 키의 반대 결정은 409."""
    app = make(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as c:
        rid, st = start(c)
        rev = st["report_revision"]
        with patch.object(app.state.store, "insert_approval", side_effect=Crash("주입")):
            assert approve(c, rid, rev, "k-approve").status_code == 500
        flip = approve(c, rid, rev, "k-hold", "held")
        assert flip.status_code == 409 and flip.json()["error_code"] == "RUN_NOT_AWAITING_APPROVAL"
        again = approve(c, rid, rev, "k-approve")
        assert again.status_code == 200 and again.json()["duplicate"]
        converged(app, c, rid, "approved", rev, "k-approve")
    close(app)


def test_pending_intent_finishes_before_review_can_bump_revision(tmp_path):
    """그래프 재개 전에 멈춘 승인 의도가 있을 때 검토가 와도 새 판으로 바뀌지 않음 (의도를 먼저 끝냄)."""
    app = make(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as c:
        rid, st = start(c)
        rev = st["report_revision"]
        with patch.object(app.state.runs.agent, "approve", side_effect=Crash("주입")):
            assert approve(c, rid, rev, "k1").status_code == 500
        res = c.get(f"/runs/{rid}/resources").json()["items"]
        r = c.post(f"/runs/{rid}/review", json={"reviewer": "lead-01", "report_revision": rev,
                                                "resource_judgements": [{"resource_id": res[0]["resource_id"],
                                                                         "judgement": "부적합"}]})
        assert r.status_code == 409 and r.json()["error_code"] == "RUN_NOT_AWAITING_APPROVAL"
        converged(app, c, rid, "approved", rev, "k1")
        assert c.get(f"/runs/{rid}").json()["report_revision"] == rev
    close(app)


def test_concurrent_pending_intents_resolve_to_first(tmp_path):
    """두 의도(승인·보류)가 모두 그래프 재개 전에 멈춤 → 먼저 받은 승인으로 끝나고 보류는 거절로 닫힘."""
    app = make(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as c:
        rid, st = start(c)
        rev = st["report_revision"]
        with patch.object(app.state.runs, "_apply_intent", side_effect=Crash("주입")):
            assert approve(c, rid, rev, "first").status_code == 500
            with patch.object(app.state.runs, "_reconcile", lambda run_id: None):
                assert approve(c, rid, rev, "second", "held").status_code == 500
        assert len(app.state.store.pending_intents(rid)) == 2
    close(app)
    app = make(tmp_path)
    with TestClient(app) as c:
        converged(app, c, rid, "approved", rev, "first")
        assert app.state.store.get_intent("second")["state"] == "rejected"
        rej = [a for a in app.state.store.audit_rows(rid) if a["action"] == "approval_rejected"]
        assert len(rej) == 1 and '"held"' in rej[0]["detail"]
    close(app)


# ---------------------------------------------------------------- 추가 조사 범위 보존


def _focus(cid):
    return {"candidate_ids": [cid], "tools": ["calc_holiday_gap"], "memo": MEMO}


def _plan_of(app, rid):
    return app.state.runs.agent.status(rid)["values"].get("extra_plan")


def _control_plan(tmp_path):
    """재시작 없이 같은 요청을 처리한 계획 (대조)."""
    app = make(tmp_path, "control.sqlite")
    with TestClient(app) as c:
        rid, st = start(c)
        cid = st["candidates"][0]["candidate_id"]
        assert approve(c, rid, st["report_revision"], "ctl", "more", focus=_focus(cid)).status_code == 200
        plan = _plan_of(app, rid)
    close(app)
    return cid, plan


def _check_round(app, c, rid, rev, cid, control):
    st = c.get(f"/runs/{rid}").json()
    assert st["status"] == "awaiting_approval" and st["extra_rounds"] == 1 and st["report_revision"] > rev
    v = app.state.runs.agent.status(rid)["values"]
    assert v["extra_rounds"] == 1
    plan = v["extra_plan"]
    assert plan == control  # 같은 계획
    assert "calc_holiday_gap" in plan["tools"] and plan["candidate_ids"] == [cid] and plan["cities"] == ["춘천"]
    assert plan["memo_used"] is True
    assert "calc_holiday_gap" in v["tools_run"] and v["requested_evidence_ids"]
    assert v["path"].count("plan_more") == 1  # 계획은 한 번만
    # 계획 LLM 호출도 한 번만 (받을 때 정한 계획을 재시작 뒤 그대로 씀)
    calls = [r for r in app.state.store._all("SELECT node FROM llm_calls WHERE run_id = ?", rid)
             if r["node"] == "plan_more"]
    assert len(calls) == 1
    approvals = [a for a in app.state.store.list_approvals(rid) if a["decision"] == "more"]
    assert len(approvals) == 1
    assert app.state.store.get_intent(approvals[0]["idempotency_key"])["state"] == "applied"


def _no_memo_text(tmp_path, name):
    for f in tmp_path.glob(name + "*"):
        assert MEMO.encode() not in f.read_bytes(), f.name


def test_more_focus_survives_restart_right_after_acceptance(tmp_path):
    """재현(audit_latest.py): 받은 직후 작업 전에 꺼짐 → 다시 켜면 같은 후보·도구·메모 계획으로 1회차."""
    cid0, control = _control_plan(tmp_path)
    app = make(tmp_path)
    with TestClient(app) as c:
        rid, st = start(c)
        cid = st["candidates"][0]["candidate_id"]
        assert cid == cid0
        app.state.runs._submit = lambda *a: None  # 작업 실행 전에 꺼짐
        r = approve(c, rid, st["report_revision"], "lost-focus", "more", focus=_focus(cid))
        assert r.status_code == 200
        assert c.get(f"/runs/{rid}").json()["status"] == "running"
        it = app.state.store.get_intent("lost-focus")
        assert it["focus"]["plan"]["tools"] and "memo" not in it["focus"]  # 계획은 저장, 메모 원문은 없음
    close(app)
    app = make(tmp_path)
    with TestClient(app) as c:
        _check_round(app, c, rid, st["report_revision"], cid, control)
    close(app)
    _no_memo_text(tmp_path, "state")


def test_more_focus_survives_restart_after_resume_key_claimed(tmp_path):
    """그래프 재개 키를 차지한 직후 꺼짐 → 다시 켜면 같은 범위로 이어 감 (키는 한 번만)."""
    _, control = _control_plan(tmp_path)
    app = make(tmp_path)
    with TestClient(app) as c:
        rid, st = start(c)
        rev = st["report_revision"]
        cid = st["candidates"][0]["candidate_id"]
        app.state.runs._submit = lambda *a: None
        assert approve(c, rid, rev, "claimed", "more", focus=_focus(cid)).status_code == 200
        app.state.runs.agent.store.claim_resume(rid, "claimed", {"decision": "more", "report_revision": rev,
                                                                 "approver": "lead-01", "idempotency_key": "claimed"})
    close(app)
    app = make(tmp_path)
    with TestClient(app) as c:
        _check_round(app, c, rid, rev, cid, control)
    close(app)
    _no_memo_text(tmp_path, "state")


class Killed(BaseException):
    """프로세스가 꺼진 것처럼: 작업 스레드의 except Exception 에도 잡히지 않음."""


def test_more_focus_survives_crash_during_tool_execution(tmp_path):
    """요청한 도구를 실행하는 도중 꺼짐 → 다시 켜면 같은 계획으로 회차를 끝냄 (횟수만 차감되지 않음)."""
    from bigcontest.agent import nodes

    _, control = _control_plan(tmp_path)
    app = make(tmp_path)
    with TestClient(app) as c:
        rid, st = start(c)
        rev = st["report_revision"]
        cid = st["candidates"][0]["candidate_id"]
        svc = app.state.runs
        svc._submit = lambda *a: None
        assert approve(c, rid, rev, "mid-tool", "more", focus=_focus(cid)).status_code == 200
        real_call = nodes.call_tool

        def dying(name, ctx):
            if name == "calc_holiday_gap":
                raise Killed()
            return real_call(name, ctx)

        it = app.state.store.get_intent("mid-tool")
        with patch.object(nodes, "call_tool", dying), pytest.raises(Killed):
            svc._more(rid, rev, "lead-01", "mid-tool", it["focus"])
        assert c.get(f"/runs/{rid}").json()["status"] == "running"
    close(app)
    app = make(tmp_path)
    with TestClient(app) as c:
        _check_round(app, c, rid, rev, cid, control)
    close(app)
    _no_memo_text(tmp_path, "state")


def test_more_without_focus_still_recovers(tmp_path):
    """범위 없는 추가 조사도 받은 직후 꺼졌다 켜면 1회차 (계획 없음)."""
    app = make(tmp_path)
    with TestClient(app) as c:
        rid, st = start(c)
        app.state.runs._submit = lambda *a: None
        assert approve(c, rid, st["report_revision"], "plain", "more").status_code == 200
    close(app)
    app = make(tmp_path)
    with TestClient(app) as c:
        s2 = c.get(f"/runs/{rid}").json()
        assert s2["status"] == "awaiting_approval" and s2["extra_rounds"] == 1
        assert _plan_of(app, rid)["tools"] == [] and _plan_of(app, rid)["source"] == "none"
    close(app)
