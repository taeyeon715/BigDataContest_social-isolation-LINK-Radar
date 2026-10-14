"""LLM 자리 (에이전트 설계 §8): 인터페이스, 규칙 기반 가짜(FakeLLM), Claude 연결(AnthropicLLM).

LLM 이 맡는 일
- classify: 질문 → 경로(lookup / investigate / out_of_scope)
- choose_extra_tools: 추가 조사 도구 이름 (화이트리스트로 거름. 필수 근거 도구는 LLM 과 무관하게 항상 실행)
- explain: 설명 문장 (사실은 템플릿이 근거 카드에서 넣음)
- plan_more: 추가 조사 메모 → 화이트리스트 도구·반증 계획
- followup: 이어 묻기 답 문장 (근거 카드 안 근거 ID 만 인용)
- investigate: 조사 루프에서 코드가 만든 미해결 질문 중 확인할 것 고르기 (근거를 본 뒤, 결과는 "추가로 확인한 것"에만)
- followup_intent: 이어 묻기 질문 종류 (규칙이 못 정할 때만, enum 5개)

등급·후보는 바꾸지 못한다. 호출마다 기록(모델 ID, 프롬프트 버전, 토큰, 비용, 소요 시간)을 남긴다.
가짜 변형(variant)은 테스트가 "LLM 이 무엇을 골라도 등급이 같다", "틀린 사실을 Guard 가 잡는다"를 확인하는 데 쓴다.
상태의 `llm` 값: none(LLM 없음) / 가짜 변형 이름 / claude (configs/llm.yaml 의 모델, ANTHROPIC_API_KEY 필요) /
replay:<녹화 파일> (녹화한 Claude 응답을 재생, 키·네트워크 없음, cassette.py) / record:<녹화 파일> (실제 Claude 를 부르며 녹화).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from bigcontest.agent.guard import render_fact
from bigcontest.agent.schemas import EvidenceCard, Fact

PROMPT_VERSION = "fake-v0"


class LLMError(RuntimeError):
    pass


class LLMInitError(LLMError):
    """LLM 을 만들지 못함 (키 없음·SDK 없음·설정 오류). code 는 기록용 오류 코드 (키 값은 담지 않음)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LLM(Protocol):
    model_id: str
    prompt_version: str

    def classify(self, question: str, request: dict | None) -> dict: ...

    def choose_extra_tools(self, request: dict | None, available: list[str], required: list[str]) -> list[str]: ...

    def explain(self, card: EvidenceCard, candidates: list[dict], attempt: int, brief: bool = False,
                feedback: list[str] | None = None) -> list[str]: ...

    def plan_more(self, memo: str, tools: list[str], counter_checks: list[str], candidates: list[dict]) -> dict: ...

    def followup(self, question: str, payload: dict) -> list[str]: ...

    def investigate(self, questions: list[dict], candidates: list[dict], already: list[dict], max_picks: int) -> dict: ...

    def followup_intent(self, question: str) -> dict: ...


# 가짜 변형
VARIANTS = (
    "fake",  # 규칙과 같은 분류, 추가 조사 1개, 사실 없는 설명 문장
    "fake_no_tools",  # 추가 조사를 하나도 안 고름 (필수 도구도 안 고름)
    "fake_all_tools",  # 화이트리스트 밖 이름·필수 도구 이름까지 잔뜩 고름
    "fake_error",  # 모든 호출 실패 → 규칙 기반·템플릿으로 대신
    "fake_wrong_fact_once",  # 첫 보고서에만 틀린 사실(방향 바꿈) 문장을 넣음
    "fake_wrong_fact_always",  # 매번 틀린 사실 문장을 넣음 → REVISE 2회 뒤 ABSTAIN
    "fake_diagnosis_once",  # 첫 보고서에 개인 진단 문장 (G-04)
    "fake_wrong_level_once",  # 첫 보고서에 틀린 등급·방향 설명 → REVISE 뒤 통과
    "fake_flip_route",  # 경로를 뒤집어 냄 (investigate → lookup, lookup → investigate, 범위 밖 → lookup). 낮추기는 무시됨
)
_FLIP = {"investigate": "lookup", "lookup": "investigate", "out_of_scope": "lookup"}


