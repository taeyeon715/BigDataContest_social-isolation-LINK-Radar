"""권한·저장 전 가리기·실행 예산·횟수 한도·보존 기한 (시스템 점검 SA-03·SA-04·SA-08, AG-06).

- 조회 역할은 실행·질의응답·이어 묻기를 만들 수 없음 (LLM 비용)
- 공용 토큰(화면 번들에 들어가는 토큰): 기본 조회, prod 에서 검토·승인 불가, 기록 이름에 "공용:" 표시
- 질문·검토 메모·자원 판정 메모·승인 메모는 가린 글만 저장 (DB·체크포인트·감사·로그 어디에도 원문 없음)
- 실행당 LLM 호출 수·비용 한도 (configs/llm.yaml budget) → 넘으면 규칙·템플릿, llm_calls 에 BUDGET_EXCEEDED
- 사용자별 실행 만들기 횟수 한도 → 429 RATE_LIMITED + Retry-After
- 보존 기한이 지난 질문·메모 글 비우기
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app  # noqa: E402
from api.auth import Auth, Identity  # noqa: E402
from api.limits import RateLimiter, parse_rate  # noqa: E402
from api.logs import LOGGER, JsonFormatter  # noqa: E402
from api.retention import purge_old_text  # noqa: E402
from api.services.fake_data import KST  # noqa: E402
from bigcontest.agent import budget as B  # noqa: E402
from bigcontest.agent.llm import FakeLLM, call_llm  # noqa: E402

TOKENS = {
    "tok-view": Identity("viewer-01", "조회", True),
    "tok-review": Identity("worker-01", "검토", True),
    "tok-review2": Identity("worker-02", "검토", True),
    "tok-approve": Identity("lead-01", "승인", True),
}
H = {k[4:]: {"Authorization": f"Bearer {k}"} for k in TOKENS}
RUN = {"mode": "qa", "candidate_id": "C-20250926-001", "as_of": "2025-09-26"}
RAW = ("김철수 할머니", "010-1234-5678", "테헤란로 152", "a.b@x.com", "900101-1234567")
PII_Q = "강남 1인세대 비율 상위 동은? 김철수 할머니 010-1234-5678"
PII_MEMO = "김철수 할머니 010-1234-5678 테헤란로 152 a.b@x.com 900101-1234567 방문 필요"


def _no_raw(text: str) -> bool:
    return not any(x in text for x in RAW)


def _dump_db(path: Path) -> str:
    """SQLite 파일의 모든 표 모든 행을 글로 (원문이 어디에도 없는지 보려고)."""
    con = sqlite3.connect(path)
    try:
        out = []
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            for row in con.execute(f"SELECT * FROM {t}"):
                out.append(" ".join(x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x) for x in row))
        return "\n".join(out)
    finally:
        con.close()


@pytest.fixture
def secured(tmp_path):
    app = create_app(db_path=tmp_path / "s.sqlite", delay_s=0, auth=Auth(TOKENS))
    with TestClient(app) as c:
        yield c
    app.state.store.close()


@pytest.fixture
def dev(tmp_path):
    app = create_app(db_path=tmp_path / "d.sqlite", delay_s=0, auth=Auth())
    with TestClient(app) as c:
        c.db = tmp_path / "d.sqlite"  # type: ignore[attr-defined]
        yield c
    app.state.store.close()


# ---------------------------------------------------------------- 권한 (SA-03·SA-04)


def test_viewer_cannot_create_runs_or_spend_llm(secured):
    for path, body in [("/runs", RUN), ("/qa", {"question": "강남 1인세대 비율 상위 동은?", "as_of": "2025-10-20"})]:
        r = secured.post(path, json=body, headers=H["view"])
        assert r.status_code == 403 and r.json()["error_code"] == "FORBIDDEN", path
    run_id = secured.post("/runs", json=RUN, headers=H["review"]).json()["run_id"]
    assert secured.post(f"/runs/{run_id}/followup", json={"question": "왜?"}, headers=H["view"]).status_code == 403
    assert secured.get(f"/runs/{run_id}", headers=H["view"]).status_code == 200  # 읽기는 그대로


def test_shared_token_defaults_to_view_and_is_labeled(monkeypatch, tmp_path):
    for k in ("LINKRADAR_API_TOKEN", "LINKRADAR_API_USER", "LINKRADAR_API_ROLE", "LINKRADAR_API_TOKENS_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LINKRADAR_API_TOKEN", "bundle-token")
    auth = Auth.from_env()
    who = auth.identify("Bearer bundle-token")
    assert who.role == "조회" and who.shared and who.actor() == "공용:demo"
    # prod: 공용 토큰에 검토·승인 역할을 주면 시작하지 않음 (승인 기록이 공용 사용자 이름으로 남지 않게)
    monkeypatch.setenv("LINKRADAR_API_ROLE", "승인")
    with pytest.raises(RuntimeError, match="공용 토큰"):
        create_app(db_path=tmp_path / "p.sqlite", delay_s=0, auth=Auth.from_env(), env="prod")
    # dev 에서는 쓸 수 있지만 기록 이름이 "공용:demo" (한 사람 이름처럼 보이지 않게)
    app = create_app(db_path=tmp_path / "q.sqlite", delay_s=0, auth=Auth.from_env(), env="dev")
    h = {"Authorization": "Bearer bundle-token"}
    with TestClient(app) as c:
        run_id = c.post("/runs", json=RUN, headers=h).json()["run_id"]
        rev = c.get(f"/runs/{run_id}", headers=h).json()["report_revision"]
        a = c.post(f"/runs/{run_id}/approve", headers=h, json={"decision": "approved", "report_revision": rev,
                                                                "approver": "김담당", "idempotency_key": "k"})
        assert a.status_code == 200, a.text
        assert c.get(f"/runs/{run_id}/history", headers=h).json()["approvals"][0]["approver"] == "공용:demo"
        actors = {x["actor"] for x in app.state.store.audit_rows(run_id) if x["action"] != "snapshot_pinned"}
        assert actors == {"공용:demo"}
    app.state.store.close()


# ---------------------------------------------------------------- 저장 전 가리기 (SA-08)


def test_question_and_memos_masked_at_rest(dev):
    grab = []

    class _Grab(logging.Handler):
        def emit(self, record):
            grab.append(JsonFormatter().format(record))

    h = _Grab()
    logging.getLogger(LOGGER).addHandler(h)
    try:
        qa = dev.post("/qa", json={"question": PII_Q, "as_of": "2025-10-20"})
        assert qa.status_code == 200
        run_id = dev.post("/runs", json={**RUN, "question": "춘천 어디부터 확인해야 해? " + PII_MEMO}).json()["run_id"]
        res = dev.get(f"/runs/{run_id}/resources").json()
        rid = (res.get("items") or res.get("resources") or [{}])[0].get("resource_id", "R0739")
        r = dev.post(f"/runs/{run_id}/review", json={
            "reviewer": "아무개", "memo": PII_MEMO, "checked_evidence_ids": [],
            "resource_judgements": [{"resource_id": rid, "judgement": "적합", "memo": PII_MEMO}]})
        assert r.status_code == 200, r.text
        rev = dev.get(f"/runs/{run_id}").json()["report_revision"]
        a = dev.post(f"/runs/{run_id}/approve", json={"decision": "held", "report_revision": rev, "approver": "x",
                                                      "idempotency_key": "h1", "memo": PII_MEMO})
        assert a.status_code == 200, a.text
    finally:
        logging.getLogger(LOGGER).removeHandler(h)
    hist = dev.get(f"/runs/{run_id}/history").json()
    assert "[전화번호]" in hist["reviews"][0]["memo"] and _no_raw(hist["reviews"][0]["memo"])
    assert "[이름]" in (hist["approvals"][0]["memo"] or "")
    listed = dev.get("/runs").json()["items"]
    assert all(_no_raw(x["question"] or "") for x in listed) and any("[이름]" in (x["question"] or "") for x in listed)
    dump = _dump_db(dev.db)
    assert "[전화번호]" in dump and _no_raw(dump)
    assert _no_raw("\n".join(grab))


def test_real_graph_gets_masked_question_only(tmp_path):
    """실제 그래프: 체크포인트 DB 에도 원문이 없음. 이름이 든 질문은 가린 뒤에도 개인 지목으로 보류."""
    app = create_app(db_path=tmp_path / "r.sqlite", delay_s=0, backend="real", tools_backend="stub", llm="fake",
                     auth=Auth())
    try:
        with TestClient(app) as c:
            q = "강남 김철수 할머니 010-1234-5678 고독사 위험 확인해야 해?"
            r = c.post("/qa", json={"question": q, "as_of": "2025-10-20"}).json()
            assert r["status"] == "abstained" and r["abstain_reason"].startswith("OUT_OF_SCOPE_INDIVIDUAL")
            ok = c.post("/qa", json={"question": "강남 1인세대 비율 상위 동은? 010-1234-5678", "as_of": "2025-10-20"})
            assert ok.status_code == 200
            app.state.runs.wait_idle()
    finally:
        app.state.runs.close()
        app.state.store.close()
    for f in (tmp_path / "r.sqlite", tmp_path / "r.agent.sqlite"):
        dump = _dump_db(f)
        assert _no_raw(dump), f.name
    raw_bytes = (tmp_path / "r.agent.sqlite").read_bytes()
    assert "010-1234-5678".encode() not in raw_bytes and "김철수".encode() not in raw_bytes


def test_retention_clears_old_text(dev):
    run_id = dev.post("/runs", json={**RUN, "question": "춘천 어디부터 확인해야 해? 메모"}).json()["run_id"]
    assert dev.post(f"/runs/{run_id}/review", json={"reviewer": "a", "memo": "현장 메모", "resource_judgements": []}
                    ).status_code == 200
    store = dev.app.state.store
    now = datetime.now(KST)
    assert purge_old_text(store, days=30, now=now) == {k: 0 for k in (
        "runs", "reviews", "approvals", "approval_intents", "objections", "objection_resolutions",
        "resource_judgements", "run_metadata")}
    dry = purge_old_text(store, days=30, now=now + timedelta(days=31), dry_run=True)
    assert dry["runs"] == 1 and dry["reviews"] == 1
    assert store.get_run(run_id)["question"]  # dry-run 은 그대로
    out = purge_old_text(store, days=30, now=now + timedelta(days=31))
    assert out["runs"] == 1 and out["reviews"] == 1
    assert store.get_run(run_id)["question"] is None
    assert dev.get(f"/runs/{run_id}/history").json()["reviews"][0]["memo"] is None
    assert dev.get(f"/runs/{run_id}").status_code == 200  # 실행·검토 행은 남음
    rows = store._all("SELECT * FROM audit_log WHERE action = 'text_purged'")
    assert len(rows) == 1 and json.loads(rows[0]["detail"])["runs"] == 1


# ---------------------------------------------------------------- 실행 예산 (AG-06)


def test_budget_blocks_llm_calls_over_limit():
    llm = FakeLLM("fake")
    with B.run_budget(max_calls=1, max_cost_usd=None) as b:
        out1, rec1 = call_llm(llm, "classify", llm.classify, "강남 1인세대 비율 상위 동은?", None)
        out2, rec2 = call_llm(llm, "classify", llm.classify, "강남 1인세대 비율 상위 동은?", None)
    assert rec1["ok"] and out1 is not None
    assert out2 is None and rec2["error_code"] == B.BUDGET_EXCEEDED and rec2["fallback"] and b.calls == 1
    with B.run_budget(used_cost=0.5, max_calls=None, max_cost_usd=0.5):
        assert call_llm(llm, "classify", llm.classify, "q", None)[1]["error_code"] == B.BUDGET_EXCEEDED
    assert call_llm(llm, "classify", llm.classify, "q", None)[1]["ok"]  # 예산 밖(그래프 직접)은 막지 않음
    lim = B.limits()
    assert lim[0] and lim[1]  # configs/llm.yaml 에 한도가 있음


def test_budget_enforced_in_api_run(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "limits", lambda: (2, None))
    app = create_app(db_path=tmp_path / "b.sqlite", delay_s=0, backend="real", tools_backend="stub", llm="fake",
                     auth=Auth())
    try:
        with TestClient(app) as c:
            r = c.post("/runs", json={"mode": "qa", "question": "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?",
                                      "as_of": "2025-09-26"})
            assert r.status_code == 202, r.text
            run_id = r.json()["run_id"]
            app.state.runs.wait_idle()
            assert c.get(f"/runs/{run_id}").json()["status"] in ("awaiting_approval", "abstained")
            rows = app.state.store._all("SELECT * FROM llm_calls WHERE run_id = ?", run_id)
            used = [x for x in rows if x["error_code"] != B.BUDGET_EXCEEDED and x["node"] != "init"]
            over = [x for x in rows if x["error_code"] == B.BUDGET_EXCEEDED]
            assert len(used) == 2 and over and all(x["status"] == "fallback" for x in over)
            # 이어 묻기도 같은 실행 예산: 이미 넘었으므로 LLM 없이 템플릿
            f = c.post(f"/runs/{run_id}/followup", json={"question": "왜 이 등급이야?"})
            assert f.status_code == 200
            assert f.json()["source"] != "llm"
    finally:
        app.state.runs.close()
        app.state.store.close()


# ---------------------------------------------------------------- 횟수 한도 (SA-04)


def test_rate_limit_per_user(tmp_path):
    app = create_app(db_path=tmp_path / "l.sqlite", delay_s=0, auth=Auth(TOKENS), run_rate="2/60")
    with TestClient(app) as c:
        body = {"question": "강남 1인세대 비율 상위 동은?", "as_of": "2025-10-20"}
        assert c.post("/qa", json=body, headers=H["review"]).status_code == 200
        assert c.post("/runs", json=RUN, headers=H["review"]).status_code == 202
        r = c.post("/qa", json=body, headers=H["review"])
        assert r.status_code == 429 and r.json()["error_code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) >= 1
        assert c.post("/qa", json=body, headers=H["review2"]).status_code == 200  # 다른 사용자는 따로 셈
        assert c.get("/runs", headers=H["review"]).status_code == 200  # 읽기는 한도 없음
    app.state.store.close()


def test_rate_limiter_window_and_parse():
    t = [0.0]
    lim = RateLimiter(2, 10, clock=lambda: t[0])
    lim.hit("a")
    lim.hit("a")
    with pytest.raises(Exception) as e:
        lim.hit("a")
    assert e.value.status == 429  # type: ignore[attr-defined]
    t[0] = 10.5
    lim.hit("a")  # 창이 지나면 다시 받음
    assert parse_rate("0") is None and parse_rate("") is None and parse_rate("20/600") == (20, 600.0)


def test_rate_limit_default_on_in_prod_only(tmp_path):
    app = create_app(db_path=tmp_path / "x.sqlite", delay_s=0, auth=Auth(TOKENS), env="prod")
    assert app.state.run_limiter is not None and app.state.run_limiter.count == 20
    app.state.store.close()
    app = create_app(db_path=tmp_path / "y.sqlite", delay_s=0, auth=Auth(), env="dev", run_rate=None)
    assert app.state.run_limiter is None
    app.state.store.close()
