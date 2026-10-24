from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from bigcontest.agent.schemas import Candidate, NormalizedRequest
from bigcontest.agent.tools import structural_risk as sr
from bigcontest.agent.tools.datasource import DataUnavailable, SnapshotMismatch, dev_available


def candidate(adm="32010350"):
    return Candidate(candidate_id="C-T3", candidate_type="scheduled_access_review", city="춘천", adm_cd=adm,
                     analysis_cohort="60대+", service_eligibility_group="65세 이상", input_status="충족",
                     counter_result="해당 없음", candidate_level="Level 2", queue_rank=1)


def ctx(mode="simulated_replay"):
    return SimpleNamespace(backend="real", snapshot_id="snap-fixed", analysis_mode=mode,
                           request=NormalizedRequest(city="춘천", metric="카드결제", group="고령층"), as_of=date(2025, 9, 26))


def frame():
    return pd.DataFrame([{"지역": "강원 춘천시", "SGIS코드": "32010350", "읍면동명": "남면", "성별": sex,
                          "연령5": "60대+", "적용 H": .2, "V": .8, "위험 유형": "취약형", "단계": stage}
                         for sex, stage in [("남성", "경계"), ("여성", "주의")]])


def test_reference_retains_cells_and_leaves_candidate_untouched(monkeypatch):
    ds = SimpleNamespace(structural_risk=frame, hashes={"structural_risk": "a" * 64})
    monkeypatch.setattr(sr, "open_datasource", lambda sid: ds)
    c = candidate(); before = c.model_dump()
    ref = sr.get_structural_risk(ctx(), [c])[0]
    assert ref.status == "available" and len(ref.cells) == 2
    assert [r.stage for r in ref.cells] == ["경계", "주의"]
    assert ref.source_hash == "a" * 64 and "사후 계산" in ref.period_label
    assert "60~64세" in ref.mapping_note and "Level 2" in ref.comparison_note
    assert c.model_dump() == before


def test_strict_and_city_reference_do_not_read_future_dong_data(monkeypatch):
    monkeypatch.setattr(sr, "open_datasource", lambda _: pytest.fail("must not read reference"))
    assert sr.get_structural_risk(ctx("strict_as_of"), [candidate()])[0].cells == []
    assert sr.get_structural_risk(ctx(), [candidate(None)])[0].status == "not_applicable"


def test_missing_duplicate_and_invalid_reference_are_not_fabricated(monkeypatch):
    def missing():
        raise DataUnavailable("no optional source")
    ds = SimpleNamespace(structural_risk=missing, hashes={})
    monkeypatch.setattr(sr, "open_datasource", lambda _: ds)
    assert sr.get_structural_risk(ctx(), [candidate()])[0].status == "unavailable"
    for data in [frame().iloc[:1], pd.concat([frame(), frame()]), frame().assign(V=1.5)]:
        ds.structural_risk = lambda data=data: data
        assert sr.get_structural_risk(ctx(), [candidate()])[0].cells == []


def test_reference_snapshot_mismatch_is_not_swallowed(monkeypatch):
    def mismatch(_):
        raise SnapshotMismatch("changed")
    monkeypatch.setattr(sr, "open_datasource", mismatch)
    with pytest.raises(SnapshotMismatch):
        sr.get_structural_risk(ctx(), [candidate()])


@pytest.mark.skipif(not dev_available(), reason="local data unavailable")
def test_real_card_survives_checkpoint_and_reference_does_not_change_decisions(tmp_path, monkeypatch):
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
        assert card.structural_risk and card.structural_risk[0].status == "available"
        assert len(card.structural_risk[0].cells) == 2
        assert all("구조 위험 단계" not in f.metric for f in card.facts)
    finally:
        agent.close()
