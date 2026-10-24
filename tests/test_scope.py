"""후보로 연 조사는 그 후보만: 등급은 정기 감시와 같고, 카드 제목·근거·자원은 그 후보 것.

- 등급·순서는 도시 전체 후보·근거로 정한 뒤 거르므로 정기 감시 실행의 같은 후보와 같아야 한다 (결정론)
- 카드에는 그 후보 근거 + 그 동 근거 + 도시 공통 근거만. 다른 동 근거는 상태에만 남음
- 실제 도구(dev 스냅샷)가 없으면 실제 도구 시험은 건너뜀
"""

from __future__ import annotations

import pytest

from bigcontest.agent import nodes
from bigcontest.agent.graph import Agent
from bigcontest.agent.state import card_of
from bigcontest.agent.tools.datasource import dev_available

KEYS = ("candidate_type", "city", "adm_cd", "analysis_cohort", "candidate_level", "level_reason", "rule_id",
        "counter_result", "queue_rank", "selected_for_review", "evidence_ids")


def _same_as_monitoring(agent: Agent, as_of: str, city: str, prefix: str, limit: int = 4) -> list[str]:
    mon = agent.start(f"{prefix}-mon", "monitoring", as_of, city=city)["values"]
    cands = [c for c in mon["candidates"] if c["city"] == city]
    assert cands
    titles = []
    for i, c in enumerate(cands[:limit]):
        rid = f"{prefix}-{i}"
        v = agent.start(rid, "qa", as_of, question=f"{city} 어르신 어디부터 확인해야 해?",
                        scope_candidate_ids=[c["candidate_id"]])["values"]
        assert [x["candidate_id"] for x in v["candidates"]] == [c["candidate_id"]]
        got = v["candidates"][0]
        assert {k: got.get(k) for k in KEYS} == {k: c.get(k) for k in KEYS}
        card = card_of(v)
        assert card.title.startswith(f"후보 {c['candidate_id']} ·") and "건" not in card.title
        ids = {e.evidence_id for e in card.evidence}
        assert set(c["evidence_ids"]) <= ids  # 그 후보 근거는 모두 카드에
        # 카드 근거는 상태에 있음 (등급 입력 근거 + 조사 루프 근거 "추가로 확인한 것")
        assert ids <= {e["evidence_id"] for e in v["evidence"] + v["investigation_evidence"]}
        assert {r["candidate_id"] for r in v["resources"]} <= {c["candidate_id"]}
        titles.append(card.title)
    return titles


def test_scope_stub_levels_same_as_monitoring(tmp_path):
    a = Agent(tmp_path / "s.db", tools_backend="stub")
    try:
        _same_as_monitoring(a, "2025-10-20", "춘천", "stub")
    finally:
        a.close()


def test_scope_missing_candidate_keeps_all(tmp_path):
    """없는 후보 ID 면 거르지 않고 오류 기록만 (실행이 비지 않게)."""
    a = Agent(tmp_path / "m.db", tools_backend="stub")
    try:
        v = a.start("miss", "qa", "2025-10-20", question="춘천 어르신 어디부터 확인해야 해?",
                    scope_candidate_ids=["C-없음"])["values"]
        assert v["candidates"] and any(e.get("error_code") == "SCOPE_CANDIDATE_MISSING" for e in v["errors"])
        assert card_of(v).title.startswith("후보 ") and "건" in card_of(v).title
    finally:
        a.close()


def test_scoped_evidence_keeps_shared_context():
    from bigcontest.agent.schemas import Candidate, ToolResult

    def ev(i, scope, cohort=None):
        return ToolResult.model_validate({
            "evidence_id": i, "metric": "m", "value": 1, "unit": "u", "measured_grain": "g", "applied_scope": scope,
            "analysis_cohort": cohort, "analysis_mode": "simulated_replay", "as_of": "2025-10-20",
            "snapshot_id": "s", "status": "OK"})

    c = Candidate.model_validate({"candidate_id": "C-1", "candidate_type": "scheduled_access_review", "city": "춘천",
                                  "adm_cd": "51110330", "analysis_cohort": "60대+", "input_status": "충족",
                                  "counter_result": "해당 없음", "evidence_ids": ["EV-T"]})
    items = [ev("EV-T", "춘천"), ev("EV-A", "춘천 남면 (51110330)"), ev("EV-B", "춘천 북산면 (51110350)"),
             ev("EV-C", "춘천"), ev("EV-D", "춘천", "20대 여성")]
    got = [e.evidence_id for e in nodes.scoped_evidence(items, [c])]
    assert got == ["EV-T", "EV-A", "EV-C"]  # 후보 근거·그 동 근거·도시 공통은 남고, 다른 동·다른 코호트는 뺌


@pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")
@pytest.mark.parametrize("as_of,city", [("2025-12-20", "춘천"), ("2025-10-13", "강남")])
def test_scope_real_levels_same_as_monitoring(tmp_path, as_of, city):
    a = Agent(tmp_path / "r.db", tools_backend="real")
    try:
        _same_as_monitoring(a, as_of, city, "real-" + as_of.replace("-", ""), limit=3)
    finally:
        a.close()


# ---------------------------------------------------------------- 추가 조사 (후보로 연 조사)


def test_cohort_fits():
    f = nodes.cohort_fits
    assert f(None, "60대·70대·80대+") and f("60대·70대·80대+", "60대·70대·80대+")
    assert f("70대 여성", "60대·70대·80대+") and f("80대+ 남성", "60대·70대·80대+") and f("80대+ 여성", "60+")
    assert not f("20대 남성", "60대·70대·80대+") and not f("50대 여성", "60대+")
    assert not f("60대 여성", "60대 남성") and not f("60대", "60대 남성")  # 성별이 다르거나 더 넓으면 아님


def _ev(i, scope, cohort=None, metric="m"):
    from bigcontest.agent.schemas import ToolResult

    return ToolResult.model_validate({
        "evidence_id": i, "metric": metric, "value": 1, "unit": "u", "measured_grain": "g", "applied_scope": scope,
        "analysis_cohort": cohort, "analysis_mode": "simulated_replay", "as_of": "2025-09-26",
        "snapshot_id": "s", "status": "OK"})


def test_scoped_evidence_keeps_requested_sub_cohorts():
    from bigcontest.agent.schemas import Candidate

    c = Candidate.model_validate({"candidate_id": "C-1", "candidate_type": "scheduled_access_review", "city": "춘천",
                                  "adm_cd": "32010350", "analysis_cohort": "60대·70대·80대+", "input_status": "충족",
                                  "counter_result": "해당 없음", "evidence_ids": ["EV-T"]})
    items = [_ev("EV-T", "춘천"), _ev("EV-H1", "춘천 (동 목록은 공유 맥락)", "70대 여성"),
             _ev("EV-H2", "춘천 (동 목록은 공유 맥락)", "20대 남성"), _ev("EV-H3", "춘천 북산면 (32010360)", "70대 여성"),
             _ev("EV-X", "춘천 (동 목록은 공유 맥락)", "70대 여성")]
    got = [e.evidence_id for e in nodes.scoped_evidence(items, [c], ["EV-H1", "EV-H2", "EV-H3"])]
    # 요청한 도구의 근거 중 하위 코호트(70대 여성)만. 다른 코호트·다른 동·요청 안 한 근거는 뺌
    assert got == ["EV-T", "EV-H1"]


def _pick_heat(self, request, available, required):
    self.last_reason = "시험: 폭염 도구"
    return [t for t in available if t == "calc_heat_response"]


@pytest.fixture
def stub_extra(monkeypatch):
    """1회차: LLM 이 폭염 도구를 고름. 추가 조사: 연휴 도구가 하위 코호트 근거를 냄 (가짜 도구를 바꿔 끼움)."""
    from bigcontest.agent import llm as L
    from bigcontest.agent.tools import stubs

    def heat(ctx):
        return [stubs._ev(ctx, "HEAT", metric="폭염 반응 (탐색)", value=0.9, applied_scope=ctx.city)]

    def hol(ctx):
        return [stubs._ev(ctx, f"HOL-{k}", metric="연휴 중 결제 평소 대비", value=0.8, analysis_cohort=coh,
                          applied_scope=f"{ctx.city} (동 목록은 공유 맥락)")
                for k, coh in (("80F", "80대+ 여성"), ("80M", "80대+ 남성"), ("20M", "20대 남성"))]

    monkeypatch.setitem(stubs._TOOLS, "calc_heat_response", heat)
    monkeypatch.setitem(stubs._TOOLS, "calc_holiday_gap", hol)
    monkeypatch.setattr(L.FakeLLM, "choose_extra_tools", _pick_heat)


def _scoped_more(agent: Agent, as_of: str, city: str, pick, memo: str):
    mon = agent.start("mon", "monitoring", as_of, city=city)["values"]
    c = pick([x for x in mon["candidates"] if x["city"] == city])
    st = agent.start("q", "qa", as_of, question=f"{city} 어르신 어디부터 확인해야 해?", llm="fake",
                     scope_candidate_ids=[c["candidate_id"]])["values"]
    card1 = card_of(st)
    agent.approve("q", "more", st["report_revision"], idempotency_key="m",
                  focus={"candidate_ids": [c["candidate_id"]], "memo": memo})
    v = agent.status("q")["values"]
    return c, st, card1, v, card_of(v)


