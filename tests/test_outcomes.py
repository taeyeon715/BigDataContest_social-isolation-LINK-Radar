"""현장 확인 결과: 승인된 실행에만, 건수만, 모아 보기에서는 10 미만을 숨김."""

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
from bigcontest.agent.schemas import (  # noqa: E402
    OutcomeListResponse,
    OutcomeResponse,
    OutcomeSummaryResponse,
    RunAccepted,
)
from bigcontest.rules import outcomes as O  # noqa: E402

COUNTS = {"checked_households": 12, "contacted": 7, "absent": 4, "new_links": 3, "crises_found": 1}


@pytest.fixture(params=["fake", "real"])
def client(tmp_path, request):
    kw = {"backend": "real", "tools_backend": "stub", "llm": "none"} if request.param == "real" else {}
    app = create_app(db_path=tmp_path / "state.sqlite", delay_s=0, **kw)
    with TestClient(app) as c:
        yield c
    if request.param == "real":
        app.state.runs.close()
    app.state.store.close()


def _run(c, approve=True) -> tuple[str, str]:
    if getattr(c.app.state.runs, "source", "fake") == "real":
        body = {"mode": "qa", "question": "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?", "as_of": "2025-09-26"}
    else:
        body = {"mode": "qa", "candidate_id": "C-20250926-001", "as_of": "2025-09-26"}
    r = c.post("/runs", json=body)
    assert r.status_code == 202, r.text
    rid = RunAccepted.model_validate(r.json()).run_id
    st = c.get(f"/runs/{rid}").json()
    if approve:
        a = c.post(f"/runs/{rid}/approve", json={"decision": "approved", "report_revision": st["report_revision"],
                                                 "approver": "lead-01", "idempotency_key": f"{rid}:ok"})
        assert a.status_code == 200, a.text
    return rid, st["candidates"][0]["candidate_id"]


def post(c, rid, cid, key, **kw):
    return c.post(f"/runs/{rid}/outcomes", json={"candidate_id": cid, **COUNTS, **kw, "idempotency_key": key})


def test_record_only_after_approval_counts_only(client):
    rid, cid = _run(client, approve=False)
    r = post(client, rid, cid, "o-1")
    assert r.status_code == 409 and r.json()["error_code"] == "RUN_NOT_APPROVED"
    rid, cid = _run(client)
    out = OutcomeResponse.model_validate(post(client, rid, cid, "o-2").json())
    assert out.outcome.candidate_id == cid and out.outcome.checked_households == 12 and not out.duplicate
    assert out.outcome.trigger_code and out.outcome.city == "춘천"
    # 같은 키 같은 내용 → 1건, 같은 키 다른 내용 → 409
    assert OutcomeResponse.model_validate(post(client, rid, cid, "o-2").json()).duplicate
    assert post(client, rid, cid, "o-2", contacted=1).json()["error_code"] == "IDEMPOTENCY_KEY_REUSED"
    # 건수 관계·없는 후보·개인 정보 칸은 거절
    assert post(client, rid, cid, "o-3", contacted=10, absent=5).json()["error_code"] == "INVALID_REQUEST"
    assert post(client, rid, "C-없음", "o-4").json()["error_code"] == "CANDIDATE_NOT_IN_RUN"
    assert post(client, rid, cid, "o-5", memo="김OO 할머니 부재").status_code == 400
    assert post(client, rid, cid, "o-6", checked_households=-1).status_code == 400
    # 고쳐 적으면 새 기록이 기준, 이 실행 상세에서는 10 미만도 그대로
    assert post(client, rid, cid, "o-7", crises_found=2).status_code == 200
    lst = OutcomeListResponse.model_validate(client.get(f"/runs/{rid}/outcomes").json())
    assert lst.history_count == 2 and [x.crises_found for x in lst.items] == [2]
    audit = [a["action"] for a in client.app.state.store.audit_rows(rid)]
    assert audit.count("outcome_recorded") == 2 and "outcome_rejected" in audit
    assert not any("김OO" in json.dumps(a, ensure_ascii=False) for a in client.app.state.store.audit_rows(rid))


def test_summary_hides_small_cells(client):
    rid, cid = _run(client)
    assert post(client, rid, cid, "s-1").status_code == 200
    s = OutcomeSummaryResponse.model_validate(client.get("/outcomes/summary", params={"city": "춘천"}).json())
    row = s.rows[0]
    assert row.checked_households == 12 and row.places == 1 and row.runs == 1
    assert row.contacted is None and row.absent is None and row.crises_found is None  # 10 미만은 숫자 없이
    assert set(row.small_cells) == {"contacted", "absent", "new_links", "crises_found"}
    assert row.contact_rate is None and row.crisis_rate is None  # 숨긴 칸이 들어간 비율도 없음
    ev = OutcomeSummaryResponse.model_validate(client.get("/outcomes/summary", params={"by": "event"}).json())
    assert ev.by == "event" and ev.rows
    assert client.get("/outcomes/summary", params={"by": "x"}).status_code == 400
    assert client.get("/outcomes/summary", params={"city": "서울"}).status_code == 400


