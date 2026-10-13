"""Claude 연결 (에이전트 설계 §8). 기본 테스트는 네트워크를 쓰지 않는다 (가짜 클라이언트).
실제 API 시험은 RUN_LLM_TESTS=1 일 때만 (호출 2~3회).

- 구조화 출력 → Pydantic 검증, 실패하면 1회 재시도 뒤 규칙·템플릿으로 대신
- 호출마다 모델 ID·프롬프트 버전·토큰·비용·소요 시간 기록 (키는 기록하지 않음)
- 설명 문장은 근거 ID 만 인용, 새 숫자가 있는 문장은 버림
"""

import json
import os
from datetime import date
from types import SimpleNamespace

import pytest

from bigcontest.agent import guard as G
from bigcontest.agent.llm import (
    AnthropicLLM,
    PROMPT_DIR,
    _api_key,
    call_llm,
    cost_usd,
    llm_config,
    sanitize_sentences,
)
from bigcontest.agent.schemas import EvidenceCard, Fact, Report, ToolResult

AS_OF = date(2025, 10, 15)


class FakeClient:
    """messages.create 만 흉내. 응답 목록을 차례로 돌려줌."""

    def __init__(self, texts, stop="end_turn"):
        self.texts, self.stop, self.requests = list(texts), stop, []
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        text = self.texts.pop(0)
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def card() -> EvidenceCard:
    e = ToolResult(evidence_id="EV-20251015-CC-DEV-CC-20F-20251007", metric="카드 결제건수 평소 대비 (이탈 경보)",
                   value=0.871, unit="평소 대비 비율", direction="감소", analysis_mode="simulated_replay", as_of=AS_OF,
                   merchant_region="춘천시", customer_residence="강원", is_shared_context=True,
                   analysis_cohort="20대 여성", status="OK", data_version="card2@sha256:abc")
    f = Fact(metric=e.metric, region="춘천시 가맹점 · 강원 거주 20대 여성", period="2025-10-01~2025-10-07", value=0.871,
             unit=e.unit, direction="감소", evidence_id=e.evidence_id)
    return EvidenceCard(card_id="C", title="t", snapshot_id="dev-x", analysis_mode="simulated_replay", as_of=AS_OF,
                        evidence_revision=1, facts=[f], evidence=[e])


def test_config_models_prompts_and_pricing():
    cfg = llm_config()
    assert cfg["models"]["supervisor"]["id"] == "claude-sonnet-5-5"
    assert cfg["models"]["brief"]["id"] == "claude-haiku-5-5"
    for slot in cfg["slots"].values():
        assert (PROMPT_DIR / f"{slot['prompt']}.md").exists()
        assert slot["temperature"] is None  # 샘플링 값은 보내지 않음
    assert cost_usd("supervisor", 1_000_000, 1_000_000) == 12.0  # $2 + $10 (공식 가격표)
    assert cost_usd("brief", 50_000, 0) == 0.005
    assert cost_usd("brief", 200_000, 0) == 0.1  # 100K 넘는 프롬프트는 $0.50/MTok


