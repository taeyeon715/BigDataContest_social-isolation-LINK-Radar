"""등급 규칙표 → 기대 등급, 경계값 (configs/rules.yaml rules-v1.0). 자료 없이 도는 단위 시험.

- 상위 30%·절대 최소값 0.35·직선 5km·이탈 10% 경계
- 부분 판정 / 근거 신뢰도 낮음 → Level 3 불가, 이탈은 Level 2 상한 (공유 맥락)
- K만 바꾸면 등급은 그대로, selected_for_review 만 바뀜
- 같은 event_id 로 다시 만들지 않음, 청년 남성은 '안내 강화'
- 연휴 뒤 기대 경로: 경로 뒤 회복이면 기각 가능, 아니면 확인 필요 그대로
- 순위 모집단 표시가 있으면 받은 근거 수와 무관하게 그 순위를 씀
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from bigcontest.agent.schemas import Candidate, ToolResult
from bigcontest.agent.tools import signal as S
from bigcontest.rules import load_rules, rules_version
from bigcontest.rules import candidates as RC
from bigcontest.rules.levels import Axes, StructFact, decide

AS_OF = date(2025, 9, 26)


def ev(eid, metric, value, scope=None, **kw) -> ToolResult:
    return ToolResult(evidence_id=eid, metric=metric, value=value, applied_scope=scope, analysis_mode="simulated_replay",
                      as_of=AS_OF, status="OK", value_state="observed", **kw)


def structs(vals: dict[str, float], city="강남", warn=None):
    code = "11" if city == "강남" else "32"
    return [ev(f"EV-20250926-STR-{a}", "1인세대 비율", v, f"{city} X동 ({a})", n_obs=5000,
               warnings=(warn or {}).get(a, [])) for a, v in vals.items()]


def sched(adm, status="충족", missing=None, cid="C-1", group=None):
    return Candidate(candidate_id=cid, candidate_type="scheduled_access_review", city="강남", adm_cd=adm,
                     analysis_cohort="40대·50대", trigger="R1 추석 연휴 D-7", input_status=status,
                     missing_inputs=missing or [], counter_result="해당 없음", service_eligibility_group=group,
                     evidence_ids=[f"EV-20250926-STR-{adm}", f"EV-20250926-ACC-{adm}"])


TEN = {f"1123{i:04d}": 0.70 - i * 0.03 for i in range(10)}  # 0.70 … 0.43


def test_version_and_rule_ids_on_every_candidate():
    out = S.assign_level([sched("11230000"), sched("11230005", cid="C-2")], structs(TEN))
    assert all(c.rule_version == rules_version() == "rules-v1.0" and c.rule_id for c in out)
    assert all("[rules-v1.0 " in c.level_reason and "임시 기준" not in c.level_reason for c in out)


@pytest.mark.parametrize("adm,level,rule", [
    ("11230000", "Level 2", "L2-STRUCT-TRIGGER"),  # 1위
    ("11230002", "Level 2", "L2-STRUCT-TRIGGER"),  # 3위 = ceil(0.3*10)
    ("11230003", "Level 1", "L1-TRIGGER-ONLY"),  # 4위: 상위 30% 밖
])
def test_structure_top_share_boundary(adm, level, rule):
    (c,) = S.assign_level([sched(adm)], structs(TEN))
    assert (c.candidate_level, c.rule_id) == (level, rule)


def test_structure_absolute_minimum():
    low = {f"1123{i:04d}": 0.34 - i * 0.01 for i in range(10)}  # 1위도 0.34 < 0.35
    (c,) = S.assign_level([sched("11230000")], structs(low))
    assert c.candidate_level == "Level 1"


@pytest.mark.parametrize("km,level", [(4.99, "Level 2"), (5.0, "Level 3"), (12.0, "Level 3")])
def test_access_boundary(km, level):
    e = structs(TEN) + [ev("EV-20250926-ACC-11230000", "관측 거점까지 직선거리", km, "강남 X동 (11230000)", unit="km")]
    (c,) = S.assign_level([sched("11230000")], e)
    assert c.candidate_level == level


def test_access_only_is_level2():
    e = structs(TEN) + [ev("EV-20250926-ACC-11230009", "관측 거점까지 직선거리", 9.0, "강남 X동 (11230009)", unit="km")]
    (c,) = S.assign_level([sched("11230009")], e)
    assert (c.candidate_level, c.rule_id) == ("Level 2", "L2-ACCESS-TRIGGER")


def test_partial_and_low_confidence_cap_level3():
    e = structs(TEN) + [ev("EV-20250926-ACC-11230000", "관측 거점까지 직선거리", 9.0, "강남 X동 (11230000)", unit="km")]
    (c,) = S.assign_level([sched("11230000", "부분 판정", ["winter_contraction"])], e)
    assert c.candidate_level == "Level 2" and c.rule_id.endswith("CAP-PARTIAL") and c.evidence_confidence == "낮음"
    small = [x.model_copy(update={"n_obs": 50}) if x.evidence_id.endswith("11230000") else x for x in e]
    (c,) = S.assign_level([sched("11230000")], small)
    assert c.candidate_level == "Level 2" and "CAP-CONF-LOW" in c.rule_id and c.evidence_confidence == "낮음"


def _axes(**kw) -> Axes:
    base = dict(candidate_type="unexpected_activity_deviation", input_status="충족", counter_result="통과",
                counter_reason="반증 통과", structure=StructFact("11230000", 0.7, 1, 22, "강남 22개 동", True))
    return Axes(**{**base, **kw})


@pytest.mark.parametrize("drop,level", [(0.099, "Level 1"), (0.10, "Level 2"), (0.25, "Level 2")])
def test_deviation_minimum_effect(drop, level):
    assert decide(_axes(deviation_drop=drop)).level == level


def test_deviation_never_level3_shared_context():
    d = decide(_axes(deviation_drop=0.3, second_adm_axis=True))
    assert d.level == "Level 2" and "CAP-SHARED-CONTEXT" in d.caps


@pytest.mark.parametrize("counter,level,rule", [("기각", "Level 1", "L1-REJECTED"), ("보류", "판단 보류", "HOLD-COUNTER")])
def test_counter_results(counter, level, rule):
    d = decide(_axes(counter_result=counter, counter_reason="사유", deviation_drop=0.3))
    assert (d.level, d.rule_id) == (level, rule)


def test_hold_input_first():
    d = decide(_axes(input_status="보류", missing_inputs=["card_cohort_series"], counter_result="기각"))
    assert d.level == "판단 보류" and d.rule_id == "HOLD-INPUT"


def test_threshold_override_changes_level():
    r = load_rules({"thresholds.structure.top_share": 0.4})
    (c,) = S.assign_level([sched("11230003")], structs(TEN), r)
    assert c.candidate_level == "Level 2"  # 4위: 40% 안


# ---------------------------------------------------------------- 순위 모집단


def test_population_rank_independent_of_filter():
    warn = {"11230009": [RC.rank_warning("강남 22개 동 전체, 2025-08 주민등록 (조회 필터와 무관)", 15, 22)]}
    filtered = structs({"11230009": 0.43}, warn=warn)  # 동 하나만 조회
    (c,) = S.assign_level([sched("11230009")], filtered)
    assert c.candidate_level == "Level 1" and S.structure_tops(filtered) == {}  # 예전: "도시 안 1/1위" → Level 2
    warn = {"11230009": [RC.rank_warning("강남 22개 동 전체, 2025-08 주민등록 (조회 필터와 무관)", 2, 22)]}
    (c,) = S.assign_level([sched("11230009")], structs({"11230009": 0.43}, warn=warn))
    assert c.candidate_level == "Level 2" and "강남 22개 동 전체" in c.level_reason and "2/22위" in c.level_reason


def test_no_population_mixing_between_cities():
    gn = structs({f"1123{i:04d}": 0.40 + i * 0.01 for i in range(10)})
    cc = structs({f"3201{i:04d}": 0.60 + i * 0.01 for i in range(10)}, city="춘천")
    tops = S.structure_tops(gn + cc)
    assert len([a for a in tops if a.startswith("11")]) == 3 and len([a for a in tops if a.startswith("32")]) == 3


# ---------------------------------------------------------------- K · event_id · 대응 종류


def test_k_changes_selection_not_level():
    cands = S.assign_level([sched(a, cid=f"C-{i}") for i, a in enumerate(TEN)], structs(TEN))
    runs = {k: S.assign_queue(cands, structs(TEN), k) for k in (1, 2, 5, 20)}
    lv = {k: [(c.candidate_id, c.candidate_level, c.queue_rank) for c in v] for k, v in runs.items()}
    assert lv[1] == lv[2] == lv[5] == lv[20]  # 등급·순서는 K 와 무관
    assert [sum(c.selected_for_review for c in v) for v in runs.values()] == [1, 2, 3, 3]  # 적격(Level 2) 3곳
    top = min(runs[1], key=lambda c: c.queue_rank)
    assert top.adm_cd == "11230000"  # 같은 등급이면 구조 값 높은 순


def test_queue_tie_break_deterministic():
    cands = S.assign_level([sched("11230001", cid="C-9"), sched("11230000", cid="C-1")],
                           structs({"11230000": 0.5, "11230001": 0.5}))
    q = sorted(S.assign_queue(cands, structs({"11230000": 0.5, "11230001": 0.5}), 5), key=lambda c: c.queue_rank)
    assert [c.adm_cd for c in q] == ["11230000", "11230001"]  # 값이 같으면 adm_cd 순


def _trigger(as_of: date):
    start = date(2025, 10, 3)
    return ToolResult(evidence_id=f"EV-{as_of:%Y%m%d}-GN-TRG-R1", metric=S.TRIGGER_METRIC,
                      value=f"R1 추석 연휴 D-{(start - as_of).days} (10/03~10/09)", applied_scope="강남",
                      observed_from=start, observed_to=date(2025, 10, 9), simulated_available_at=start - timedelta(days=7),
                      analysis_mode="simulated_replay", as_of=as_of, status="OK", value_state="observed")


def _classify(as_of: date):
    from bigcontest.agent.schemas import NormalizedRequest

    tgt = ToolResult(evidence_id=f"EV-{as_of:%Y%m%d}-GN-TGT", metric="대상 집단 계약", value="40대·50대",
                     analysis_cohort="40대·50대", applied_scope="강남", analysis_mode="simulated_replay", as_of=as_of,
                     status="OK", value_state="observed")
    st = [x.model_copy(update={"evidence_id": x.evidence_id.replace("20250926", f"{as_of:%Y%m%d}"), "as_of": as_of})
          for x in structs(TEN)]
    req = NormalizedRequest(city="강남", metric="카드결제", group="중장년 남성 1인가구", period_to=as_of)
    return S.classify_candidate_type([_trigger(as_of), tgt, *st], req, as_of)


def test_scheduled_created_on_trigger_day_once_per_event():
    d7 = _classify(date(2025, 9, 26))  # 추석 D-7
    assert d7 and {c.event_id for c in d7} == {"강남:R1:20251003"}
    assert {c.event_opened_at for c in d7} == {date(2025, 9, 26)}
    assert len(d7) == 3  # 진입 규칙: 상위 30% (10곳 중 3곳)만, 나머지 7곳은 후보 아님
    seen: set = set()
    assert len(RC.new_only(d7, seen)) == 3
    d5 = _classify(date(2025, 9, 28))  # 같은 사건 다시 실행
    assert {c.event_id for c in d5} == {"강남:R1:20251003"} and RC.new_only(d5, seen) == []


def test_no_axis_dong_gives_single_city_level_candidate():
    low = {f"1123{i:04d}": 0.30 for i in range(10)}
    from bigcontest.agent.schemas import NormalizedRequest

    as_of = date(2025, 9, 26)
    req = NormalizedRequest(city="강남", metric="카드결제", group="고령층", period_to=as_of)
    tgt = ToolResult(evidence_id="EV-20250926-GN-TGT", metric="대상 집단 계약", value="60대·70대·80대+",
                     analysis_cohort="60대·70대·80대+", applied_scope="강남", analysis_mode="simulated_replay",
                     as_of=as_of, status="OK", value_state="observed")
    (c,) = S.classify_candidate_type([_trigger(as_of), tgt, *structs(low)], req, as_of)
    assert c.adm_cd is None and c.input_status == "충족"
    assert S.assign_level([c], structs(low))[0].candidate_level == "Level 1"


def test_youth_response_type_is_guidance():
    e = structs(TEN)
    (c,) = S.assign_level([sched("11230000", group="20~34세 남성")], e)
    assert c.candidate_level == "Level 2" and c.response_type == "안내 강화"
    (c,) = S.assign_level([sched("11230000", group="65세 이상")], e)
    assert c.response_type == "담당자 추가 분석"
    assert RC.response_type("Level 3", "청년 남성") == "안내 강화"
    assert RC.response_type("Level 1", "20~34세 남성") == "추세 관찰"


# ---------------------------------------------------------------- 연휴 뒤 기대 경로


HOL = {date(2025, 10, d) for d in (3, 5, 6, 7, 8, 9)}
BLOCK = (date(2025, 10, 3), date(2025, 10, 9), "추석")


def test_expected_path_days():
    end, rec = RC.expected_path(BLOCK, HOL)
    assert end == date(2025, 10, 13) and rec == [date(2025, 10, 14), date(2025, 10, 15), date(2025, 10, 16)]
    end, rec = RC.expected_path((date(2025, 8, 15), date(2025, 8, 17), "광복절"), {date(2025, 8, 15)})
    assert end == date(2025, 8, 17)  # 3일 연휴: 기대 경로 없음


def _series(after_level: float):
    dates = pd.date_range("2025-10-01", "2025-10-31")
    E = np.zeros((len(dates), 1))
    E[(dates >= "2025-10-10") & (dates <= "2025-10-13"), 0] = -0.15  # 경로 안 하락
    E[dates >= "2025-10-14", 0] = after_level
    return E, E.copy(), dates


@pytest.mark.parametrize("after,cutoff,verdict", [
    (0.0, date(2025, 10, 20), "기대 경로 안"),
    (-0.15, date(2025, 10, 20), "경로 뒤 낮음"),
    (0.0, date(2025, 10, 15), "경로 뒤 미관측"),
])
def test_post_path_review(after, cutoff, verdict):
    E, L, dates = _series(after)
    assert RC.post_path_review(E, L, dates, 0, BLOCK, HOL, cutoff)[0] == verdict


def test_judge_counter_path_rejection():
    c = Candidate(candidate_id="C-1", candidate_type="unexpected_activity_deviation", city="강남",
                  analysis_cohort="70대 남성", input_status="충족", counter_result="보류")
    conf = {"holiday_expectation": ev("EV-1-CF1-K", "반증: 공휴일 기대값", "설명 일부", warnings=["n1"]),
            "city_common_factor": ev("EV-1-CF2-K", "반증: 도시 공통요인", "기대 경로 안", warnings=["경로 뒤 회복"]),
            "weather_asos": ev("EV-1-CF3-K", "반증: 강수", "통과", warnings=["n3"])}
    res, why = S.judge_counter(c, conf)
    assert res == "기각" and "경로 뒤 회복" in why
    conf["city_common_factor"] = conf["city_common_factor"].model_copy(update={"value": "공통 변화"})
    assert S.judge_counter(c, conf)[0] == "보류"


# ---------------------------------------------------------------- rule_checks (explain_level 입력)


def test_rule_checks_filled_by_engine():
    from bigcontest.agent.tools import report as R

    e = structs(TEN) + [ev("EV-20250926-ACC-11230000", "관측 거점까지 직선거리", 2.0, "강남 X동 (11230000)", unit="km")]
    (c,) = S.assign_level([sched("11230000")], e)
    assert c.candidate_level == "Level 2" and c.rule_checks
    by = {x.condition: x for x in c.rule_checks}
    acc = by["관측 거점까지 직선거리"]
    assert acc.met is False and acc.value == 2.0 and acc.threshold == 5.0 and acc.level_if_met == "Level 3"
    top = by["구조 상위 (도시 안 상위 30%)"]
    assert top.met is True and top.value == 1 and top.threshold == 3
    ex = R.explain_level(c)
    assert ex.rule_id == "L2-STRUCT-TRIGGER" and any("Level 3" in w for w in ex.what_if)
    (d,) = S.assign_level([sched("11230005")], e)  # 6위: 구조 상위가 되면 Level 2
    assert {x.condition: x.level_if_met for x in d.rule_checks}["구조 상위 (도시 안 상위 30%)"] == "Level 2"


def test_rule_checks_deviation_small_drop():
    from bigcontest.rules.levels import rule_checks

    a = _axes(deviation_drop=0.05)
    by = {x["condition"]: x for x in rule_checks(a)}
    assert by["이탈 크기 최소값"]["met"] is False and by["이탈 크기 최소값"]["level_if_met"] == "Level 2"
