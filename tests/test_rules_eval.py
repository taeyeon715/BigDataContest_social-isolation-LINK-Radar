"""등급 규칙 실제 자료 시험 (dev 스냅샷). 자료가 없으면 건너뜀.

- 같은 동의 등급은 도시 전체 조회와 그 동 하나 조회에서 같음, 도시끼리 모집단이 섞이지 않음
- 비교안: 강남 동 순위는 A(주민등록+달력)와 D(전체)가 같음, 잘못된 단위 주장 0
- 연휴 뒤 기대 경로: 기준일 카드에서 경로 뒤 회복을 보기 전에는 기각하지 않음 (강남 고령층 10/15)
- 페르소나 (합성): 평소 기간 20% 지속 하락 → 반증 통과 Level 2
"""

from datetime import date

import pandas as pd
import pytest

from bigcontest.agent.tools import call_tool
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.datasource import DataSource, dev_available, dev_snapshot_id, register_source
from bigcontest.rules.compare import VARIANT_INPUTS, collect, ctx_for, filter_evidence, run_rules, run_variant, top_k
from bigcontest.rules.compare import key_of, wrong_unit_claims

pytestmark = pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")
D1 = date(2025, 9, 26)


@pytest.fixture(scope="module")
def d1_evidence():
    c = ctx_for(dev_snapshot_id(), "강남", D1, "중장년 남성 1인가구")
    return c, collect(c)


@pytest.mark.parametrize("city", ["강남", "춘천"])
def test_i08_same_level_full_vs_filtered(city):
    c = ctx_for(dev_snapshot_id(), city, D1, "고령층")
    full = [e for e in call_tool("get_structure", c) if e.status == "OK" and e.metric == "1인세대 비율"]
    tops = S.structure_tops(full)
    n = len(full)
    for e in full:
        one = [x for x in call_tool("get_structure", c, adm_cd=S.adm_of(e)) if x.status == "OK"]
        assert (S.adm_of(e) in S.structure_tops(one)) == (S.adm_of(e) in tops), S.adm_of(e)
        assert any(f"{city} {n}개 동 전체" in w and "주민등록" in w for w in one[0].warnings)


def test_no_population_mixing_across_cities():
    gn = [e for e in call_tool("get_structure", ctx_for(dev_snapshot_id(), "강남", D1, "고령층")) if e.status == "OK"]
    cc = [e for e in call_tool("get_structure", ctx_for(dev_snapshot_id(), "춘천", D1, "고령층")) if e.status == "OK"]
    both = S.structure_tops(gn + cc)
    assert {a for a in both if a.startswith("11")} == set(S.structure_tops(gn))
    assert {a for a in both if a.startswith("32")} == set(S.structure_tops(cc))


def test_d1_count_and_variants(d1_evidence):
    c, ev = d1_evidence
    runs = {v: run_variant(ev, v, c.request, D1, 10).candidates for v in "ABCD"}
    sched = [x for x in runs["D"] if x.candidate_type == "scheduled_access_review"]
    assert len(sched) == 7 and all(x.candidate_level == "Level 2" for x in sched)  # 진입 규칙으로 22 → 7
    tops = {v: {key_of(x) for x in top_k(sorted(r, key=lambda x: x.queue_rank), 10)} for v, r in runs.items()}
    assert tops["A"] == tops["D"]  # 강남 동 순위는 주민등록이 정함
    for v, r in runs.items():
        assert wrong_unit_claims(r, filter_evidence(ev, VARIANT_INPUTS[v])) == 0


def test_city_common_not_rejected_before_path_recovery():
    c = ctx_for(dev_snapshot_id(), "강남", date(2025, 10, 15), "고령층")
    r = run_rules(collect(c), c.request, c.as_of, 10).candidates
    held = [x for x in r if x.candidate_level == "판단 보류"]
    assert held and not any(x.counter_result == "기각" for x in r)
    cf2 = [e for e in collect(c) if e.metric == "반증: 도시 공통요인" and e.value == "공통 변화"]
    assert cf2 and all(any("경로 뒤 회복 미관측" in w for w in e.warnings) for e in cf2)


def test_persona_sustained_drop_level2():
    """합성 검증 (실제 고립 탐지율 아님): 춘천 70대 여성 11월 20% 지속 감소 → 반증 통과 Level 2."""
    ds = DataSource(dev_snapshot_id())
    p = ds.panel()
    col = ("춘천", "70대", "여성")
    y = p.Y[col].to_numpy().copy()
    m = (p.dates >= pd.Timestamp("2025-11-03")) & (p.dates <= pd.Timestamp("2025-11-30"))
    y[m] *= 0.8
    ds._cache["panel"] = p.replace_column(col, y)
    register_source("persona-test-p1", ds)
    c = ctx_for("persona-test-p1", "춘천", date(2025, 11, 12), "고령층")
    r = run_rules(collect(c), c.request, c.as_of, 10).candidates
    (dev,) = [x for x in r if x.candidate_type == "unexpected_activity_deviation" and x.analysis_cohort == "70대 여성"]
    assert dev.counter_result == "통과" and dev.candidate_level == "Level 2" and dev.rule_id == "L2-STRUCT-DEVIATION"
    assert dev.is_shared_context and dev.adm_cd is None and dev.check_first_adm