def test_classify_structured_output():
    client = FakeClient([json.dumps({"route": "lookup", "request": {"city": "강남", "metric": "체류인구", "group": None,
                                     "age_min": 60, "age_max": 69, "sex": None, "time_of_day": "18_23"},
                                     "reason": "단순 조회"})])
    llm = AnthropicLLM(client=client)
    out = llm.classify("강남 60대의 밤 활동은 어때?", None)
    assert out["route"] == "lookup" and out["request"]["time_of_day"] == "18_23"
    req = client.requests[0]
    assert req["model"] == "claude-sonnet-5-5" and req["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in req
    rec = llm.calls[0]
    assert rec["prompt_version"] == "classify_v2" and rec["input_tokens"] == 1000 and rec["cost_usd"] == 0.004


def test_invalid_output_retries_once_then_falls_back():
    llm = AnthropicLLM(client=FakeClient(['{"route": "maybe"}', "not json"]))
    out, rec = call_llm(llm, "classify", llm.classify, "질문", None)
    assert out is None and rec["attempts"] == 2 and not rec["ok"] and rec["api_calls"] == 2
    assert rec["fallback"] and rec["input_tokens"] == 2000  # 실패한 호출도 토큰·비용 기록


def test_refusal_is_error():
    llm = AnthropicLLM(client=FakeClient(["{}", "{}"], stop="refusal"))
    out, rec = call_llm(llm, "classify", llm.classify, "질문", None)
    assert out is None and "refusal" in rec["error"]


def test_tool_choice_is_whitelisted():
    llm = AnthropicLLM(client=FakeClient([json.dumps({"tools": ["calc_holiday_gap"], "reason": "r"})]))
    assert llm.choose_extra_tools({}, ["calc_holiday_gap", "get_mobility_layer"], []) == ["calc_holiday_gap"]
    schema = llm.calls  # 스키마 enum 이 available 로 묶임
    assert schema[0]["ok"]


def test_explain_drops_new_numbers_and_unknown_ids():
    c = card()
    lines = [
        "춘천 가맹점 20대 여성 고객의 결제 하락은 연휴 효과로 설명되었습니다 [EV-20251015-CC-DEV-CC-20F-20251007].",
        "결제가 13% 줄었습니다.",  # 새 숫자
        "다른 근거도 봐야 합니다 [EV-20251015-CC-XXX].",  # 카드에 없는 ID
    ]
    llm = AnthropicLLM(client=FakeClient([json.dumps({"sentences": lines})]))
    out = llm.explain(c, [], 1)
    assert out == lines[:1] and llm.calls[0]["dropped"] == 2
    # 남은 문장은 사실 검사를 통과 (근거 ID 인용만, 숫자 없음)
    report = Report(report_revision=1, evidence_card=c, fact_sentences=[], narrative="\n".join(out))
    assert G.check_facts(report, c).verdict == "PASS"
    assert sanitize_sentences(["카드 근거 [EV-20251015-CC-DEV-CC-20F-20251007] 참고"], c)


def test_explain_brief_uses_haiku():
    client = FakeClient([json.dumps({"sentences": ["요약입니다."]})])
    llm = AnthropicLLM(client=client)
    llm.explain(card(), [], 1, brief=True)
    assert client.requests[0]["model"] == "claude-haiku-5-5" and llm.calls[0]["prompt_version"] == "brief_v3"


def test_api_key_never_logged():
    llm = AnthropicLLM(client=FakeClient([json.dumps({"sentences": ["설명."]})]))
    llm.explain(card(), [], 1)
    key = _api_key()
    if key:
        assert key not in json.dumps(llm.calls, ensure_ascii=False)


# ---------------------------------------------------------------- 실제 API (RUN_LLM_TESTS=1)

live = pytest.mark.skipif(os.environ.get("RUN_LLM_TESTS") != "1" or not _api_key(),
                          reason="RUN_LLM_TESTS=1 과 ANTHROPIC_API_KEY 가 있을 때만")


@live
def test_live_classify_and_explain():
    llm = AnthropicLLM()
    out = llm.classify("추석 전에 춘천 어르신 어디부터 확인해야 해?", None)
    assert out["route"] == "investigate"
    lines = llm.explain(card(), [], 1)
    report = Report(report_revision=1, evidence_card=card(), fact_sentences=[], narrative="\n".join(lines))
    assert G.check_facts(report, card()).verdict == "PASS"
    for c in llm.calls:
        assert c["ok"] and c["input_tokens"] > 0 and c["cost_usd"] is not None


def test_negated_caveat_exempt_only_in_template_sentences():
    """부정 주의 문장 예외는 코드가 만든 템플릿 문장에만. LLM 설명 문장은 부정문이어도 G-14
    (프롬프트가 부정문도 금지). 반어·조건 꼴은 템플릿이어도 예외 아님."""
    c = card()
    caveat = "체류인구는 주민 수가 아니므로 현장 확인의 출발점으로만 봅니다."

    def rules_of(template=(), narrative=None):
        r = Report(report_revision=1, evidence_card=c, fact_sentences=["최종 판단은 담당자", c.time_label, *template],
                   narrative=narrative)
        return G.check_expression(r, c).rule_ids

    assert "G-14" not in rules_of(template=[caveat])
    assert "G-14" in rules_of(narrative=caveat)
    assert "G-14" in rules_of(narrative="체류인구로 본 주민 수가 줄었습니다.")
    assert "G-03" in rules_of(template=["이 동 주민이 아니면 누가 결제했겠습니까."])
    assert "G-14" in rules_of(template=["체류인구가 주민 수가 아니라고 할 수 없습니다."])


def test_retry_sends_guard_feedback():
    client = FakeClient([json.dumps({"sentences": ["설명."]})])
    llm = AnthropicLLM(client=client)
    llm.explain(card(), [], 2, feedback=["G-14: 지표를 다른 뜻으로 바꾸지 않았는가: 문장"])
    payload = json.loads(client.requests[0]["messages"][0]["content"])
    assert payload["guard_feedback"] == ["G-14: 지표를 다른 뜻으로 바꾸지 않았는가: 문장"]