class FakeLLM:
    """규칙 기반 가짜 LLM."""

    model_id = "fake"
    last_reason: str | None = None  # 마지막 호출의 판단 이유
    prompt_version = PROMPT_VERSION

    def __init__(self, variant: str = "fake"):
        if variant not in VARIANTS:
            raise ValueError(f"알 수 없는 가짜 LLM 변형: {variant}")
        self.variant = variant

    def _maybe_fail(self) -> None:
        if self.variant == "fake_error":
            raise LLMError("가짜 LLM 오류")

    def classify(self, question: str, request: dict | None) -> dict:
        self._maybe_fail()
        from bigcontest.agent.nodes import rule_route  # 규칙과 같은 답

        route = rule_route(question, request)
        if self.variant == "fake_flip_route":
            return {"route": _FLIP[route], "reason": "가짜: 경로 뒤집기"}
        return {"route": route, "reason": "가짜: 규칙과 같은 경로"}

    def choose_extra_tools(self, request: dict | None, available: list[str], required: list[str]) -> list[str]:
        self._maybe_fail()
        if self.variant == "fake_no_tools":
            self.last_reason = "가짜: 추가 조사 없음"
            return []
        if self.variant == "fake_all_tools":
            self.last_reason = "가짜: 화이트리스트 밖 이름까지 고름"
            return list(available) + list(required) + ["run_sql", "delete_everything"]
        self.last_reason = "가짜: 첫 번째 추가 도구"
        return available[:1]

    def explain(self, card: EvidenceCard, candidates: list[dict], attempt: int, brief: bool = False,
                feedback: list[str] | None = None) -> list[str]:
        self._maybe_fail()
        out = [
            "아래 후보는 근거 카드의 구조·달력·이탈 근거를 묶어 정리한 것입니다.",
            "현장 확인 순서는 담당자가 정합니다.",
        ]
        wrong = self.variant == "fake_wrong_fact_always" or (self.variant == "fake_wrong_fact_once" and attempt == 1)
        if wrong and card.facts:
            f = card.facts[0]
            flipped = {"감소": "증가", "증가": "감소"}.get(f.direction or "", "증가")
            bad = Fact(**{**f.model_dump(), "direction": flipped})
            out.append(render_fact(bad))
        if self.variant == "fake_wrong_level_once" and attempt == 1:
            out.append("모든 후보는 Level 3로 확정했습니다.")
            out.append("근거의 값은 감소를 보이지만 실제로는 증가했습니다.")
        if self.variant == "fake_diagnosis_once" and attempt == 1:
            out.append("이 동 어르신들은 고독사 가능성이 높습니다.")
        return out

    def plan_more(self, memo: str, tools: list[str], counter_checks: list[str], candidates: list[dict]) -> dict:
        self._maybe_fail()
        if self.variant == "fake_all_tools":  # 화이트리스트 밖 이름까지 → 노드가 거름
            return {"tools": list(tools) + ["run_sql"], "counter_checks": list(counter_checks) + ["주가"],
                    "reason": "가짜: 화이트리스트 밖 이름까지 고름"}
        from bigcontest.agent.nodes import memo_plan

        t, c = memo_plan(memo)
        return {"tools": t, "counter_checks": c, "reason": "가짜: 메모 낱말로 도구를 고름"}

    def investigate(self, questions: list[dict], candidates: list[dict], already: list[dict], max_picks: int) -> dict:
        """가짜 조사 고르기. fake: 목록 뒤쪽부터 (규칙과 다르게), fake_no_tools: 안 고름,
        fake_all_tools: 목록 밖 key·중복·숫자·진단 말이 든 이유까지 (노드가 거름)."""
        self._maybe_fail()
        keys = [q["key"] for q in questions]
        if self.variant == "fake_no_tools":
            return {"picks": [], "reason": "가짜: 더 확인할 것 없음"}
        if self.variant == "fake_all_tools":
            picks = [{"key": k, "why": "가짜: 3곳 모두 고독사 위험"} for k in keys + keys + ["RUN_SQL|강남|"]]
            return {"picks": picks, "reason": "가짜: 목록 밖 key 까지 고름"}
        picks = [{"key": k, "why": "가짜: 목록 뒤쪽 질문부터 확인"} for k in reversed(keys)][:max_picks]
        return {"picks": picks, "reason": "가짜: 목록 뒤쪽부터 고름"}

    def followup_intent(self, question: str) -> dict:
        self._maybe_fail()
        return {"intent": "other", "reason": "가짜: 기타"}

    def followup(self, question: str, payload: dict) -> list[str]:
        self._maybe_fail()
        ids = [f["evidence_id"] for f in payload.get("facts") or []][:2]
        cite = " ".join(f"[{i}]" for i in ids)
        out = [f"질문한 내용은 이 실행의 근거 카드 안에서만 답합니다 {cite}".strip() + "."]
        if self.variant in ("fake_wrong_fact_once", "fake_wrong_fact_always") and payload.get("facts"):
            f = payload["facts"][0]
            out.append(f"이 근거의 값은 크게 늘었습니다 [{f['evidence_id']}].")  # 방향 바꿈 → Guard 가 잡음
        if self.variant == "fake_diagnosis_once":
            out.append("이 동 어르신들은 고독사 가능성이 높습니다.")
        return out


