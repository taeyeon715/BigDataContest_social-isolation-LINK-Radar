"""승인·재개 테스트 (에이전트 설계 §7).

- 승인 대기에서 프로세스 종료 → 재시작 → 승인 → 확정 기록 정확히 1건 (같은 프로세스 안 재생성 + 진짜 하위 프로세스)
- 같은 승인 2번 → 1건, 동시에 2번 → 1건
- 오래된 report_revision 승인 → 거절, 다시 대기
- Guard ABSTAIN·계약 위반은 승인해도 풀리지 않음 (보류 기록)
"""

import json
import os
import subprocess
import sys
import textwrap
import threading

from bigcontest.agent.checkpoint import RecordStore
from bigcontest.agent.graph import Agent, config_for

AS_OF = "2025-09-26"
Q = "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?"


def test_restart_in_process_then_approve_once(tmp_path):
    db = tmp_path / "runs.db"
    a1 = Agent(db, tools_backend="stub")
    st = a1.start("run-1", "qa", AS_OF, question=Q, snapshot_id="snap-A")
    assert st["waiting"]
    rev = st["interrupt"]["report_revision"]
    a1.close()
    del a1  # 그래프·체크포인터 객체를 버림 (재시작 흉내)

    a2 = Agent(db, tools_backend="stub")  # 새 그래프 인스턴스가 같은 DB 를 다시 엶
    st2 = a2.status("run-1")
    assert st2["waiting"] and st2["interrupt"]["report_revision"] == rev
    assert st2["values"]["snapshot_id"] == "snap-A"  # 처음 고정한 스냅샷 그대로
    res = a2.approve("run-1", "approved", report_revision=rev)
    assert res["status"] == "accepted" and res["record"]["kind"] == "확정"
    assert a2.store.count("run-1", "finalize") == 1
    assert res["record"]["payload"]["snapshot_id"] == "snap-A"
    assert res["record"]["payload"]["report_revision"] == rev
    a2.close()


_START = textwrap.dedent(
    """
    import json, sys
    from bigcontest.agent.graph import Agent
    a = Agent(sys.argv[1], tools_backend="stub")
    st = a.start("run-p", "qa", "2025-09-26", question=sys.argv[2])
    print(json.dumps({"waiting": st["waiting"], "rev": st["interrupt"]["report_revision"]}))
    """
)
_APPROVE = textwrap.dedent(
    """
    import json, sys
    from bigcontest.agent.graph import Agent
    a = Agent(sys.argv[1], tools_backend="stub")
    res = a.approve("run-p", "approved", report_revision=int(sys.argv[2]))
    print(json.dumps({"status": res["status"], "kind": res["record"]["kind"]}, ensure_ascii=False))
    """
)


def _py(code: str, *args: str) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, encoding="utf-8",
                         env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_restart_real_process_then_approve_once(tmp_path):
    db = str(tmp_path / "runs.db")
    started = _py(_START, db, Q)  # 프로세스 1: 승인 대기에서 끝남
    assert started["waiting"]
    first = _py(_APPROVE, db, str(started["rev"]))  # 프로세스 2: 재시작 후 승인
    assert first == {"status": "accepted", "kind": "확정"}
    second = _py(_APPROVE, db, str(started["rev"]))  # 프로세스 3: 같은 승인 다시
    assert second == {"status": "duplicate", "kind": "확정"}
    store = RecordStore(db)
    assert store.count("run-p", "finalize") == 1
    store.close()


def test_duplicate_approval_one_record(tmp_path):
    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    a.start("dup", "qa", AS_OF, question=Q)
    r1 = a.approve("dup", "approved", report_revision=1)
    r2 = a.approve("dup", "approved", report_revision=1)
    assert r1["status"] == "accepted" and r2["status"] == "duplicate"
    assert r2["record"]["created_at"] == r1["record"]["created_at"]
    assert a.store.count("dup") == 1
    a.close()


def test_concurrent_approval_one_record(tmp_path):
    db = tmp_path / "runs.db"
    a0 = Agent(db, tools_backend="stub")
    a0.start("conc", "qa", AS_OF, question=Q)
    a0.close()
    agents = [Agent(db, tools_backend="stub"), Agent(db, tools_backend="stub")]  # 두 API 작업자가 같은 DB 를 씀
    results, barrier = [], threading.Barrier(2)

    def approve(agent):
        barrier.wait()
        results.append(agent.approve("conc", "approved", report_revision=1))

    threads = [threading.Thread(target=approve, args=(ag,)) for ag in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert agents[0].store.count("conc", "finalize") == 1
    assert sorted(r["status"] for r in results) in (["accepted", "duplicate"], ["duplicate", "accepted"])
    kinds = {r["record"]["kind"] for r in results}
    assert kinds == {"확정"}
    for ag in agents:
        ag.close()


def test_stale_report_revision_rejected(tmp_path):
    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    a.start("stale", "qa", AS_OF, question=Q)
    assert a.approve("stale", "more", report_revision=1)["status"] == "accepted"  # 추가 조사 → 보고서 2판
    st = a.status("stale")
    assert st["waiting"] and st["interrupt"]["report_revision"] == 2
    res = a.approve("stale", "approved", report_revision=1)  # 옛 버전으로 승인
    assert res["status"] == "rejected" and res["code"] == "STALE_REPORT_REVISION"
    assert a.status("stale")["waiting"] and a.store.count("stale") == 0
    res = a.approve("stale", "approved", report_revision=2)
    assert res["status"] == "accepted" and res["record"]["kind"] == "확정"
    assert res["record"]["payload"]["evidence_revision"] == 2
    a.close()


def test_report_needing_regeneration_rejected(tmp_path):
    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    a.start("regen", "qa", AS_OF, question=Q)
    values = a.status("regen")["values"]
    # 담당자 자원 판정이 바뀌어 보고서가 "다시 만들어야 함" 상태가 됨 (검토 API 가 하는 일)
    cfg = config_for("regen")
    a.app.update_state(cfg, {"report": {**values["report"], "needs_regeneration": True}}, as_node="guard_report")
    a.app.invoke(None, cfg)  # 다시 승인 대기로
    assert a.status("regen")["waiting"]
    res = a.approve("regen", "approved", report_revision=1)
    assert res["status"] == "rejected" and a.store.count("regen") == 0
    a.close()


def test_guard_abstain_not_overridden_by_approval(tmp_path):
    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    st = a.start("abst", "qa", AS_OF, question=Q, scenario="confounder_timeout_always")
    assert st["waiting"] and st["interrupt"]["is_abstain"]
    res = a.approve("abst", "approved", report_revision=st["interrupt"]["report_revision"])
    assert res["record"]["kind"] == "보류"
    assert "승인으로 풀리지 않음" in res["record"]["payload"]["approval"]["note"]
    a.close()


def test_contract_violation_not_reopened_by_more(tmp_path):
    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    st = a.start("strict", "qa", AS_OF, question=Q, analysis_mode="strict_as_of")
    assert st["values"]["path"][-2:] == ["check_availability", "abstain_report"]
    res = a.approve("strict", "more", report_revision=st["interrupt"]["report_revision"])
    assert res["record"]["kind"] == "보류"
    assert "collect_signals" not in a.status("strict")["values"]["path"]
    a.close()


def test_publish_recorded_once(tmp_path):
    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    a.start("mon", "monitoring", AS_OF)
    a.start("mon", "monitoring", AS_OF)  # 같은 run_id 로 다시 실행해도 발행 기록은 1건
    assert a.store.count("mon", "publish") == 1
    a.close()
