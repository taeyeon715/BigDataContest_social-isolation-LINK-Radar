"""에이전트 기능: 추가 조사 계획, 담당자 검토 뒤 보고서 다시 쓰기, 이어 묻기, 체크포인트 줄이기,
도구별 소요 시간, 가짜 API 의 판단 기록·실행 자원·이어 묻기. 모두 가짜 도구·가짜 LLM (유료 호출 없음)."""

from __future__ import annotations

import sqlite3
from datetime import date
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from typing import get_args  # noqa: E402

from api.app import create_app  # noqa: E402
from bigcontest.agent import nodes  # noqa: E402
from bigcontest.agent.graph import Agent  # noqa: E402
from bigcontest.agent.state import report_dict  # noqa: E402
from bigcontest.agent.followup import pick_candidates  # noqa: E402
from bigcontest.agent.schemas import (  # noqa: E402
    Candidate,
    EvidenceCard,
    ExtraTool,
    FollowupResponse,
    RunResourcesResponse,
    RunStatusResponse,
)
from bigcontest.agent.tools import EXTRA_TOOLS  # noqa: E402

Q = "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?"
AS_OF = "2025-09-26"


@pytest.fixture
def agent(tmp_path):
    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    yield a
    a.close()


def levels(v):
    return [(c["candidate_id"], c["candidate_level"]) for c in v["candidates"]]


def test_extra_tool_whitelist_matches_schema():
    assert set(get_args(ExtraTool)) == set(EXTRA_TOOLS)


@pytest.mark.parametrize("llm", ["none", "fake", "fake_all_tools", "fake_error"])
def test_more_memo_plan_whitelisted_and_levels_unchanged(agent, llm):
    rid = f"plan-{llm}"
    st = agent.start(rid, "qa", AS_OF, question=Q, llm=llm)
    before = levels(st["values"])
    res = agent.approve(rid, "more", st["values"]["report_revision"], idempotency_key="m",
                        focus={"memo": "연휴 뒤 회복과 겨울 체류도 확인", "counter_checks": ["강수"]})
    assert res["status"] == "accepted"
    v = agent.status(rid)["values"]
    plan = v["extra_plan"]
    assert set(plan["tools"]) <= set(EXTRA_TOOLS) and "run_sql" not in plan["tools"]
    assert {"calc_holiday_gap", "calc_winter_contraction"} <= set(plan["tools"])
    assert plan["source"] == ("llm" if llm in ("fake", "fake_all_tools") else "rule")
    assert set(plan["counter_checks"]) <= set(nodes.COUNTER_CHECKS)
    assert plan["memo_used"] and v["focus"]["memo"] is None  # 메모 원문은 상태에서 지움
    assert levels(v) == before  # 계획이 무엇이든 등급은 같음
    assert set(plan["tools"]) <= set(v["tools_run"])


def test_more_without_focus_keeps_old_behavior(agent):
    st = agent.start("plain", "qa", AS_OF, question=Q, llm="fake")
    agent.approve("plain", "more", 1, idempotency_key="m")
    v = agent.status("plain")["values"]
    assert v["extra_plan"]["source"] == "none" and v["report"]["round_changes"][0].startswith("1회차 대비")
    assert "새 근거 없음" in "\n".join(v["report"]["round_changes"])  # 같은 도구를 다시 돌리면 그대로라고 보여 줌
    assert levels(v) == levels(st["values"])


def test_individual_memo_not_used(agent):
    agent.start("ind", "qa", AS_OF, question=Q, llm="fake")
    agent.approve("ind", "more", 1, idempotency_key="m", focus={"memo": "김철수 할머니 집 주소 확인"})
    v = agent.status("ind")["values"]
    assert not v["extra_plan"]["memo_used"] and "개인 지목" in v["extra_plan"]["reason"]
    assert all(r.get("slot") != "plan_more" for r in v["llm_log"])  # LLM 에 보내지 않음