def get_llm(name: str | None) -> LLM | None:
    """상태의 `llm` 값 → LLM. none/None 이면 LLM 없이 (규칙 기반·템플릿). claude 면 AnthropicLLM."""
    if not name or name == "none":
        return None
    if name == "claude":
        return AnthropicLLM()
    if name.startswith("replay:") or name.startswith("record:"):
        from bigcontest.agent.cassette import client_for

        return AnthropicLLM(client=client_for(name))
    return FakeLLM(name)


def get_llm_safe(name: str | None) -> tuple[LLM | None, dict | None]:
    """get_llm 과 같지만 초기화 실패를 막는다: 실패하면 (None, 기록) → 규칙·템플릿으로 진행.
    알 수 없는 가짜 변형 이름처럼 코드 쪽 잘못은 그대로 오류."""
    try:
        return get_llm(name), None
    except LLMInitError as e:
        code, msg = e.code, str(e)
    except LLMError as e:
        code, msg = "LLM_INIT_FAILED", str(e)
    except ImportError:
        code, msg = "LLM_SDK_MISSING", "anthropic SDK 를 불러오지 못함"
    except (KeyError, TypeError, OSError) as e:
        code, msg = "LLM_CONFIG_ERROR", f"LLM 설정 오류 ({type(e).__name__})"
    return None, {"slot": "init", "model_id": name, "prompt_version": None, "ok": False, "attempts": 1,
                  "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_ms": 0.0,
                  "error_code": code, "error": msg[:200], "fallback": "LLM 초기화 실패 → 규칙·템플릿으로 대신"}