def test_summarize_unit():
    base = {"city": "춘천", "trigger_code": "W", "event_id": "춘천:W:2025", "adm_cd": None}
    recs = [
        {**base, "outcome_id": "a", "run_id": "r1", "candidate_id": "c1", "created_at": "1", "seq": 1, **COUNTS},
        {**base, "outcome_id": "b", "run_id": "r1", "candidate_id": "c1", "created_at": "2", "seq": 2,
         **{**COUNTS, "contacted": 9}},  # 고쳐 적음 → 이것만
        {**base, "outcome_id": "c", "run_id": "r2", "candidate_id": "c2", "created_at": "3", "seq": 3, **COUNTS},
    ]
    (row,) = O.summarize(recs)
    assert row["places"] == 2 and row["runs"] == 2 and row["checked_households"] == 24 and row["contacted"] == 16
    assert row["contact_rate"] == round(16 / 24, 3) and row["absent"] is None and "absent" in row["small_cells"]
    assert O.trigger_code_of({"event_id": "강남:R1:20251003"}) == "R1"
    assert O.trigger_code_of({"candidate_type": "unexpected_activity_deviation"}) == "DEV"


# ---------------------------------------------------------------- 같은 시각 정정


def _fixed_app(tmp_path, backend, name="fixed.sqlite"):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 14, 6, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    kw = {"backend": "real", "tools_backend": "stub", "llm": "none"} if backend == "real" else {}
    return create_app(db_path=tmp_path / name, delay_s=0, clock=lambda: now, **kw)


def _close(app):
    if getattr(app.state.runs, "source", "fake") == "real":
        app.state.runs.close()
    app.state.store.close()


@pytest.mark.parametrize("backend", ["fake", "real"])
def test_same_time_correction_wins_regardless_of_random_id(tmp_path, backend):
    """재현(audit_integrity.py): 시각 고정 + ID 끝을 먼저 z, 나중 a 로 → 고친 값(30)이 조회·집계·재시작 뒤에도 기준."""
    from unittest.mock import patch

    from api.services import runs as RS

    app = _fixed_app(tmp_path, backend)
    with TestClient(app) as c:
        rid, cid = _run(c)
        with patch.object(RS, "new_id", side_effect=[rid + "-z", rid + "-a"]):
            first = post(c, rid, cid, "old", checked_households=12)
            second = post(c, rid, cid, "fix", checked_households=30)
        assert first.status_code == second.status_code == 200
        assert first.json()["outcome"]["created_at"] == second.json()["outcome"]["created_at"]  # 같은 시각
        got = OutcomeListResponse.model_validate(c.get(f"/runs/{rid}/outcomes").json())
        assert [i.checked_households for i in got.items] == [30] and got.history_count == 2
        summary = c.get("/outcomes/summary").json()["rows"]
        assert summary[0]["checked_households"] == 30
    _close(app)
    app = _fixed_app(tmp_path, backend)  # 다시 켜도 같음
    with TestClient(app) as c:
        assert c.get(f"/runs/{rid}/outcomes").json()["items"][0]["checked_households"] == 30
        assert c.get("/outcomes/summary").json()["rows"][0]["checked_households"] == 30
    _close(app)


def test_concurrent_corrections_follow_commit_order(tmp_path):
    """동시에 여러 번 고쳐 적어도 가장 나중에 저장된(커밋된) 값이 기준. 저장 순서 seq 는 겹치지 않음."""
    import threading

    app = _fixed_app(tmp_path, "fake")
    with TestClient(app) as c:
        rid, cid = _run(c)
        out = []

        def go(n):
            out.append(post(c, rid, cid, f"k{n}", checked_households=10 + n, contacted=1, absent=1))

        ts = [threading.Thread(target=go, args=(n,)) for n in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert all(r.status_code == 200 for r in out)
        rows = app.state.store.list_outcomes(run_id=rid)
        assert [r["seq"] for r in rows] == sorted({r["seq"] for r in rows}) and len(rows) == 8
        last = rows[-1]["checked_households"]
        assert c.get(f"/runs/{rid}/outcomes").json()["items"][0]["checked_households"] == last
    _close(app)


def test_latest_needs_seq_and_ignores_id_and_time():
    base = {"run_id": "r", "candidate_id": "c", "created_at": "2026-10-14T05:00:00+09:00"}
    assert O.latest([{**base, "outcome_id": "out-z", "seq": 1, "checked_households": 12},
                     {**base, "outcome_id": "out-a", "seq": 2, "checked_households": 30}])[0]["checked_households"] == 30
    with pytest.raises(ValueError):
        O.latest([{**base, "outcome_id": "x", "checked_households": 1}])


def test_old_db_without_seq_is_migrated(tmp_path):
    """seq 칸이 없던 예전 파일: 켜면 칸을 더하고 넣은 순서(rowid)로 채움."""
    import sqlite3

    from api.store import Store

    path = tmp_path / "old.sqlite"
    s = Store(path)
    s.close()
    con = sqlite3.connect(path)
    con.execute("DROP INDEX IF EXISTS idx_outcomes_seq")
    con.execute("ALTER TABLE field_outcomes DROP COLUMN seq")
    con.execute("INSERT INTO runs (run_id, mode, as_of, analysis_mode, snapshot_id, status, created_at, updated_at)"
                " VALUES ('r','qa','2025-09-26','simulated_replay','s','approved','t','t')")
    for oid, n in (("out-z", 12), ("out-a", 30)):
        con.execute("INSERT INTO field_outcomes (outcome_id, run_id, candidate_id, city, trigger_code,"
                    " checked_households, contacted, absent, new_links, crises_found, recorded_by, idempotency_key,"
                    " created_at) VALUES (?,?,?,?,?,?,0,0,0,0,'x',?,'same')", (oid, "r", "c", "춘천", "W", n, oid))
    con.commit()
    con.close()
    s = Store(path)
    assert O.latest(s.list_outcomes(run_id="r"))[0]["checked_households"] == 30
    s.close()