def _check_round(st, card1, v, card2) -> tuple[set[str], str]:
    from bigcontest.agent.state import report_of

    assert [x["candidate_level"] for x in v["candidates"]] == [x["candidate_level"] for x in st["candidates"]]
    # 등급 입력 근거만 비교 (조사 루프가 1회차에 이미 보여 준 근거는 "추가로 확인한 것" 칸 몫, round_changes 도 같은 기준)
    main1, main2 = {e["evidence_id"] for e in st["evidence"]}, {e["evidence_id"] for e in v["evidence"]}
    ids1 = {e.evidence_id for e in card1.evidence} & main1
    ids2 = {e.evidence_id for e in card2.evidence} & main2
    assert ids1 <= ids2  # 1회차 근거가 말없이 빠지지 않음
    new = ids2 - ids1
    assert new and new <= set(v["requested_evidence_ids"])
    changes = "\n".join(report_of(v).round_changes)
    assert f"새 근거 {len(new)}건 (근거 표에 넣음)" in changes  # 보고서 숫자 = 카드에 새로 보이는 근거 수
    assert "빠진 근거" not in changes
    assert v["guard"][-1]["verdict"] == "PASS"
    return new, changes


def test_scoped_more_shows_requested_evidence_stub(tmp_path, stub_extra):
    a = Agent(tmp_path / "x.db", tools_backend="stub")
    try:
        c, st, card1, v, card2 = _scoped_more(a, "2025-10-20", "춘천", lambda cs: cs[0], "연휴 앞뒤 결제 차이도 봐줘")
        assert "폭염 반응 (탐색)" in {e.metric for e in card1.evidence}
        new, changes = _check_round(st, card1, v, card2)
        expect = {e["evidence_id"] for e in v["evidence"] if e["metric"] == "연휴 중 결제 평소 대비"
                  and nodes.cohort_fits(e["analysis_cohort"], c["analysis_cohort"])}
        assert new == expect and expect
        assert "근거 표에 넣지 않은 새 근거" in changes  # 20대 남성은 상태에만
        assert "calc_heat_response" in v["tools_run"]  # 1회차 추가 도구도 다시 돌림
    finally:
        a.close()


def test_round_changes_lists_removed_evidence_with_reason():
    keys = {"폭염|춘천|None|x": [1, "OK"], "구조|춘천|None|x": [1, "OK"]}
    prev = {"round": 1, "evidence": keys, "state_evidence": keys, "levels": {}, "tools": []}
    row = {"applied_scope": "춘천", "observed_to": "x", "value": 1, "status": "OK"}
    state = {"prev_round": prev, "extra_rounds": 1, "candidates": [], "evidence_card": {"evidence_ids": ["E2"]},
             "evidence": [{"evidence_id": "E1", "metric": "폭염", **row}, {"evidence_id": "E2", "metric": "새것", **row}]}
    out = "\n".join(nodes.round_changes(state))
    assert "새 근거 1건 (근거 표에 넣음): 새것" in out
    assert "빠진 근거 2건" in out and "폭염 (근거 표 범위 밖, 상태에는 있음)" in out
    assert "구조 (이번 회차 도구 결과에 없음)" in out


@pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")
def test_scoped_more_shows_requested_evidence_real(tmp_path, monkeypatch):
    """시연과 같은 설정: 춘천 2025-09-26 남면 후보, 1회차 폭염 도구, 추가 조사 메모로 연휴 전후 차이."""
    from bigcontest.agent import llm as L

    monkeypatch.setattr(L.FakeLLM, "choose_extra_tools", _pick_heat)
    a = Agent(tmp_path / "r.db", tools_backend="real")
    try:
        c, st, card1, v, card2 = _scoped_more(
            a, "2025-09-26", "춘천", lambda cs: next(x for x in cs if x["adm_cd"] == "32010640"),
            "연휴 앞뒤 결제 차이를 더 봐줘")
        assert c["candidate_level"] == "Level 3"  # 효자2동 (연휴 주의 목록)
        assert any(e.metric.startswith("폭염일") for e in card2.evidence)
        new, _ = _check_round(st, card1, v, card2)
        assert len(new) == 12  # 연휴 중·뒤 × 60·70·80대+ × 남녀
        assert all("{" not in s for e in card2.evidence for s in e.valid_claims)  # 빈칸 없음
    finally:
        a.close()
