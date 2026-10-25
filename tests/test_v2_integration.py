"""rules-v2.0 연결 부분: 재판정이 입력 상태를 지키는지, 최종 등급과 대응 종류·근거 신뢰도가 맞는지,
현황 범위가 다른 규칙 값을 섞지 않는지, 규칙 판 표기가 한 값인지. 자료 없이 도는 단위 시험.

칸 축은 가짜 표(test_holiday_watch._cell 과 같은 칸)로 넣는다. 60대 이상 H·S 는 방향 확인 신호라 Level 3 근거로 센다.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from bigcontest.agent import v2_levels as V2
from bigcontest.agent.schemas import Candidate, CandidateCountRange
from bigcontest.rules import candidates as RC
from bigcontest.rules import load_rules

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # api 패키지 (test_api_real 과 같은 방식)
    sys.path.insert(0, str(ROOT))

AS_OF = pd.Timestamp("2025-09-26").date()
TRIG = "R1 추석 연휴 D-7 (10/03~10/09)"
WTRIG = "W 동절기 (춘천 읍·면)"


def _cell(adm, sex, s=True, h=True, pop=500):
    return {"city": "춘천", "adm_cd": adm, "sex": sex, "age5": "60대+", "pop": pop, "s_top": s, "s_eb": 0.3,
            "s_rank": 1, "s_n": 25, "s_small": pop < 100, "h_state": "observed", "h_top": h, "h": 0.9,
            "h_basis": "체류도만", "v_state": "missing", "v_counts": False, "v_eb": None, "v_source": "시험",
            "a_top": False, "a_km": 1.0}


def _cand(adm="32010001", status="충족", missing=(), conf="보통", level="Level 2", rt="담당자 추가 분석",
          trigger=TRIG, cid="C-20250926-001", group="고령층") -> Candidate:
    return Candidate(candidate_id=cid, candidate_type="scheduled_access_review", city="춘천", adm_cd=adm,
                     analysis_cohort="60대 이상", service_eligibility_group=group, trigger=trigger,
                     input_status=status, missing_inputs=list(missing), counter_result="해당 없음",
                     candidate_level=None if status == "보류" else level, rule_id="L2-STRUCT-TRIGGER",
                     rule_version="rules-v1.1", evidence_confidence=conf, response_type=rt)


@pytest.fixture
def axes(monkeypatch):
    """두 동(효자 가짜 32010001·32010002)의 남녀 60대 이상 칸이 모두 H+S, 지난 연휴 카드 신호 있음."""
    df = pd.DataFrame([_cell(a, sx) for a in ("32010001", "32010002") for sx in ("남", "여")])
    box = {"ax": SimpleNamespace(df=df)}
    monkeypatch.setattr(V2, "axes_at", lambda ds, as_of, mode: box["ax"])
    monkeypatch.setattr(V2, "_card_gaps", lambda ev: {("춘천", "남"): -0.2, ("춘천", "여"): -0.2})
    w = pd.DataFrame({"w_ok": [True, True], "w60": [-0.2, -0.2]}, index=pd.Index(["32010001", "32010002"], name="adm_cd"))
    monkeypatch.setattr(V2, "_winter", lambda ds, as_of, mode: w)
    return box


DS = SimpleNamespace(adm_index={"32010001": {"adm_type": "읍"}, "32010002": {"adm_type": "면"}})


def _valid(c: Candidate) -> Candidate:
    """상태 저장 뒤 다시 읽을 때처럼 검증 (model_copy 는 검증을 건너뜀)."""
    return Candidate.model_validate(c.model_dump())


def _relevel(cands, req_group=None):
    return V2.sync_final(V2.relevel_scheduled(cands, [], DS, AS_OF, "simulated_replay", req_group))


# ---------------------------------------------------------------- 1. 보류·부분 판정·낮은 신뢰도 유지


@pytest.mark.parametrize("adm", ["32010001", None])
def test_hold_without_axes_stays_hold(axes, adm):
    """필수 근거가 없어 보류인 후보는 축 자료가 없어도(또는 도시 단위여도) 판단 보류. 검증 오류 없음."""
    axes["ax"] = None
    c = _cand(adm=adm, status="보류", missing=["target_group"])
    out = _relevel([c])
    assert len(out) == 1
    o = _valid(out[0])
    assert o.candidate_level == "판단 보류" and o.rule_id == "HOLD-INPUT" and o.rule_version == "rules-v2.0"
    assert o.input_status == "보류" and o.response_type == "추가 자료 확보"


def test_hold_with_axes_stays_hold_and_makes_no_new_dong(axes):
    out = _relevel([_cand(status="보류", missing=["calendar_trigger"])])
    assert [_valid(o).candidate_level for o in out] == ["판단 보류"]  # 템플릿이 보류면 새 동 후보도 없음


def test_full_inputs_reach_level3(axes):
    out = [_valid(o) for o in _relevel([_cand()])]
    assert [o.candidate_level for o in out] == ["Level 3", "Level 3"]  # 원래 동 + 새로 만든 동
    assert all(o.response_type == "현장 확인" and o.evidence_confidence == "보통" for o in out)
    assert out[1].input_status == "충족"


def test_partial_is_capped_and_low_confidence(axes):
    """부분 판정(겨울 변화 결측)은 Level 3 불가, 근거 신뢰도 낮음. 새로 만든 동 후보도 같은 결측을 물려받음."""
    c = _cand(status="부분 판정", missing=["winter_contraction"], trigger=WTRIG)
    out = [_valid(o) for o in _relevel([c])]
    assert len(out) == 2
    for o in out:
        assert o.candidate_level == "Level 2" and "CAP-PARTIAL" in o.rule_id.split("+")
        assert o.input_status == "부분 판정" and o.missing_inputs == ["winter_contraction"]
        assert o.evidence_confidence == "낮음" and o.response_type == "담당자 추가 분석"


def test_low_confidence_is_carried(axes):
    out = [_valid(o) for o in _relevel([_cand(conf="낮음")])]
    o = out[0]
    assert o.candidate_level == "Level 2" and "CAP-CONF-LOW" in o.rule_id.split("+")
    assert o.evidence_confidence == "낮음" and V2.CONF_CARRIED in o.level_reason


def test_cell_confidence_follows_small_cell(axes):
    """칸 인구가 적으면 그 칸 판정의 신뢰도(낮음)가 후보 근거 신뢰도가 된다."""
    df = axes["ax"].df.copy()
    df["pop"] = 50
    df["s_small"] = True
    axes["ax"] = SimpleNamespace(df=df)
    o = _valid(_relevel([_cand()])[0])
    assert o.candidate_level == "Level 2" and o.evidence_confidence == "낮음"


@pytest.mark.parametrize(("missing", "trigger", "status"), [
    (["structure_vulnerability 또는 access_gap"], TRIG, "충족"),  # 동 단위 결측은 새 동에 넘기지 않음
    (["winter_contraction"], WTRIG, "부분 판정"),
    (["target_group"], TRIG, "보류"),
    (["target_group", "structure_vulnerability 또는 access_gap"], TRIG, "보류"),
])
def test_inherit_inputs(missing, trigger, status):
    t = _cand(status="부분 판정" if status != "보류" else "보류", missing=missing, trigger=trigger)
    got = V2.inherit_inputs(t)
    assert got["input_status"] == status
    assert all("structure_vulnerability" not in m for m in got["missing_inputs"])


# ---------------------------------------------------------------- 2. 최종 등급과 대응 종류


@pytest.mark.parametrize(("level", "rid", "group", "want"), [
    ("Level 3", "V2-L3-HOLIDAY-WATCH", "고령층", "현장 확인"),
    ("Level 1", "V2-L1-NO-CELL", "고령층", "추세 관찰"),
    ("Level 2", "V2-L2-DATA-TRIGGER", "청년 남성", "안내 강화"),
])
def test_response_type_follows_final_level(level, rid, group, want):
    """연휴 주의·재판정으로 등급이 바뀐 뒤 최초 판정의 '담당자 추가 분석'이 남지 않음."""
    c = _cand(level=level, group=group).model_copy(update={"rule_id": rid, "rule_version": "rules-v2.0"})
    o = _valid(V2.sync_final([c])[0])
    assert o.response_type == want == RC.response_type(level, group, load_rules(), rid)


def test_sync_final_caps_level3_set_by_watch():
    """연휴 주의·동절기 단계가 정한 Level 3도 입력 상태 상한을 지킴 (보류 → 판단 보류, 부분 판정 → Level 2)."""
    l3 = {"candidate_level": "Level 3", "rule_id": "V2-L3-HOLIDAY-WATCH", "rule_version": "rules-v2.0"}
    hold = _cand(status="보류", missing=["target_group"]).model_copy(update=l3)
    part = _cand(status="부분 판정", missing=["winter_contraction"]).model_copy(update=l3)
    h, p = (_valid(o) for o in V2.sync_final([hold, part]))
    assert h.candidate_level == "판단 보류" and h.response_type == "추가 자료 확보"
    assert p.candidate_level == "Level 2" and p.rule_id == "V2-L3-HOLIDAY-WATCH+CAP-PARTIAL"
    assert p.evidence_confidence == "낮음" and p.response_type == "담당자 추가 분석"


# ---------------------------------------------------------------- 3. 현황 후보 수 범위


def test_overview_range_is_omitted_under_v2(monkeypatch):
    """rules-v2.0 에서는 v1.1 run_rules 로 센 범위를 섞지 않고 범위를 비움 (지금 수만)."""
    import bigcontest.rules.compare as compare
    from api.services.dashboard_real import RealDashboard

    monkeypatch.delenv("LINKRADAR_RULES", raising=False)
    monkeypatch.setattr(compare, "run_rules", lambda *a, **k: pytest.fail("v1.1 판정을 부름"))
    cands = [_cand(cid=f"C-20250926-00{i}") for i in range(1, 4)]
    got = RealDashboard(runs=None)._count_ranges("run-x", "춘천", cands)
    assert got["scheduled_access_review"].count == 3
    assert all(r.low is None and r.high is None for r in got.values())


def test_count_range_validation():
    assert CandidateCountRange(count=3).low is None
    with pytest.raises(ValueError):
        CandidateCountRange(count=3, low=1)
    with pytest.raises(ValueError):
        CandidateCountRange(count=3, low=4, high=5)


# ---------------------------------------------------------------- 4. 규칙 판 표기


def test_rule_version_label_follows_active_rules(monkeypatch):
    from bigcontest.agent.tools.common import rule_version

    monkeypatch.delenv("LINKRADAR_RULES", raising=False)
    assert "rules-v2.0" in rule_version() and "v1.1" not in rule_version()
    monkeypatch.setenv("LINKRADAR_RULES", "v1.1")
    assert "rules-v1.1" in rule_version()


def test_snapshot_info_uses_same_rule_version(monkeypatch):
    from api.services.snapshots import AgentSnapshotService

    monkeypatch.delenv("LINKRADAR_RULES", raising=False)
    svc = AgentSnapshotService.__new__(AgentSnapshotService)
    svc._known = {}
    assert "rules-v2.0" in svc._info("dev-0123456789ab").rule_version
