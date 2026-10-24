"""rules-v2.0 등급 판정 (src/bigcontest/rules/levels_v2.py): 제공 데이터 신호(H·W·C)로 등급, 외부(S·V·A)는 확인."""

from bigcontest.rules.levels_v2 import CellFacts, cell_type, decide_v2, load_rules_v2, split

SCH = "scheduled_access_review"
DEV = "unexpected_activity_deviation"


def f(**kw):
    return CellFacts(**{"candidate_type": SCH, "trigger": "R1", **kw})


def test_rules_file_loads():
    r = load_rules_v2()
    assert r["version"] == "rules-v2.0"
    assert r["levels"]["level3_min_data"] == 2 and r["levels"]["level3_min_external"] == 1
    assert set(r["levels"]["data_axes"]) == {"H", "W", "C"} and set(r["levels"]["external_axes"]) == {"S", "V", "A"}


def test_split_data_and_external():
    assert split(["H", "C", "S", "A"]) == (["H", "C"], ["S", "A"])


def test_two_data_signals_and_one_external_give_level3():
    d = decide_v2(f(axes={"H": True, "C": True, "S": True}))
    assert d.level == "Level 3" and d.rule_id == "V2-L3-DATA2-EXT1"
    assert decide_v2(f(axes={"H": True, "W": True, "V": True})).level == "Level 3"


def test_external_alone_never_raises():
    # 외부 축 셋이 모두 서도 신한·SK 신호가 없으면 Level 1
    d = decide_v2(f(axes={"S": True, "V": True, "A": True}))
    assert d.level == "Level 1" and "외부 확인만" in d.reason


def test_one_data_signal_or_no_external_is_level2():
    d = decide_v2(f(axes={"H": True, "S": True, "V": True}))
    assert d.level == "Level 2" and "CAP-DATA-ONE" in d.caps
    d = decide_v2(f(axes={"H": True, "C": True}))
    assert d.level == "Level 2" and "CAP-NO-EXTERNAL" in d.caps


def test_low_confidence_or_partial_caps_level3():
    full = {"H": True, "C": True, "S": True}
    assert "CAP-CONF-LOW" in decide_v2(f(axes=full, confidence="낮음")).caps
    assert "CAP-PARTIAL" in decide_v2(f(axes=full, input_status="부분 판정", missing_inputs=["x"])).caps


def test_hold_and_counter_order_same_as_v11():
    assert decide_v2(f(input_status="보류", missing_inputs=["x"], axes={"H": True, "C": True})).level == "판단 보류"
    assert decide_v2(CellFacts(DEV, counter_result="보류", axes={"H": True})).level == "판단 보류"
    assert decide_v2(CellFacts(DEV, counter_result="기각", axes={"H": True})).level == "Level 1"


def test_deviation_capped_at_level2_needs_telecom_signal():
    md = load_rules_v2()["deviation"]["min_drop"]
    d = decide_v2(CellFacts(DEV, counter_result="통과", deviation_drop=md + 0.01, axes={"H": True, "S": True}))
    assert d.level == "Level 2" and "CAP-SHARED-CONTEXT" in d.caps
    assert decide_v2(CellFacts(DEV, counter_result="통과", deviation_drop=md - 0.01, axes={"H": True})).level == "Level 1"
    # 외부 축만 선 칸이면 Level 1
    assert decide_v2(CellFacts(DEV, counter_result="통과", deviation_drop=md + 0.01, axes={"S": True})).level == "Level 1"


def test_types():
    assert cell_type(["H", "S"]) == "이중 위험"
    assert cell_type(["H"]) == "관계 단절형"
    assert cell_type(["V"]) == "구조·경제 취약형"
    assert cell_type(["A"]) == "해당 없음"


def test_card_alone_does_not_point_to_a_dong():
    # 카드(C)는 도시 단위: 외부 확인이 있어도 통신 신호가 없으면 Level 1
    d = decide_v2(f(axes={"C": True, "S": True, "V": True}))
    assert d.level == "Level 1" and "카드 신호만" in d.reason
    assert decide_v2(f(axes={"C": True, "W": True, "S": True})).level == "Level 3"