def test_review_state_update_regenerates_and_blocks_old_approval(agent):
    st = agent.start("rv", "qa", AS_OF, question=Q)
    rev = st["values"]["report_revision"]
    res = agent.review("rv", {"judgements": [{"resource_id": "STUB-R02", "judgement": "보류"}],
                              "checked_evidence_ids": [], "follow_up_on": None, "review_ids": ["rev-a"]}, rev, "rev-a")
    assert res["status"] == "accepted" and res["report_revision"] == rev + 1
    assert agent.review("rv", {"review_ids": ["rev-a"]}, rev + 1, "rev-a")["status"] == "duplicate"
    v = agent.status("rv")["values"]
    assert v["report"]["review_ids"] == ["rev-a"] and not v["report"]["needs_regeneration"]
    assert agent.approve("rv", "approved", rev)["code"] == "STALE_REPORT_REVISION"
    assert agent.approve("rv", "approved", rev + 1)["status"] == "accepted"


def test_tool_timings_and_perf_in_run_meta(agent):
    st = agent.start("perf", "qa", AS_OF, question=Q)
    v = st["values"]
    assert v["tool_timings"] and all({"tool", "ms", "status", "n", "round"} <= set(t) for t in v["tool_timings"])
    perf = v["run_meta"]["perf"]
    assert "get_structure" in perf["tools"] and perf["tools"]["get_structure"]["status"] == {"OK": 1}
    assert "find_resources" in perf["tools"] and perf["nodes"]["collect_signals"]["calls"] == 1


def test_monitoring_checkpoint_saved_once(agent):
    """승인 없는 정기 감시는 끝날 때만 체크포인트 (durability=exit)."""
    agent.start("mon", "monitoring", AS_OF)
    agent.start("qa1", "qa", AS_OF, question=Q)
    con = sqlite3.connect(agent.db_path)
    n = dict(con.execute("SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id").fetchall())
    con.close()
    assert n["mon"] <= 2 < n["qa1"]
    assert agent.status("mon")["values"]["published"]


def test_evidence_stored_once_in_state(agent):
    """근거는 상태 evidence 에만. 카드는 근거 ID, 보고서는 카드 ID 만 담고 펼치면 예전과 같은 모양."""
    from bigcontest.agent.schemas import Report
    from bigcontest.agent.state import card_of, report_of

    st = agent.start("once", "qa", AS_OF, question=Q)
    v = st["values"]
    assert "evidence" not in v["evidence_card"] and v["evidence_card"]["evidence_ids"]
    assert "evidence_card" not in v["report"] and v["report"]["card_id"] == v["evidence_card"]["card_id"]
    card, rep = card_of(v), report_of(v)
    assert [e.evidence_id for e in card.evidence] == v["evidence_card"]["evidence_ids"]
    assert rep.evidence_card == card and Report.model_validate_json(rep.model_dump_json()) == rep
    # 추가 조사 뒤에도 새 판의 카드는 새 근거로 펼쳐짐
    agent.approve("once", "more", v["report_revision"], idempotency_key="m1")
    v2 = agent.status("once")["values"]
    assert report_of(v2).evidence_card.evidence_revision == v2["evidence_revision"] > v["evidence_revision"]


def test_followup_answer_cites_card_only(agent):
    agent.start("fu", "qa", AS_OF, question=Q, llm="fake")
    out = agent.followup("fu", "C-20250926-001 은 왜 이 등급이야?")
    assert out["status"] == "answered" and out["source"] == "llm"
    card = {e["evidence_id"] for e in report_dict(agent.status("fu")["values"])["evidence_card"]["evidence"]}
    assert set(out["cited_evidence_ids"]) <= card and "C-20250926-001" in out["answer"]
    bad = agent.followup("fu", "이 근거 값 알려줘", llm="fake_wrong_fact_once")
    assert bad["status"] == "answered" and bad["source"] in ("llm", "template")
    assert "크게 늘었습니다" not in bad["answer"]  # 방향 바꾼 문장은 빠짐
    assert agent.followup("fu", "101동 302호 어르신은?")["status"] == "abstained"


# ---------------------------------------------------------------- 가짜 API 도 같은 칸


@pytest.fixture
def fake(tmp_path):
    app = create_app(db_path=tmp_path / "f.sqlite", delay_s=0)
    with TestClient(app) as c:
        yield c
    app.state.store.close()