def call_llm(llm: LLM | None, slot: str, fn: Callable[..., Any], *args) -> tuple[Any | None, dict | None]:
    """LLM 호출 한 번 (실패하면 1회 재시도, 그래도 실패하면 None → 규칙·템플릿). (결과, 호출 기록)."""
    if llm is None:
        return None, None
    # 프롬프트 판: 자리별 프롬프트 파일 이름 (classify_v3 등). 설정 판(llm-v1)이 아님. 가짜 LLM 은 fake-v0
    slot_prompt = ((getattr(llm, "cfg", None) or {}).get("slots") or {}).get(slot, {}).get("prompt")
    record = {
        "slot": slot,
        "model_id": llm.model_id,
        "prompt_version": slot_prompt or llm.prompt_version,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "attempts": 0,
        "ok": False,
        "latency_ms": 0.0,
    }
    from bigcontest.agent import budget as _budget  # 실행당 예산 (API 가 켬). 넘으면 부르지 않고 규칙·템플릿

    run_budget = _budget.current()
    over = run_budget.check() if run_budget is not None else None
    if over:
        record.update(error_code=_budget.BUDGET_EXCEEDED, error=over, fallback="예산 초과 → 규칙·템플릿으로 대신")
        return None, record
    t0 = time.perf_counter()
    result = None
    n0 = len(getattr(llm, "calls", []) or [])
    if hasattr(llm, "last_reason"):
        llm.last_reason = None
    for _ in range(2):
        record["attempts"] += 1
        try:
            result = fn(*args)
            record["ok"] = True
            break
        except LLMError as e:
            record["error"] = str(e)[:300]
    record["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    calls = (getattr(llm, "calls", []) or [])[n0:]
    if calls:  # 실제 API 호출 기록 (시도마다)
        record["model_id"] = calls[-1]["model_id"]
        record["prompt_version"] = calls[-1]["prompt_version"]
        record["input_tokens"] = sum(c["input_tokens"] for c in calls)
        record["output_tokens"] = sum(c["output_tokens"] for c in calls)
        costs = [c["cost_usd"] for c in calls]
        record["cost_usd"] = None if any(c is None for c in costs) else round(sum(costs), 6)
        record["api_calls"] = len(calls)
        record["dropped_sentences"] = sum(c.get("dropped", 0) for c in calls)
        if any("drafted" in c for c in calls):  # Guard 전에 뺀 문장 수·이유 코드 (설명 품질 지표가 씀)
            record["drafted_sentences"] = sum(c.get("drafted", 0) for c in calls)
            reasons = sorted({r for c in calls for r in c.get("dropped_reasons") or []})
            if reasons:
                record["dropped_reasons"] = reasons
        if any("masked" in c for c in calls):
            record["masked_items"] = max(c.get("masked", 0) for c in calls)
    if run_budget is not None:
        run_budget.add(record)
    if not record["ok"]:
        record["fallback"] = "규칙 기반·템플릿으로 대신"
    # LLM 이 낸 판단 이유 (decision_trace). 질문 원문은 담지 않음, 짧게
    reason = result.get("reason") if isinstance(result, dict) else getattr(llm, "last_reason", None)
    if record["ok"] and reason:
        record["reason"] = str(reason)[:200]
    return result, record


# ---------------------------------------------------------------- Claude (Anthropic API)

import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from functools import lru_cache  # noqa: E402
from typing import Literal  # noqa: E402

from pydantic import BaseModel, ConfigDict, Field, ValidationError  # noqa: E402

from bigcontest.config import ROOT  # noqa: E402

PROMPT_DIR = ROOT / "configs" / "prompts"
_ROUTES = ("lookup", "investigate", "out_of_scope")
_CITIES = ("강남", "춘천")
_METRICS = ("체류인구", "카드결제", "주민등록인구", "1인세대비율", "시민생활지표")
_GROUPS = ("고령층", "중장년 남성 1인가구", "청년 남성")
_TOD = ("00_05", "06_11", "12_17", "18_23")


@lru_cache
def llm_config() -> dict:
    from bigcontest.contracts import load_yaml

    return load_yaml("llm.yaml")


def prompt_text(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def _api_key() -> str | None:
    """ANTHROPIC_API_KEY (환경 변수, 없으면 .env). 값은 어디에도 기록하지 않는다."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        m = re.search(r"^ANTHROPIC_API_KEY=(.*)$", env.read_text(encoding="utf-8"), re.M)
        if m and m.group(1).strip():
            return m.group(1).strip().strip("\"'")
    return None


def cost_usd(model_key: str, input_tokens: int, output_tokens: int) -> float | None:
    """configs/llm.yaml 의 공식 가격으로 비용. 가격이 없으면 None (추측하지 않음)."""
    m = llm_config()["models"].get(model_key) or {}
    pin, pout = m.get("input_usd_per_mtok"), m.get("output_usd_per_mtok")
    if pin is None or pout is None:
        return None
    if m.get("long_context_over_tokens") and input_tokens > m["long_context_over_tokens"]:
        pin, pout = m.get("long_input_usd_per_mtok"), m.get("long_output_usd_per_mtok")
        if pin is None or pout is None:
            return None
    return round((input_tokens * pin + output_tokens * pout) / 1e6, 6)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassifyRequest(_Out):
    city: Literal["강남", "춘천"] | None = None
    metric: Literal["체류인구", "카드결제", "주민등록인구", "1인세대비율", "시민생활지표"] | None = None
    group: Literal["고령층", "중장년 남성 1인가구", "청년 남성"] | None = None
    age_min: int | None = Field(default=None, ge=0, le=120)
    age_max: int | None = Field(default=None, ge=0, le=120)
    sex: Literal["남", "여"] | None = None
    time_of_day: Literal["00_05", "06_11", "12_17", "18_23"] | None = None
    adm_names: list[str] | None = None  # 동 이름 (스키마 enum = ref/adm_cd_names.csv). 규칙이 비운 칸만 씀
    period: str | None = None  # 기간 이름 (스키마 enum = periods.RELATIVE). 규칙이 비운 칸만 씀


class ClassifyOut(_Out):
    route: Literal["lookup", "investigate", "out_of_scope"]
    request: ClassifyRequest
    reason: str


class ToolsOut(_Out):
    tools: list[str]
    reason: str


class ExplainOut(_Out):
    sentences: list[str]


class PlanOut(_Out):
    tools: list[str]
    counter_checks: list[str]
    reason: str


class PickOut(_Out):
    key: str
    why: str


class InvestigateOut(_Out):
    picks: list[PickOut]
    reason: str


INTENTS = ("why_level", "resources", "compare", "data_limits", "other")


class IntentOut(_Out):
    intent: Literal["why_level", "resources", "compare", "data_limits", "other"]
    reason: str


def _investigate_schema(keys: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "picks": {"type": "array", "items": {
                "type": "object",
                "properties": {"key": {"type": "string", "enum": list(keys) or ["none"]}, "why": {"type": "string"}},
                "required": ["key", "why"], "additionalProperties": False}},
            "reason": {"type": "string"},
        },
        "required": ["picks", "reason"],
        "additionalProperties": False,
    }


INTENT_SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": list(INTENTS)}, "reason": {"type": "string"}},
    "required": ["intent", "reason"],
    "additionalProperties": False,
}


def _plan_schema(tools: list[str], checks: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "tools": {"type": "array", "items": {"type": "string", "enum": list(tools) or ["none"]}},
            "counter_checks": {"type": "array", "items": {"type": "string", "enum": list(checks) or ["none"]}},
            "reason": {"type": "string"},
        },
        "required": ["tools", "counter_checks", "reason"],
        "additionalProperties": False,
    }


def _nullable(enum: tuple | None = None, typ: str = "string") -> dict:
    base = {"type": typ} | ({"enum": list(enum)} if enum else {})
    return {"anyOf": [base, {"type": "null"}]}


def _dong_enum() -> list[str]:
    from bigcontest.agent.places import dongs

    return sorted({d.name for d in dongs()})


def _period_enum() -> list[str]:
    from bigcontest.agent.periods import RELATIVE

    return list(RELATIVE)


def classify_schema() -> dict:
    """질문 해석 스키마. 동 이름·기간 이름은 enum (사전·규칙 밖 값은 받지 않음)."""
    return {
        "type": "object",
        "properties": {
            "route": {"type": "string", "enum": list(_ROUTES)},
            "request": {
                "type": "object",
                "properties": {
                    "city": _nullable(_CITIES), "metric": _nullable(_METRICS), "group": _nullable(_GROUPS),
                    "age_min": _nullable(typ="integer"), "age_max": _nullable(typ="integer"),
                    "sex": _nullable(("남", "여")), "time_of_day": _nullable(_TOD),
                    "adm_names": {"anyOf": [{"type": "array", "items": {"type": "string", "enum": _dong_enum()}},
                                            {"type": "null"}]},
                    "period": _nullable(tuple(_period_enum())),
                },
                "required": ["city", "metric", "group", "age_min", "age_max", "sex", "time_of_day", "adm_names",
                             "period"],
                "additionalProperties": False,
            },
            "reason": {"type": "string"},
        },
        "required": ["route", "request", "reason"],
        "additionalProperties": False,
    }


CLASSIFY_SCHEMA = classify_schema()


def _tools_schema(available: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {"tools": {"type": "array", "items": {"type": "string", "enum": list(available) or ["none"]}},
                       "reason": {"type": "string"}},
        "required": ["tools", "reason"],
        "additionalProperties": False,
    }


EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {"type": "string"}}},
    "required": ["sentences"],
    "additionalProperties": False,
}


def card_payload(card: EvidenceCard, candidates: list[dict], max_candidates: int = 20) -> dict:
    """설명 LLM 에 넘기는 근거 (숫자는 근거 카드 그대로, 설명 문장에는 쓰지 못함)."""
    order = {"Level 3": 0, "Level 2": 1, "판단 보류": 2, "Level 1": 3, "Level 0": 4}
    cands = sorted(candidates, key=lambda c: (order.get(c.get("candidate_level") or "Level 0", 5), c["candidate_id"]))
    return {
        "as_of": str(card.as_of),
        "analysis_mode": card.analysis_mode,
        "facts": [f.model_dump(mode="json") for f in card.facts],
        "candidates": [{k: c.get(k) for k in ("candidate_id", "candidate_type", "city", "adm_cd", "check_first_adm",
                                              "analysis_cohort", "trigger", "candidate_level", "level_reason",
                                              "counter_result", "counter_reason", "input_status")}
                       for c in cands[:max_candidates]],
        "n_candidates": len(candidates),
        "limitations": card.limitations[:12],
    }


class AnthropicLLM:
    """Claude 연결. 출력은 JSON 스키마 구조화 출력 → Pydantic 검증. 오류·검증 실패는 LLMError (call_llm 이 1회 재시도
    뒤 규칙·템플릿으로 대신). 호출마다 `calls` 에 모델 ID·프롬프트 버전·토큰·비용·소요 시간을 남긴다."""

    def __init__(self, client=None):
        cfg = llm_config()
        self.cfg = cfg
        self.model_id = cfg["models"]["supervisor"]["id"]
        self.prompt_version = cfg["version"]
        self.calls: list[dict] = []
        self.last_reason: str | None = None  # 마지막 호출의 판단 이유
        if client is None:
            import anthropic

            key = _api_key()
            if not key:
                raise LLMInitError("LLM_NO_API_KEY", "ANTHROPIC_API_KEY 없음")
            client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=cfg.get("timeout_s", 60))
        self.client = client

    def _call(self, slot: str, user: dict | str, schema: dict, out_model: type[BaseModel]) -> BaseModel:
        import anthropic

        spec = self.cfg["slots"][slot]
        model_key = spec["model"]
        model = self.cfg["models"][model_key]["id"]
        rec = {"slot": slot, "model_id": model, "prompt_version": spec["prompt"], "input_tokens": 0,
               "output_tokens": 0, "cost_usd": None, "latency_ms": 0.0, "ok": False}
        self.calls.append(rec)
        content = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
        t0 = time.perf_counter()
        try:
            resp = self.client.messages.create(
                model=model,
                max_tokens=spec["max_tokens"],
                system=prompt_text(spec["prompt"]),
                messages=[{"role": "user", "content": content}],
                output_config={"effort": spec["effort"], "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APIStatusError as e:
            raise LLMError(f"API 오류 {e.status_code}") from None
        except anthropic.APIConnectionError:
            raise LLMError("API 연결 오류") from None
        finally:
            rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        usage = getattr(resp, "usage", None)
        rec["input_tokens"] = int(getattr(usage, "input_tokens", 0) or 0)
        rec["output_tokens"] = int(getattr(usage, "output_tokens", 0) or 0)
        rec["cost_usd"] = cost_usd(model_key, rec["input_tokens"], rec["output_tokens"])
        rec["stop_reason"] = resp.stop_reason
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise LLMError(f"응답 중단: {resp.stop_reason}")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
        if not text:
            raise LLMError("텍스트 응답 없음")
        try:
            out = out_model.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as e:
            raise LLMError(f"구조화 출력 검증 실패: {type(e).__name__}") from None
        rec["ok"] = True
        return out

    # ---- 세 자리
    def classify(self, question: str, request: dict | None) -> dict:
        from bigcontest.agent.guard import mask_pii

        masked, n = mask_pii(question)  # 개인정보는 국외 API 로 보내지 않음. 개수만 기록
        try:
            out = self._call("classify", {"question": masked, "rule_request": request}, CLASSIFY_SCHEMA, ClassifyOut)
        finally:
            self.calls[-1]["masked"] = n
        req = {k: v for k, v in out.request.model_dump().items() if v not in (None, [])}
        # enum 밖 값은 버림 (스키마가 막지만 한 번 더)
        if "adm_names" in req:
            req["adm_names"] = [n for n in req["adm_names"] if n in _dong_enum()] or None
        if req.get("period") not in (None, *_period_enum()):
            req["period"] = None
        req = {k: v for k, v in req.items() if v is not None}
        return {"route": out.route, "request": req or None, "reason": out.reason}

    def choose_extra_tools(self, request: dict | None, available: list[str], required: list[str]) -> list[str]:
        if not available:
            return []
        out = self._call("choose_extra_tools",
                         {"request": request, "available": available, "already_required": required},
                         _tools_schema(available), ToolsOut)
        self.last_reason = out.reason  # 도구 선택 이유를 버리지 않음 (call_llm 이 기록에 남김)
        return [t for t in out.tools if t in available]  # 화이트리스트 (nodes 에서 한 번 더 거름)

    def plan_more(self, memo: str, tools: list[str], counter_checks: list[str], candidates: list[dict]) -> dict:
        out = self._call("plan_more", {"memo": memo, "tools": tools, "counter_checks": counter_checks,
                                       "candidates": candidates}, _plan_schema(tools, counter_checks), PlanOut)
        self.last_reason = out.reason
        return {"tools": [t for t in out.tools if t in tools],
                "counter_checks": [c for c in out.counter_checks if c in counter_checks], "reason": out.reason}

    def investigate(self, questions: list[dict], candidates: list[dict], already: list[dict], max_picks: int) -> dict:
        if not questions:
            return {"picks": [], "reason": "고를 질문 없음"}
        keys = [q["key"] for q in questions]
        view = [{k: q.get(k) for k in ("key", "label", "question", "tool", "candidate_ids")} for q in questions]
        out = self._call("investigate", {"questions": view, "candidates": candidates, "already": already,
                                         "max_picks": max_picks}, _investigate_schema(keys), InvestigateOut)
        self.last_reason = out.reason
        picks = [{"key": p.key, "why": p.why} for p in out.picks if p.key in keys]  # 노드에서 한 번 더 거름
        return {"picks": picks[:max_picks], "reason": out.reason}

    def followup_intent(self, question: str) -> dict:
        from bigcontest.agent.guard import mask_pii

        masked, n = mask_pii(question)
        try:
            out = self._call("followup_intent", {"question": masked}, INTENT_SCHEMA, IntentOut)
        finally:
            self.calls[-1]["masked"] = n
        self.last_reason = out.reason
        return {"intent": out.intent, "reason": out.reason}

    def followup(self, question: str, payload: dict) -> list[str]:
        from bigcontest.agent.guard import mask_pii

        masked, n = mask_pii(question)
        try:
            out = self._call("followup", {**payload, "question": masked}, EXPLAIN_SCHEMA, ExplainOut)
        finally:
            self.calls[-1]["masked"] = n
        lines = [x.strip().replace("\n", " ") for x in out.sentences if x.strip()]
        return lines[: self.cfg["slots"]["followup"].get("max_sentences", 3)]

    def explain(self, card: EvidenceCard, candidates: list[dict], attempt: int, brief: bool = False,
                feedback: list[str] | None = None) -> list[str]:
        slot = "brief" if brief else "explain"
        payload = card_payload(card, candidates)
        if attempt > 1:
            payload["note"] = "지난 설명이 Guard 검사를 통과하지 못했다. 숫자 없이, 입력의 근거 번호만 인용해 다시 쓴다."
            payload["guard_feedback"] = [f[:300] for f in (feedback or [])[:3]]  # 걸린 규칙과 문장
        out = self._call(slot, payload, EXPLAIN_SCHEMA, ExplainOut)
        lines = [s.strip().replace("\n", " ") for s in out.sentences if s.strip()]
        lines = lines[: self.cfg["slots"][slot].get("max_sentences", 4)]
        kept = sanitize_sentences(lines, card, candidates)
        self.calls[-1]["dropped"] = len(lines) - len(kept)
        self.calls[-1]["drafted"] = len(lines)
        if len(kept) < len(lines):  # 뺀 문장의 이유 코드 (AG-09). 문장 원문은 남기지 않음
            from bigcontest.agent import guard as G

            self.calls[-1]["dropped_reasons"] = sorted({_reason_code(w) for s in lines if s not in kept
                                                         for w in G.narrative_problems(s, card, candidates)})
        return kept


def _reason_code(why: str) -> str:
    """narrative_problems 사유 → 짧은 이유 코드 (숫자 / 수량 말 / 방향 / 도시 / 지역 / 동 / 근거 ID / 후보 주장)."""
    for code, pat in (("UNKNOWN_ID", "근거 카드에 없는 근거 ID"), ("NUMBER", "숫자"), ("QUANTITY", "수량"),
                      ("DIRECTION", "방향"), ("CITY", "도시"), ("REGION", "지역 이름"), ("DONG", "동 이름")):
        if pat in why:
            return code
    return "CANDIDATE_CLAIM"


def sanitize_sentences(lines: list[str], card: EvidenceCard, candidates: list | None = None) -> list[str]:
    """설명 문장 중 Guard 사실 검사(G-05)에 걸릴 문장은 미리 뺀다: 새 숫자·한글 수량 말·카드에 없는 근거 ID·
    인용 근거와 다른 방향·도시. Guard 와 같은 함수(guard.narrative_problems)를 쓴다 (Guard 가 다시 확인)."""
    from bigcontest.agent import guard as G

    return [s for s in lines if not G.narrative_problems(s, card, candidates)]
