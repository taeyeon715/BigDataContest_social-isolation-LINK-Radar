"""rules-v2.0 등급 판정 (src/bigcontest/rules/levels_v2.py)."""

from bigcontest.rules.levels_v2 import CellFacts, cell_type, decide_v2, load_rules_v2

SCH = "scheduled_access_review"
DEV = "unexpected_activity_deviation"


def f(**kw):
    return CellFacts(**{"candidate_type": SCH, "trigger": "R1", **kw})


def test_rules_file_loads():
    r = load_rules_v2()
    assert r["version"] == "rules-v2.0"
    assert r["levels"]["level3_min_axes"] == 2


def test_two_axes_give_level3_including_urban():
    # 도시 지역(거점 가까움)도 S + H 로 Level 3
    assert decide_v2(f(axes={"S": True, "H": True, "A": False})).level == "Level 3"
    assert decide_v2(f(axes={"H": True, "A": True})).level == "Level 3"


def test_s_v_only_is_level2():
    d = decide_v2(f(axes={"S": True, "V": True}))
    assert d.level == "Level 2" and "CAP-SV-ONLY" in d.caps
    assert decide_v2(f(axes={"S": True, "V": True, "H": True})).level == "Level 3"


def test_one_axis_level2_zero_axis_level1():
    assert decide_v2(f(axes={"S": True})).level == "Level 2"
    assert decide_v2(f(axes={"S": False, "H": False})).level == "Level 1"


def test_low_confidence_or_partial_caps_level3():
    assert decide_v2(f(axes={"S": True, "H": True}, confidence="낮음")).level == "Level 2"
    assert decide_v2(f(axes={"S": True, "H": True}, input_status="부분 판정")).level == "Level 2"


def test_hold_and_counter_order_same_as_v11():
    assert decide_v2(f(input_status="보류", missing_inputs=["x"], axes={"S": True, "H": True})).level == "판단 보류"
    assert decide_v2(CellFacts(DEV, counter_result="보류", axes={"S": True})).level == "판단 보류"
    assert decide_v2(CellFacts(DEV, counter_result="기각", axes={"S": True})).level == "Level 1"


def test_deviation_capped_at_level2_and_min_drop():
    md = load_rules_v2()["deviation"]["min_drop"]
    d = decide_v2(CellFacts(DEV, counter_result="통과", deviation_drop=md + 0.01, axes={"S": True, "H": True}))
    assert d.level == "Level 2" and "CAP-SHARED-CONTEXT" in d.caps
    assert decide_v2(CellFacts(DEV, counter_result="통과", deviation_drop=md - 0.01, axes={"S": True})).level == "Level 1"
    assert decide_v2(CellFacts(DEV, counter_result="통과", deviation_drop=md + 0.01, axes={})).level == "Level 1"


def test_types():
    assert cell_type(["H", "S"]) == "이중 위험"
    assert cell_type(["H"]) == "관계 단절형"
    assert cell_type(["V"]) == "구조·경제 취약형"
    assert cell_type(["A"]) == "해당 없음"