def test_fake_status_has_decision_trace_and_run_resources(fake):
    acc = fake.post("/runs", json={"mode": "qa", "candidate_id": "C-20250926-001", "as_of": AS_OF}).json()
    st = RunStatusResponse.model_validate(fake.get(f"/runs/{acc['run_id']}").json())
    assert [t.node for t in st.decision_trace] == st.path and st.decision_trace[-1].source == "human"
    assert st.published_report_revision == st.report_revision == 1
    res = RunResourcesResponse.model_validate(fake.get(f"/runs/{acc['run_id']}/resources").json())
    assert res.items and all(x.candidate_id == "C-20250926-001" and x.candidate_ids == ["C-20250926-001"]
                             for x in res.items)
    body = {"reviewer": "w", "resource_judgements": [{"resource_id": res.items[0].resource_id, "judgement": "적합"}]}
    assert fake.post(f"/runs/{acc['run_id']}/review", json=body).status_code == 200
    res2 = RunResourcesResponse.model_validate(fake.get(f"/runs/{acc['run_id']}/resources").json())
    assert res2.items[0].judgement == "적합"
    f = FollowupResponse.model_validate(fake.post(f"/runs/{acc['run_id']}/followup",
                                                  json={"question": "왜 이 동이 먼저야?"}).json())
    assert f.status == "answered" and f.source == "template"
    bad = fake.post(f"/runs/{acc['run_id']}/approve", json={"decision": "more", "report_revision": 2, "approver": "a",
                                                           "idempotency_key": "x", "focus": {"candidate_ids": ["C-1"]}})
    assert bad.status_code == 400 and bad.json()["error_code"] == "CANDIDATE_NOT_IN_RUN"


# ---------------------------------------------------------------- 이어 묻기 후보 고르기 (동 이름·집단 말)


def _cand(cid, level, adm=None, first=(), cohort="80대+", group="65세 이상", rank=None):
    kind = "scheduled_access_review" if adm else "unexpected_activity_deviation"
    return Candidate(candidate_id=cid, candidate_type=kind, city="강남", adm_cd=adm, check_first_adm=list(first),
                     is_shared_context=bool(first), analysis_cohort=cohort, service_eligibility_group=group,
                     input_status="충족", counter_result="해당 없음" if adm else "통과", candidate_level=level,
                     queue_rank=rank)


CARD = EvidenceCard(card_id="CARD-1", title="t", snapshot_id="s", analysis_mode="simulated_replay",
                    as_of=date(2025, 9, 26), evidence_revision=1)
PICK = [
    _cand("C-20250926-501", "Level 2", adm="11230510", rank=2),  # 신사동
    _cand("C-20250926-502", "Level 3", adm="11230640", cohort="40대 남성", group="40~64세 남성", rank=1),  # 역삼1동
    _cand("C-20250926-503", "Level 2", adm="11230520", cohort="20대 남성", group="20~34세 남성", rank=3),  # 논현1동
    _cand("C-20250926-504", "Level 1", first=("11230510", "11230630"), cohort="60대 여성"),  # 이탈, 신사동 공유 맥락
]


def ids(cs):
    return [c.candidate_id for c in cs]


def test_pick_candidates_by_dong_name_from_ref_table():
    """카드에 적용 범위가 없어도 ref 표의 동 이름으로 그 동 후보(동 후보 + 공유 맥락)만 고른다."""
    assert ids(pick_candidates("신사동은 왜 Level 2야?", PICK, CARD)) == ["C-20250926-501"]
    assert set(ids(pick_candidates("신사동은 왜 후보야?", PICK, CARD))) == {"C-20250926-501", "C-20250926-504"}
    assert ids(pick_candidates("논현1동은?", PICK, CARD)) == ["C-20250926-503"]


def test_pick_candidates_by_cohort_words_and_fallback():
    assert ids(pick_candidates("중장년 남성은 왜 먼저야?", PICK, CARD)) == ["C-20250926-502"]
    assert ids(pick_candidates("60대 여성 결제는?", PICK, CARD)) == ["C-20250926-504"]
    assert ids(pick_candidates("신사동 80대 어르신은?", PICK, CARD)) == ["C-20250926-501"]
    # 동·집단·등급 말이 없을 때만 전체 (검토 배정 순, 최대 3)
    assert ids(pick_candidates("왜 이렇게 나왔어?", PICK, CARD)) == ["C-20250926-502", "C-20250926-501", "C-20250926-503"]
    # 맞는 후보가 없으면 빈 목록 (다른 후보로 대신 답하지 않음)
    assert ids(pick_candidates("수서동은?", PICK, CARD)) == []
    assert ids(pick_candidates("C-20250926-504 는?", PICK, CARD)) == ["C-20250926-504"]


