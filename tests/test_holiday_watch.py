"""연휴 주의 지역 (src/bigcontest/holiday_watch.py, rules_v2.yaml holiday_watch, 에이전트 apply_holiday_watch)."""

from datetime import date

import pandas as pd
import pytest

from bigcontest.holiday_watch import holiday_phase, watch_cells, watch_config

BLOCKS = [(date(2025, 8, 15), date(2025, 8, 17), "광복절"), (date(2025, 10, 3), date(2025, 10, 9), "추석")]


def test_phase_windows():
    lead = watch_config()["lead_days"]
    assert holiday_phase(BLOCKS, date(2025, 9, 26), "강남").phase == "연휴 전"  # D-7
    assert holiday_phase(BLOCKS, date(2025, 10, 3 - 0) - pd.Timedelta(days=lead + 1).to_pytimedelta(), "강남") is None
    assert holiday_phase(BLOCKS, date(2025, 10, 6), "강남").phase == "연휴 중"
    # 연휴 뒤는 follow_up_days 가 있는 도시(춘천)만
    assert holiday_phase(BLOCKS, date(2025, 10, 13), "춘천").phase == "연휴 뒤"
    assert holiday_phase(BLOCKS, date(2025, 10, 13), "강남") is None
    assert holiday_phase(BLOCKS, date(2025, 10, 20), "춘천") is None  # 10일 지남
    assert holiday_phase(BLOCKS, date(2025, 11, 20), "춘천") is None


def _cell(adm, age, s=False, h=False, v=False, a=False, pop=500, h_obs=True, v_obs=True):
    return {"city": "춘천", "adm_cd": adm, "sex": "남", "age5": age, "pop": pop, "s_top": s, "s_eb": 0.3, "s_rank": 1,
            "s_n": 25, "s_small": pop < 100, "h_state": "observed" if h_obs else "missing", "h_top": h, "h": 0.9,
            "h_basis": "체류도만", "v_state": "observed" if v_obs else "missing", "v_counts": v, "v_eb": 0.2,
            "v_source": "시험", "a_top": a, "a_km": 6.0 if a else 1.0}


def test_watch_cells_rule():
    ax = pd.DataFrame([
        _cell("32010001", "60대+", s=True, h=True),           # S+H → Level 3
        _cell("32010002", "60대+", s=True, v=True),           # {S, V}만 → 아님
        _cell("32010003", "60대+", h=True),                   # 축 하나 → 아님
        _cell("32010004", "60대+", s=True, h=True, pop=50),   # 작은 칸 → 신뢰도 낮음 → 아님
        _cell("32010005", "40대", s=True, h=True, v=True),    # 연휴 연령이 아님 → 아님
        _cell("32010006", "60대+", h=True, a=True),           # H+A → Level 3
    ])
    w = watch_cells(ax, "R1 시험")
    assert set(w["adm_cd"]) == {"32010001", "32010006"}
    assert (w["level"] == "Level 3").all()


def test_join_dongs():
    from bigcontest.agent.nodes import _join_dongs

    assert _join_dongs(["세곡동", "역삼1동", "논현1동", "논현2동"]) == "세곡동·역삼1동·논현1·2동"
    assert _join_dongs(["남산면", "효자2동"]) == "남산면·효자2동"


def _dev():
    from bigcontest.agent.tools.datasource import dev_available

    return dev_available()


@pytest.mark.skipif(not _dev(), reason="dev 스냅샷 자료(data/interim 등) 없음")
def test_agent_chuseok_d7_level3_is_watch_list():
    """추석 D-7 정기 감시: Level 3 = 연휴 주의 8개 동, 보고서 맨 앞에 연휴 주의 블록."""
    from bigcontest.agent.graph import build_graph
    from bigcontest.agent.state import initial_state
    from bigcontest.agent.tools.report import adm_name

    out = build_graph().invoke(initial_state("hw-d7", "monitoring", "2025-09-26", tools_backend="real"),
                               {"configurable": {"thread_id": "hw-d7"}})
    l3 = {(c["city"], adm_name(c["adm_cd"])) for c in out["candidates"] if c["candidate_level"] == "Level 3"}
    assert l3 == {("강남", "세곡동"), ("강남", "역삼1동"), ("강남", "논현1동"), ("강남", "논현2동"),
                  ("춘천", "효자2동"), ("춘천", "소양동"), ("춘천", "약사명동"), ("춘천", "조운동")}
    block = out["report"]["holiday_watch"]
    assert block[0].startswith("2025 추석 연휴 주의 지역: 강남 세곡동·역삼1동·논현1·2동, 춘천 효자2동·소양동·약사명동·조운동의 60대 이상")
    assert block == out["report"]["fact_sentences"][:4]
    assert "1회 관측" in block[3]
