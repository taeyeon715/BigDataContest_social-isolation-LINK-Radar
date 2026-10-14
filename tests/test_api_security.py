"""API 신뢰성·보안 보강 테스트 (시스템 점검 R-03·04·05·08·09·10·12·17).

거절 감사 기록, 토큰·역할, 입력 한도, SQLite 설정, ID, 재개 멱등, 경로에 쓰는 ID 검사, 구조화 로그.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app  # noqa: E402
from api.auth import DEV_ACTOR, Auth, Identity  # noqa: E402
from api.logs import LOGGER, JsonFormatter  # noqa: E402

TOKENS = {
    "tok-view": Identity("viewer-01", "조회", True),
    "tok-review": Identity("worker-01", "검토", True),
    "tok-approve": Identity("lead-01", "승인", True),
}
H = {k.split("-")[1]: {"Authorization": f"Bearer {k}"} for k in TOKENS}  # H["view"], H["review"], H["approve"]
RUN = {"mode": "qa", "candidate_id": "C-20250926-001", "as_of": "2025-09-26"}
REVIEW = {"reviewer": "아무개", "resource_judgements": [{"resource_id": "R0739", "judgement": "적합"}]}


def _app(tmp_path, **kw):
    return create_app(db_path=tmp_path / "s.sqlite", delay_s=0, **kw)


@pytest.fixture
def dev(tmp_path):
    app = _app(tmp_path, auth=Auth())
    with TestClient(app) as c:
        yield c
    app.state.store.close()


@pytest.fixture
def secured(tmp_path):
    app = _app(tmp_path, auth=Auth(TOKENS))
    with TestClient(app) as c:
        yield c
    app.state.store.close()


def _approve(c, run_id, rev, key, decision="approved", headers=None, approver="lead-01"):
    return c.post(f"/runs/{run_id}/approve", headers=headers,
                  json={"decision": decision, "report_revision": rev, "approver": approver, "idempotency_key": key})


def _audit(c, run_id):
    return c.app.state.store.audit_rows(run_id)


# ---------------------------------------------------------------- 거절 감사 기록


def test_rejected_approval_is_audited(dev):
    run_id = dev.post("/runs", json=RUN).json()["run_id"]
    r = _approve(dev, run_id, 5, "wrong-rev")
    assert r.status_code == 409 and r.json()["error_code"] == "REPORT_REVISION_STALE"
    rows = [x for x in _audit(dev, run_id) if x["action"] == "approval_rejected"]
    assert len(rows) == 1
    detail = json.loads(rows[0]["detail"])
    assert detail["error_code"] == "REPORT_REVISION_STALE" and detail["report_revision"] == 5
    assert rows[0]["actor"] == DEV_ACTOR and detail["claimed_actor"] == "lead-01"
    # 실행 상태는 그대로 (거절 기록만 커밋됨)
    assert dev.get(f"/runs/{run_id}").json()["status"] == "awaiting_approval"
    # 같은 키로 다른 결정 → 거절도 기록
    assert _approve(dev, run_id, 1, "k1").status_code == 200
    assert _approve(dev, run_id, 1, "k1", "held").status_code == 409
    codes = [json.loads(x["detail"])["error_code"] for x in _audit(dev, run_id) if x["action"] == "approval_rejected"]
    assert codes == ["REPORT_REVISION_STALE", "IDEMPOTENCY_KEY_REUSED"]


@pytest.fixture
def slow(tmp_path):
    """보고서를 다시 만드는 데 10초 걸리는 앱 (시계를 직접 움직임)."""
    from datetime import datetime, timedelta

    class Clock:
        now = datetime(2026, 10, 13, 10, 0, 0)

        def __call__(self):
            return self.now

        def tick(self, s):
            self.now += timedelta(seconds=s)

    clock = Clock()
    app = create_app(db_path=tmp_path / "slow.sqlite", delay_s=10, clock=clock, auth=Auth())
    with TestClient(app) as c:
        c.clock = clock  # type: ignore[attr-defined]
        yield c
    app.state.store.close()


def _rejections(c, run_id, action="approval_rejected"):
    return [json.loads(x["detail"])["error_code"] for x in _audit(c, run_id) if x["action"] == action]


def test_all_approval_rejections_audited_without_records(dev, slow):
    """stale·보류(계약)·추가 조사 초과·상태 오류 거절이 모두 감사에 남고 승인·확정 기록은 생기지 않음."""
    # 판 번호 틀림
    run_id = dev.post("/runs", json=RUN).json()["run_id"]
    assert _approve(dev, run_id, 9, "s-1").status_code == 409
    # 추가 조사 초과
    assert _approve(dev, run_id, 1, "m-1", "more").status_code == 200
    assert _approve(dev, run_id, 2, "m-2", "more").status_code == 200
    assert _approve(dev, run_id, 3, "m-3", "more").status_code == 409
    hist = dev.get(f"/runs/{run_id}/history").json()
    assert len(hist["approvals"]) == 2 and hist["finalized"] == []
    assert _rejections(dev, run_id) == ["REPORT_REVISION_STALE", "EXTRA_ROUNDS_EXCEEDED"]
    # 계약 위반 보류 실행
    qa = dev.post("/qa", json={"question": "강남 60대의 밤 활동은 어때?", "as_of": "2025-10-20"}).json()
    assert _approve(dev, qa["run_id"], 1, "q-1").status_code == 409
    assert _rejections(dev, qa["run_id"]) == ["APPROVAL_BLOCKED_BY_ABSTAIN"]
    assert dev.get(f"/runs/{qa['run_id']}/history").json()["approvals"] == []
    # 상태 오류 (정기 감시는 승인 대기가 아님)
    mon = dev.post("/runs", json={"mode": "monitoring", "as_of": "2025-11-14", "city": "춘천"}).json()["run_id"]
    assert _approve(dev, mon, 1, "mon-1").status_code == 409
    assert _rejections(dev, mon) == ["RUN_NOT_AWAITING_APPROVAL"]
    # 다시 만드는 중 (stale)
    rid = slow.post("/runs", json=RUN).json()["run_id"]
    slow.clock.tick(11)
    assert slow.post(f"/runs/{rid}/review", json=REVIEW).status_code == 200
    assert _approve(slow, rid, 2, "st-1").status_code == 409
    assert _rejections(slow, rid) == ["REPORT_REVISION_STALE"]
    hist = slow.get(f"/runs/{rid}/history").json()
    assert hist["approvals"] == [] and hist["finalized"] == []


def test_review_rejects_ids_not_in_run(dev):
    """이 실행 카드에 없는 근거 ID, 추천 범위에 없는 자원 ID, 다른 실행의 근거 ID 는 거절."""
    run_id = dev.post("/runs", json=RUN).json()["run_id"]
    other = dev.post("/runs", json={**RUN, "candidate_id": "C-20250926-004"}).json()["run_id"]
    mine = [e["evidence_id"] for e in dev.get(f"/runs/{run_id}/evidence").json()["evidence_card"]["evidence"]]
    theirs = [e["evidence_id"] for e in dev.get(f"/runs/{other}/evidence").json()["evidence_card"]["evidence"]]
    foreign = [e for e in theirs if e not in mine]
    assert foreign
    for body, code in [
        ({**REVIEW, "checked_evidence_ids": ["EV-NOT-IN-RUN"]}, "EVIDENCE_NOT_IN_RUN"),
        ({**REVIEW, "checked_evidence_ids": foreign[:1]}, "EVIDENCE_NOT_IN_RUN"),
        ({**REVIEW, "resource_judgements": [{"resource_id": "R9999", "judgement": "적합"}]}, "RESOURCE_NOT_IN_RUN"),
    ]:
        r = dev.post(f"/runs/{run_id}/review", json=body)
        assert r.status_code == 400 and r.json()["error_code"] == code, r.text
    # 다른 도시 자원도 거절
    city = dev.get(f"/runs/{run_id}").json()["candidates"][0]["city"]
    other_city = "춘천" if city == "강남" else "강남"
    here = {x["resource_id"] for x in dev.get("/resources", params={"city": city}).json()["items"]}
    there = sorted({x["resource_id"] for x in dev.get("/resources", params={"city": other_city}).json()["items"]} - here)
    assert there
    r = dev.post(f"/runs/{run_id}/review",
                 json={**REVIEW, "resource_judgements": [{"resource_id": there[0], "judgement": "적합"}]})
    assert r.json()["error_code"] == "RESOURCE_NOT_IN_RUN"
    assert dev.get(f"/runs/{run_id}/history").json()["reviews"] == []
    assert set(_rejections(dev, run_id, "review_rejected")) == {"EVIDENCE_NOT_IN_RUN", "RESOURCE_NOT_IN_RUN"}
    # 실행 카드의 근거, 화면이 보여 주는 자원 목록(후보 동·집단)의 자원은 받음
    cand = dev.get(f"/runs/{run_id}").json()["candidates"][0]
    adm = cand["adm_cd"] or cand["check_first_adm"][0]
    shown = [x["resource_id"] for x in dev.get("/resources", params={"adm_cd": adm}).json()["items"]]
    r = dev.post(f"/runs/{run_id}/review", json={**REVIEW, "checked_evidence_ids": mine, "report_revision": 1,
                                                 "resource_judgements": [{"resource_id": x, "judgement": "보류"}
                                                                         for x in shown[:50]]})
    assert r.status_code == 200, r.text
    assert r.json()["evidence_revision"] == 1


def test_review_with_old_report_revision_rejected(slow):
    rid = slow.post("/runs", json=RUN).json()["run_id"]
    slow.clock.tick(11)
    assert slow.post(f"/runs/{rid}/review", json={**REVIEW, "report_revision": 1}).status_code == 200  # 2판으로
    r = slow.post(f"/runs/{rid}/review", json={**REVIEW, "report_revision": 1})
    assert r.status_code == 409 and r.json()["error_code"] == "REVIEW_REVISION_STALE"


def test_review_response_stale_matches_run(slow):
    """다시 만드는 중에 같은 검토를 다시 저장해도 응답 report_stale 은 실제 실행 상태와 같음."""
    rid = slow.post("/runs", json=RUN).json()["run_id"]
    slow.clock.tick(11)
    first = slow.post(f"/runs/{rid}/review", json=REVIEW).json()
    assert first["report_stale"] and first["changed"] and first["report_revision"] == 2
    again = slow.post(f"/runs/{rid}/review", json=REVIEW).json()
    run = slow.get(f"/runs/{rid}").json()
    assert again["report_stale"] is True and again["changed"] is False
    assert again["report_stale"] == run["report_stale"] and again["report_revision"] == run["report_revision"] == 2
    slow.clock.tick(11)  # 다시 만든 뒤 같은 검토 → stale 아님
    done = slow.post(f"/runs/{rid}/review", json=REVIEW).json()
    assert done["report_stale"] is False and done["changed"] is False
    assert slow.get(f"/runs/{rid}").json()["report_stale"] is False


# ---------------------------------------------------------------- 토큰·역할


def test_token_required_except_health(secured):
    assert secured.get("/health").status_code == 200
    for path in ["/overview?as_of=2025-09-26&city=춘천", "/snapshots/active", "/data/capabilities", "/runs"]:
        r = secured.get(path)
        assert r.status_code == 401 and r.json()["error_code"] == "UNAUTHENTICATED", path
    assert secured.get("/runs", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert secured.get("/runs", headers={"Authorization": "tok-view"}).status_code == 401  # Bearer 없이
    assert secured.get("/runs", headers=H["view"]).status_code == 200
    assert secured.post("/qa", json={"question": "강남 1인세대 비율 상위 동은?", "as_of": "2025-10-20"}).status_code == 401


def test_roles_enforced_and_actor_from_token(secured):
    # 조회 역할은 실행·질의응답을 만들 수 없음 (LLM 비용, SA-04)
    r = secured.post("/runs", json=RUN, headers=H["view"])
    assert r.status_code == 403 and r.json()["error_code"] == "FORBIDDEN"
    assert secured.post("/qa", json={"question": "강남 1인세대 비율 상위 동은?", "as_of": "2025-10-20"},
                        headers=H["view"]).status_code == 403
    assert secured.get("/runs", headers=H["view"]).json()["items"] == []
    run_id = secured.post("/runs", json=RUN, headers=H["review"]).json()["run_id"]
    f = secured.post(f"/runs/{run_id}/followup", json={"question": "왜 이 등급이야?"}, headers=H["view"])
    assert f.status_code == 403
    r = secured.post(f"/runs/{run_id}/review", json=REVIEW, headers=H["view"])
    assert r.status_code == 403 and r.json()["error_code"] == "FORBIDDEN"
    assert secured.post(f"/runs/{run_id}/review", json=REVIEW, headers=H["review"]).status_code == 200
    rev = secured.get(f"/runs/{run_id}", headers=H["view"]).json()["report_revision"]
    assert _approve(secured, run_id, rev, "a1", headers=H["review"]).status_code == 403
    a = _approve(secured, run_id, rev, "a1", headers=H["approve"], approver="<script>x</script>")
    assert a.status_code == 200, a.text
    hist = secured.get(f"/runs/{run_id}/history", headers=H["view"]).json()
    assert hist["reviews"][0]["reviewer"] == "worker-01"  # 본문의 "아무개"가 아니라 토큰 이름
    assert hist["approvals"][0]["approver"] == "lead-01"
    rows = {x["action"]: x for x in _audit(secured, run_id)}
    assert rows["run_created"]["actor"] == "worker-01"
    assert rows["review_saved"]["actor"] == "worker-01"
    assert json.loads(rows["review_saved"]["detail"])["claimed_actor"] == "아무개"
    assert rows["approval_received"]["actor"] == "lead-01"
    # 현장 확인 결과: 검토 역할 이상, 기록자는 토큰 이름
    cid = secured.get(f"/runs/{run_id}", headers=H["view"]).json()["candidates"][0]["candidate_id"]
    out = {"candidate_id": cid, "checked_households": 3, "contacted": 1, "absent": 1, "new_links": 0, "crises_found": 0,
           "idempotency_key": "o1"}
    assert secured.post(f"/runs/{run_id}/outcomes", json=out, headers=H["view"]).status_code == 403
    o = secured.post(f"/runs/{run_id}/outcomes", json=out, headers=H["review"])
    assert o.status_code == 200 and o.json()["outcome"]["recorded_by"] == "worker-01"


def test_dev_mode_marks_actor(dev):
    run_id = dev.post("/runs", json=RUN).json()["run_id"]
    assert dev.post(f"/runs/{run_id}/review", json=REVIEW).status_code == 200
    rows = _audit(dev, run_id)
    assert {x["actor"] for x in rows if x["action"] in ("run_created", "review_saved")} == {DEV_ACTOR}
    # 기록 이름은 본문 값 (개발 모드)
    assert dev.get(f"/runs/{run_id}/history").json()["reviews"][0]["reviewer"] == "아무개"


def test_auth_from_env(monkeypatch, tmp_path):
    for k in ("LINKRADAR_API_TOKEN", "LINKRADAR_API_USER", "LINKRADAR_API_ROLE", "LINKRADAR_API_TOKENS_FILE"):
        monkeypatch.delenv(k, raising=False)
    assert not Auth.from_env().enabled
    monkeypatch.setenv("LINKRADAR_API_TOKEN", "s3cret")
    monkeypatch.setenv("LINKRADAR_API_USER", "demo-lead")
    a = Auth.from_env()
    # 공용 토큰(화면 번들에 들어가는 토큰)의 기본 역할은 조회, 기록 이름은 "공용:" 표시
    who = a.identify("Bearer s3cret")
    assert who == Identity("demo-lead", "조회", True, shared=True) and who.actor() == "공용:demo-lead"
    monkeypatch.setenv("LINKRADAR_API_ROLE", "승인")
    assert Auth.from_env().identify("Bearer s3cret").role == "승인"
    monkeypatch.delenv("LINKRADAR_API_ROLE")
    f = tmp_path / "tokens.yaml"
    f.write_text("tokens:\n  t-w: {name: worker-02, role: 검토}\n", encoding="utf-8")
    monkeypatch.setenv("LINKRADAR_API_TOKENS_FILE", str(f))
    a = Auth.from_env()
    assert a.identify("Bearer t-w").role == "검토" and a.identify("Bearer s3cret").name == "demo-lead"
    assert not a.identify("Bearer t-w").shared and a.identify("Bearer t-w").actor() == "worker-02"
    f.write_text("tokens:\n  t-x: {name: x, role: 관리자}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Auth.from_env()


def test_prod_disables_docs_and_requires_token(tmp_path):
    with pytest.raises(RuntimeError):
        _app(tmp_path, auth=Auth(), env="prod")
    app = _app(tmp_path, auth=Auth(TOKENS), env="prod")
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    app.state.store.close()
    app = create_app(db_path=tmp_path / "d.sqlite", delay_s=0, auth=Auth(), env="dev")
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 200
    app.state.store.close()


def test_cors_allows_authorization_header(secured):
    r = secured.options("/runs", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                                          "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


# ---------------------------------------------------------------- 입력 한도


def test_huge_body_413(dev):
    r = dev.post("/qa", json={"question": "가" * (2 * 1024 * 1024), "as_of": "2025-10-20"})
    assert r.status_code == 413 and r.json()["error_code"] == "PAYLOAD_TOO_LARGE"
    assert dev.get("/runs").json()["items"] == []  # 저장되지 않음


def test_huge_chunked_body_413(dev):
    body = json.dumps({"question": "가" * 100_000, "as_of": "2025-10-20"}).encode()
    chunks = (body[i:i + 8192] for i in range(0, len(body), 8192))  # Content-Length 없이 보냄
    r = dev.post("/qa", content=chunks, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error_code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.parametrize("path,body,hint", [
    ("/qa", {"question": "가" * 501, "as_of": "2025-10-20"}, "500자"),
    ("/qa", {"question": "", "as_of": "2025-10-20"}, "500자"),
    ("/runs", {**RUN, "question": "가" * 501}, "500자"),
    ("REVIEW", {**REVIEW, "memo": "가" * 2001}, "2000자"),
    ("REVIEW", {**REVIEW, "reviewer": "가" * 51}, "1~50자"),
    ("REVIEW", {**REVIEW, "reviewer": ""}, "1~50자"),
    ("REVIEW", {**REVIEW, "checked_evidence_ids": [f"EV-{i}" for i in range(51)]}, "50개"),
    ("REVIEW", {**REVIEW, "resource_judgements": [{"resource_id": "R0739", "judgement": "적합", "memo": "가" * 2001}]},
     "2000자"),
    ("APPROVE", {"approver": "가" * 51}, "1~50자"),
    ("APPROVE", {"idempotency_key": "has space"}, "멱등 키"),
    ("APPROVE", {"idempotency_key": "k" * 101}, "멱등 키"),
    ("APPROVE", {"memo": "가" * 2001}, "2000자"),
])
def test_field_limits(dev, path, body, hint):
    if path in ("REVIEW", "APPROVE"):
        run_id = dev.post("/runs", json=RUN).json()["run_id"]
        if path == "REVIEW":
            path = f"/runs/{run_id}/review"
        else:
            path = f"/runs/{run_id}/approve"
            body = {"decision": "approved", "report_revision": 1, "approver": "lead-01", "idempotency_key": "k",
                    **body}
    r = dev.post(path, json=body)
    assert r.status_code == 400 and r.json()["error_code"] == "INVALID_REQUEST", r.text
    assert hint in r.json()["message"]


def test_limits_allow_normal_values(dev):
    assert dev.post("/qa", json={"question": "가" * 500, "as_of": "2025-10-20"}).status_code == 200
    run_id = dev.post("/runs", json=RUN).json()["run_id"]
    ids = [e["evidence_id"] for e in dev.get(f"/runs/{run_id}/evidence").json()["evidence_card"]["evidence"]]
    ok = {**REVIEW, "memo": "가" * 2000, "reviewer": "가" * 50, "checked_evidence_ids": ids}
    assert dev.post(f"/runs/{run_id}/review", json=ok).status_code == 200
    key = f"{run_id}:approved:2"  # 화면이 만드는 키 모양
    assert _approve(dev, run_id, 2, key).status_code == 200


# ---------------------------------------------------------------- SQLite, ID


def test_store_wal_and_busy_timeout(dev):
    conn = dev.app.state.store._conn
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_get_without_due_jobs_opens_no_write_tx(dev, monkeypatch):
    run_id = dev.post("/runs", json=RUN).json()["run_id"]
    store = dev.app.state.store
    calls = []
    real = store.tx

    def spy():
        calls.append(1)
        return real()

    monkeypatch.setattr(store, "tx", spy)
    for p in ["", "/evidence", "/report", "/history"]:
        assert dev.get(f"/runs/{run_id}{p}").status_code == 200
    assert dev.get("/runs").status_code == 200
    assert calls == []


def test_ids_are_not_count_based(dev):
    run_id = dev.post("/runs", json=RUN).json()["run_id"]
    assert re.match(r"^run-\d{8}-\d{6}-[0-9a-f]{8}$", run_id)
    r1 = dev.post(f"/runs/{run_id}/review", json=REVIEW).json()["review_id"]
    # 보존 정책 등으로 행이 지워져도 다음 ID가 겹치지 않음
    store = dev.app.state.store
    with store.tx():
        store._exec("DELETE FROM resource_judgements")
        store._exec("DELETE FROM reviews")
    r2 = dev.post(f"/runs/{run_id}/review", json={**REVIEW, "resource_judgements": []}).json()["review_id"]
    assert r1 != r2 and r1.startswith("rev-") and r2.startswith("rev-")
    a = _approve(dev, run_id, 2, "k-1").json()["approval_id"]
    assert re.match(r"^apr-\d{8}-\d{6}-[0-9a-f]{8}$", a)
    ids = {dev.post("/runs", json=RUN).json()["run_id"] for _ in range(20)}  # 같은 초에도 다름
    assert len(ids) == 20


# ---------------------------------------------------------------- 구조화 로그


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(JsonFormatter().format(record))


def test_request_log_json_without_question(dev):
    grab = _Grab()
    logging.getLogger(LOGGER + ".request").addHandler(grab)
    try:
        secret_q = "강남 1인세대 비율 상위 동은? 홍길동 010-1234-5678"
        r = dev.post("/qa", json={"question": secret_q, "as_of": "2025-10-20"}, headers={"X-Request-ID": "req-abc"})
        assert r.headers["X-Request-ID"] == "req-abc"
        run_id = r.json()["run_id"]
        dev.get("/runs/run-none")
        dev.get(f"/runs/{run_id}")
    finally:
        logging.getLogger(LOGGER + ".request").removeHandler(grab)
    recs = [json.loads(x) for x in grab.lines]
    assert recs[0]["request_id"] == "req-abc" and recs[0]["run_id"] == run_id and recs[0]["status"] == 200
    assert recs[0]["actor"] == DEV_ACTOR
    assert recs[1]["error_code"] == "RUN_NOT_FOUND" and recs[1]["status"] == 404
    assert recs[2]["run_id"] == run_id and re.match(r"^[0-9a-f]{16}$", recs[2]["request_id"])
    assert all("홍길동" not in x and "1234-5678" not in x for x in grab.lines)


# ---------------------------------------------------------------- 경로에 쓰는 ID


def test_snapshot_id_checked_before_path(tmp_path):
    from bigcontest.snapshot import SnapshotError, Snapshot, check_snapshot_id, publish, read_manifest

    for bad in ["../x", "..", "a/b", "a\\b", "snap.1", "", "x" * 81, "/etc"]:
        with pytest.raises(SnapshotError):
            check_snapshot_id(bad)
        with pytest.raises(SnapshotError):
            Snapshot.open(bad, root=tmp_path)
    with pytest.raises(SnapshotError):
        read_manifest("../../etc", tmp_path)
    for good in ["snap-20261013-100000-ab12", "dev-0123456789ab", "snap-fake-20261014-090000-ffff", "snap-a"]:
        assert check_snapshot_id(good) == good
    with pytest.raises(SnapshotError):
        publish(lambda f: None, root=tmp_path, snapshot_id="../evil", data_from=date(2025, 7, 1),
                data_to=date(2025, 12, 31))
    assert not (tmp_path.parent / "evil").exists() and not (tmp_path.parent / ".building-..").exists()


def test_snapshot_verify_detects_changed_file(tmp_path):
    import pandas as pd

    from bigcontest.snapshot import Snapshot, SnapshotError, publish

    def build(folder):
        pd.DataFrame({"n": [1, 2]}).to_parquet(folder / "c.parquet", index=False)

    publish(build, root=tmp_path, snapshot_id="snap-v", data_from=date(2025, 7, 1), data_to=date(2025, 12, 31))
    Snapshot.open("snap-v", root=tmp_path, verify=True)
    pd.DataFrame({"n": [9, 9]}).to_parquet(tmp_path / "snap-v" / "c.parquet", index=False)
    Snapshot.open("snap-v", root=tmp_path)  # 기본은 확인 안 함
    with pytest.raises(SnapshotError, match="c.parquet"):
        Snapshot.open("snap-v", root=tmp_path, verify=True)


def test_run_id_checked_before_path(tmp_path):
    from bigcontest.runmeta import RunMetadata, check_run_id, read_run_metadata, write_run_metadata

    for bad in ["../evil", "a/b", "a:b", "x.json", "", "-x", "x" * 101]:
        with pytest.raises(ValueError):
            check_run_id(bad)
    meta = RunMetadata(run_id="../evil", created_at="2026-10-13T10:00:00", snapshot_id="snap-a",
                       analysis_mode="simulated_replay", as_of="2025-09-26", code_commit=None, code_dirty=None)
    with pytest.raises(ValueError):
        write_run_metadata(meta, tmp_path / "runs")
    with pytest.raises(ValueError):
        read_run_metadata("../evil", tmp_path / "runs")
    assert not (tmp_path / "evil.json").exists()
    assert check_run_id("run-20261013-100000-3fa9c2d1") and check_run_id("inv-ok")


# ---------------------------------------------------------------- 에이전트 start·재개


INVESTIGATE_Q = "이번 추석 전에 춘천에서 어디부터 확인해야 해?"


def test_agent_start_rejects_existing_run_id(tmp_path):
    from bigcontest.agent.graph import Agent, RunAlreadyExists

    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    try:
        a.start("dup-start", "qa", "2025-09-26", question=INVESTIGATE_Q)
        before = a.status("dup-start")["values"]
        with pytest.raises(RunAlreadyExists):
            a.start("dup-start", "qa", "2025-09-26", question="강남 1인세대 비율 상위 동은?")
        assert a.status("dup-start")["values"] == before
        with pytest.raises(ValueError):
            a.start("../bad", "qa", "2025-09-26", question=INVESTIGATE_Q)
    finally:
        a.close()


def test_agent_more_resume_once_per_key(tmp_path):
    from bigcontest.agent.graph import Agent

    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    try:
        a.start("more-once", "qa", "2025-09-26", question=INVESTIGATE_Q)
        r1 = a.approve("more-once", "more", report_revision=1, idempotency_key="m-1")
        assert r1["status"] == "accepted"
        path_after = a.status("more-once")["values"]["path"].count("collect_signals")
        r2 = a.approve("more-once", "more", report_revision=1, idempotency_key="m-1")
        assert r2["status"] == "duplicate" and r2["decision"] == "more"
        assert a.status("more-once")["values"]["path"].count("collect_signals") == path_after  # 다시 돌지 않음
        assert a.store.count("more-once") == 0  # 부작용 기록 표와 섞이지 않음
    finally:
        a.close()


# ---------------------------------------------------------------- 키 가리기


def test_collect_log_masks_api_keys(tmp_path):
    import pandas as pd

    from bigcontest.resources.collect import Fetcher, mask_secrets, run_collection

    assert mask_secrets("GET https://a/b?serviceKey=AbC%2B1&pageNo=1") == "GET https://a/b?serviceKey=***&pageNo=1"
    assert mask_secrets("confmKey=xyz key=1 monkey=2") == "confmKey=*** key=*** monkey=2"

    class Leaky(Fetcher):
        source = "leaky"

        def fetch(self, page):
            raise ConnectionError("https://apis.data.go.kr/x?serviceKey=REAL-SECRET-123&pageNo=1 timed out")

        def parse(self, raw):
            return []

    cfg = {"max_calls": 3, "max_retries": 1, "retry_wait_sec": 0, "raw_dir": tmp_path / "raw",
           "run_log": tmp_path / "log.csv", "corrections": tmp_path / "corr.csv"}
    led = pd.DataFrame({"resource_id": ["R1"], "src": ["leaky"], "source_record_id": ["S1"],
                        "operating_status": ["운영"], "last_verified": [""]})
    res = run_collection(led, Leaky(), ["name"], cfg=cfg, corrections=pd.DataFrame())
    assert res.run["status"] == "실패" and "serviceKey=***" in res.run["message"]
    assert "REAL-SECRET-123" not in (tmp_path / "log.csv").read_text(encoding="utf-8")