def test_stub_dong_codes_match_ref_table():
    """가짜 도구의 행정동 코드·이름은 실제 코드표(ref/adm_cd_names.csv, 통계청 8자리)와 같다 (화면이 이름을 바르게 보이게)."""
    from bigcontest.agent.tools import stubs
    from bigcontest.agent.tools.report import _adm_names

    table = _adm_names()
    for city, rows in stubs.ADMS.items():
        for cd, name, *_ in rows:
            assert table.get(cd) == name, (city, cd, name)
    for rows in stubs.RESOURCES.values():
        for r in rows:
            assert r["adm_cd"] is None or r["adm_cd"] in table


def test_run_resources_grouped_one_row_per_resource():
    """GET /runs/{id}/resources: 후보마다 한 줄이던 자원을 자원 하나에 한 줄로 (candidate_ids). 거리는 가까운 값,
    자격은 조심스러운 값, 경고는 합침, 판정은 자원 ID 로."""
    from api.services.agent_runs import group_resources

    base = {"name": "n", "resource_type": "t", "city": "강남", "adm_cd": None, "target_group": None,
            "operating_status": "운영", "phone_based": False, "source_url": "u", "verified_date": "2025-09-01"}
    rows = [
        {**base, "resource_id": "R1", "candidate_id": "C-2", "eligibility": "적합", "distance_km": 2.0, "warnings": ["a"]},
        {**base, "resource_id": "R2", "candidate_id": "C-2", "eligibility": "적합", "distance_km": None, "warnings": []},
        {**base, "resource_id": "R1", "candidate_id": "C-1", "eligibility": "확인 필요", "distance_km": 0.5,
         "warnings": ["a", "b"]},
        {**base, "resource_id": "R1", "candidate_id": "C-1", "eligibility": "적합", "distance_km": 0.7, "warnings": []},
    ]
    out = group_resources(rows, {"R1": ("보류", None)})
    assert [(x.resource_id, x.candidate_ids, x.candidate_id) for x in out] == [
        ("R1", ["C-2", "C-1"], "C-2"), ("R2", ["C-2"], "C-2")]
    r1 = out[0]
    assert (r1.distance_km, r1.eligibility, r1.warnings, r1.judgement) == (0.5, "확인 필요", ["a", "b"], "보류")
    assert out[1].judgement is None
    # 위기 전화는 합친 뒤에도 맨 아래
    crisis = {**base, "resource_id": "R0742", "candidate_id": "C-2", "eligibility": "적합", "distance_km": None,
              "warnings": ["묶음: 위급 시 안내 (자살·정신건강 위기 때 안내, 첫 추천 아님)"]}
    rows2 = [rows[1], crisis, {**rows[0], "candidate_id": "C-1"}]
    assert [x.resource_id for x in group_resources(rows2, {})] == ["R2", "R1", "R0742"]


def test_monitoring_contract_checked_per_city(monkeypatch):
    """두 도시 정기 감시는 도시마다 계약을 검사. 한 도시만 어기면 사유에 그 도시가 붙음."""
    from bigcontest.agent.schemas import ContractIssue, ContractResult
    from bigcontest.agent.state import initial_state

    st = initial_state("cc", "monitoring", AS_OF, tools_backend="real", snapshot_id="snap-x")
    st.update(nodes.intake(st))
    assert st["monitor_cities"] == ["강남", "춘천"]
    same = nodes.check_availability(st)
    assert same["contract_check"]["status"] == "OK" and "abstain_reason" not in same
    real = nodes.check_request_contract

    def fake(req):
        if req.city == "강남":
            return ContractResult(status="ERROR", issues=[ContractIssue(code="CITY_NOT_COVERED", rule="G-01",
                                                                       message="자료 없음")])
        return real(req)

    monkeypatch.setattr(nodes, "check_request_contract", fake)
    out = nodes.check_availability(st)
    assert out["contract_check"]["status"] == "ERROR"
    assert out["abstain_reason"].startswith("CITY_NOT_COVERED: [강남] 자료 없음")
