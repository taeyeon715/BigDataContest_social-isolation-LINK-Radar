"""화면 문장 다듬기: 등급 조건 값의 단위(비율→%), 집중 확인 줄을 낱말 중간에서 자르지 않기."""

from bigcontest.agent.nodes import clip_reason
from bigcontest.agent.schemas import RuleCheck
from bigcontest.agent.tools import report as R


def test_rule_check_ratio_as_percent():
    v = R._view(RuleCheck(condition="이탈 크기 최소값", met=True, value=0.153, threshold=0.1, comparator=">=", unit="비율"))
    assert v.text == "이탈 크기 최소값 조건: 충족(15.3%, 기준 10% 이상, 여유 5.3%p)"
    assert "비율" not in v.text.replace("이탈 크기", "")
    km = R._view(RuleCheck(condition="관측 거점까지 직선거리", met=True, value=18.57, threshold=5.0, comparator=">=", unit="km"))
    assert km.text == "관측 거점까지 직선거리 조건: 충족(18.57km, 기준 5km 이상, 여유 13.57km)"
    rank = R._view(RuleCheck(condition="구조 상위", met=True, value=5, threshold=8, comparator="<=", unit="위"))
    assert rank.text == "구조 상위 조건: 충족(5위, 기준 8위 이하, 여유 3위)"


REASON = ("구조 취약성 상위(1인세대 비율 0.6124, 춘천 25개 동 전체, 2025-08 주민등록 (조회 필터와 무관) 중 5/25위) + "
          "접근성 근거(현재 원장 기준 접근성: 안부·관계 거점(노인복지관·사회복지관)까지 직선 18.6km, 과거 당시 확인 안 됨) + "
          "예정된 트리거(R1 추석 연휴 D-7 (10/03~10/09))")


def test_clip_reason_on_part_boundary():
    out = clip_reason(REASON)
    assert out.startswith("구조 취약성 상위(") and out.endswith(" 외")
    assert "접근성 근)" not in out and "+ 접근성" not in out  # 조각 중간에서 자르지 않음
    assert clip_reason("짧은 이유") == "짧은 이유"
    long_one = "가" * 50 + " " + "나" * 100
    assert clip_reason(long_one) == "가" * 50 + " …"
