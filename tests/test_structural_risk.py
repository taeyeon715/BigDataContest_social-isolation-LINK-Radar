"""후보 카드의 칸 근거 축 (src/bigcontest/agent/tools/structural_risk.py, rules-v2.0 기준일 계산)."""

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from bigcontest.agent.schemas import Candidate, NormalizedRequest
from bigcontest.agent.tools import cell_axes as CA
from bigcontest.agent.tools import structural_risk as sr
from bigcontest.agent.tools.datasource import SnapshotMismatch, dev_available


def candidate(adm="32010350", rule_id="L2-STRUCT-TRIGGER"):
    return Candidate(candidate_id="C-T3", candidate_type="scheduled_access_review", city="춘천", adm_cd=adm,
                     analysis_cohort="60대+", service_eligibility_group="65세 이상", input_status="충족",
                     counter_result="해당 없음", candidate_level="Level 2", queue_rank=1, rule_id=rule_id)


def ctx(mode="simulated_replay"):
    return SimpleNamespace(backend="real", snapshot_id="snap-fixed", analysis_mode=mode,
                           request=NormalizedRequest(city="춘천", metric="카드결제", group="고령층"), as_of=date(2025, 9, 26))


def row(sex, s_top=False, h_top=False, pop=500):
    return {"city": "춘천", "adm_cd": "32010350", "sex": sex, "age5": "60대+", "pop": pop, "s_eb": 0.4, "s_top": s_top,
            "s_rank": 3, "s_n": 25, "s_small": pop < 100, "h": 0.8, "h_top": h_top, "h_state": "observed",
            "h_basis": "체류도만", "v_eb": 0.1, "v_top": False, "v_counts": False, "v_state": "observed",
            "v_source": "시험", "a_km": 1.2, "a_top": False}


def axes(rows):
    return CA.AxesAt(pd.DataFrame(rows), 8, (7, 8), date(2025, 7, 1), date(2025, 8, 31), date(2025, 9, 15))


def test_cells_are_v2_axes_and_candidate_untouched(monkeypatch):
    monkeypatch.setattr(sr, "open_datasource", lambda sid: object())
    monkeypatch.setattr(CA, "axes_at", lambda ds, as_of, mode: axes([row("남", s_top=True, h_top=True), row("여", pop=50)]))
    c = candidate()
    before = c.model_dump()
    ref = sr.get_structural_risk(ctx(), [c])[0]
    assert ref.status == "available" and len(ref.cells) == 2
    assert ref.cells[0].axes_on == ["H", "S"] and ref.cells[0].cell_type == "이중 위험"
    assert ref.cells[1].axes_on == [] and ref.cells[1].small
    assert "주민등록 8월, 통신 7·8월" in ref.period_label and "60~64세" in ref.mapping_note
    assert "참고" in ref.comparison_note  # v1.1 후보 → 축은 참고
    assert c.model_dump() == before
    v2 = sr.get_structural_risk(ctx(), [candidate(rule_id="V2-L3-HOLIDAY-WATCH")])[0]
    assert v2.comparison_note.startswith("이 칸 근거 축으로")


def test_no_published_months_and_city_candidates_show_nothing(monkeypatch):
    monkeypatch.setattr(sr, "open_datasource", lambda sid: object())
    monkeypatch.setattr(CA, "axes_at", lambda ds, as_of, mode: None)  # strict_as_of 등
    assert sr.get_structural_risk(ctx("strict_as_of"), [candidate()])[0].cells == []
    assert sr.get_structural_risk(ctx(), [candidate(None)])[0].status == "not_applicable"


def test_missing_or_duplicate_cells_are_not_fabricated(monkeypatch):
    monkeypatch.setattr(sr, "open_datasource", lambda sid: object())
    for rows in ([row("남")], [row("남"), row("여"), row("여")]):
        monkeypatch.setattr(CA, "axes_at", lambda ds, as_of, mode, rows=rows: axes(rows))
        assert sr.get_structural_risk(ctx(), [candidate()])[0].cells == []


def test_snapshot_mismatch_is_not_swallowed(monkeypatch):
    def mismatch(_):
        raise SnapshotMismatch("changed")
    monkeypatch.setattr(sr, "open_datasource", mismatch)
    with pytest.raises(SnapshotMismatch):
        sr.get_structural_risk(ctx(), [candidate()])


@pytest.mark.skipif(not dev_available(), reason="local data unavailable")
def test_real_card_axes_do_not_change_decisions(tmp_path, monkeypatch):
    from bigcontest.agent.graph import Agent
    from bigcontest.agent.state import card_of
    agent = Agent(tmp_path / "runs.db", tools_backend="real")
    try:
        q = "춘천 남면 어르신 어디부터 확인해야 해?"
        with monkeypatch.context() as patch:
            patch.setattr(sr, "get_structural_risk", lambda ctx, candidates: [])
            base = agent.start("t3-base", "qa", "2025-09-26", question=q, llm="none")["values"]
        result = agent.start("t3-with", "qa", "2025-09-26", question=q, llm="none")["values"]
        fields = ["adm_cd", "candidate_level", "queue_rank", "selected_for_review", "counter_result", "input_status"]
        assert [[c.get(f) for f in fields] for c in base["candidates"]] == [[c.get(f) for f in fields] for c in result["candidates"]]
        assert base["report"]["fact_sentences"] == result["report"]["fact_sentences"]
        card = card_of(result)
        ref = next(r for r in card.structural_risk if r.adm_cd == "32010350")
        assert ref.status == "available" and len(ref.cells) == 2
        assert "통신" in ref.period_label
    finally:
        agent.close()
