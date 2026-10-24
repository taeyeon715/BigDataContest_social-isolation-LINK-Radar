"""근거 ID 는 실제 계산을 가리킨다 (외부 점검 K-01).

- 자원 찾기 봉투 ID 에 필요 유형·원인 유형·집단·limit·스냅샷의 해시를 붙임: 같은 계산 → 같은 ID, 다른 계산 → 다른 ID
- 실제 도구 춘천 09/26 감시: 조사 루프의 의료 접근 인용(신동면·동산면·동면)은 의료 접근 5건 봉투를 열고,
  기본 추천 봉투(상위 5 + 위급 시 안내 2 = 7건)는 그대로 남음
- 같은 ID 에 다른 내용이 들어오면 조용히 버리지 않고 오류 (조사 루프 errors, 카드 만들기·상태 복원은 예외)
"""

from __future__ import annotations

from datetime import date

import pytest

from bigcontest.agent import nodes
from bigcontest.agent.graph import build_graph
from bigcontest.agent.schemas import NormalizedRequest
from bigcontest.agent.state import EvidenceIdConflict, card_dict, initial_state, report_of
from bigcontest.agent.tools import stubs
from bigcontest.agent.tools.common import evidence_id, params_hash
from bigcontest.agent.tools.datasource import dev_available
from bigcontest.agent.tools.stubs import ToolContext

SINDONG, DONGSAN, DONGMYEON = "32010330", "32010320", "32010310"  # 신동면·동산면·동면


def _ctx(snapshot="snap-a"):
    return ToolContext(scenario="normal", as_of=date(2025, 9, 26), analysis_mode="simulated_replay", snapshot_id=snapshot,
                       request=NormalizedRequest(city="춘천", metric="카드결제", period_to=date(2025, 9, 26)))


def test_id_encodes_computation_args():
    p = {"tool": "find_resources", "group": "고령층", "need_type": None, "cause_type": "연휴형", "limit": 5}
    a = evidence_id(_ctx(), f"RES-{SINDONG}", id_params=p)
    assert a == evidence_id(_ctx(), f"RES-{SINDONG}", id_params=dict(p))  # 같은 계산 → 같은 ID
    assert a.startswith(f"EV-20250926-CC-RES-{SINDONG}-") and len(a.rsplit("-", 1)[1]) == 8
    for change in ({"need_type": "의료 접근"}, {"cause_type": None}, {"group": "중장년 남성"}, {"limit": 3}):
        assert evidence_id(_ctx(), f"RES-{SINDONG}", id_params={**p, **change}) != a, change
    assert evidence_id(_ctx("snap-b"), f"RES-{SINDONG}", id_params=p) != a  # 스냅샷이 다르면 다른 ID
    assert params_hash("s", {"a": 1, "b": 2}) == params_hash("s", {"b": 2, "a": 1})
    # 인자가 없는 도구는 예전 모양 그대로
    assert evidence_id(_ctx(), "STR-32010350") == "EV-20250926-CC-STR-32010350"


# ---------------------------------------------------------------- 충돌 검출


def _env(eid, value):
    return stubs._ev(_ctx(), eid.removeprefix("EV-20250926-"), metric="복지 자원 후보", value=value, unit="건")


def test_id_conflicts_detects_same_id_different_content():
    a, b, c = _env("EV-20250926-RES-X", 7), _env("EV-20250926-RES-X", 5), _env("EV-20250926-RES-Y", 5)
    assert nodes.id_conflicts([a], [b, c]) == ["EV-20250926-RES-X"]
    assert nodes.id_conflicts([a, c], [a.model_copy()]) == []  # 같은 내용은 충돌 아님


def test_card_restore_raises_on_conflict():
    """상태 복원(card_dict): 같은 ID 에 다른 내용이면 기본 근거를 조용히 고르지 않고 오류."""
    a, b = _env("EV-20250926-RES-X", 7), _env("EV-20250926-RES-X", 5)
    state = {"evidence": [a.model_dump(mode="json")], "investigation_evidence": [b.model_dump(mode="json")],
             "evidence_card": {"card_id": "c", "evidence_ids": ["EV-20250926-RES-X"]}}
    with pytest.raises(EvidenceIdConflict):
        card_dict(state)
    state["investigation_evidence"] = [a.model_dump(mode="json")]
    assert card_dict(state)["evidence"][0]["value"] == 7


def test_investigation_conflict_is_recorded_and_card_refuses(monkeypatch):
    """조사 루프가 등급 근거와 같은 ID 로 다른 값을 내면: errors 에 EVIDENCE_ID_CONFLICT, 카드는 만들지 않음 (예외)."""
    orig = nodes.call_tool
    phase = {"inv": False}

    def call(tool, ctx):
        out = orig(tool, ctx)
        if phase["inv"] and tool == "calc_holiday_gap":
            return [e.model_copy(update={"value": 0.123}) for e in out]
        return out

    orig_inv = nodes.investigate

    def inv(state):
        phase["inv"] = True
        try:
            return orig_inv(state)
        finally:
            phase["inv"] = False

    monkeypatch.setattr(nodes, "call_tool", call)
    monkeypatch.setattr(nodes, "investigate", inv)
    # 기본 수집에서도 calc_holiday_gap 을 부르는 LLM 변형 (같은 ID 가 등급 근거에 이미 있음)
    seen = {}
    orig_check = nodes.check_findings

    def spy(state):
        out = orig_check(state)
        seen["errors"] = list(state.get("errors") or [])
        return out

    monkeypatch.setattr(nodes, "check_findings", spy)
    with pytest.raises(EvidenceIdConflict):
        build_graph().invoke(initial_state("k01c", "monitoring", "2025-09-26", tools_backend="stub",
                                           llm="fake_all_tools"), {"recursion_limit": 100})
    assert any(e["error_code"].startswith(nodes.EVIDENCE_ID_CONFLICT) for e in seen["errors"])


# ---------------------------------------------------------------- 실제 도구: 춘천 09/26 (점검 재현)


@pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")
def test_chuncheon_0926_medical_citation_opens_medical_envelope():
    s = build_graph().invoke(initial_state("k01", "monitoring", "2025-09-26", tools_backend="real", llm="fake",
                                           city="춘천"), {"recursion_limit": 100})
    assert s["path"][-1] == "publish_candidates" and s["verdict"] == "PASS"
    assert not [e for e in s["errors"] if e["error_code"].startswith(nodes.EVIDENCE_ID_CONFLICT)]
    base = {e["evidence_id"]: e for e in s["evidence"]}
    inv = {e["evidence_id"]: e for e in s["investigation_evidence"]}
    # 같은 ID 로 다른 내용이 없음 (점검에서는 세 동 모두 충돌)
    assert nodes.id_conflicts(s["evidence"], s["investigation_evidence"]) == []
    rep = report_of(s)
    card = {e.evidence_id: e for e in rep.evidence_card.evidence}
    medical = [i for i in rep.investigation.items if i.qid == "MEDICAL_ACCESS"]
    assert {i.adm_cd for i in medical} == {"32010350", "32010360", "32010390"}  # 보강 후 후보에서 추가 조사
    for item in medical:
        assert item.status == "answered" and "의료 접근 자원 후보 5건" in item.finding
        (eid,) = item.evidence_ids
        ev = card[eid]  # 보고서 인용 → 카드의 바로 그 봉투
        assert eid in inv and eid not in base
        assert ev.value == 5 and "필요 유형 의료 접근" in ev.warnings[0]
        assert "위급 시 안내 0건" in ev.denominator
        # 기본 추천 봉투는 Level 2 이상 후보에만 (rules-v2.0: 이 세 면은 통신 신호가 없어 Level 1 → 기본 추천 없음)
        same_dong = [e for e in s["evidence"] if e["metric"] == "복지 자원 후보" and f"({item.adm_cd})" in e["applied_scope"]]
        level = next(c["candidate_level"] for c in s["candidates"] if c.get("adm_cd") == item.adm_cd)
        if level in ("Level 2", "Level 3"):
            assert len(same_dong) == 1 and same_dong[0]["value"] == 5 and same_dong[0]["evidence_id"] != eid
        else:
            assert same_dong == []
