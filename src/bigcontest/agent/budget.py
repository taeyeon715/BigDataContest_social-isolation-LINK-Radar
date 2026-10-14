"""실행 하나의 예산 (LLM 호출 수·비용, 도구 시간 한도).

- 한도: configs/llm.yaml `budget` (max_calls_per_run, max_cost_usd_per_run, max_tool_ms_per_run). 값이 없으면 한도 없음
- API(api/services/agent_runs.py)가 그래프를 돌리거나 이어 묻기를 할 때 `run_budget(...)` 으로 이 실행의 예산을
  켠다. 이미 쓴 호출 수·비용(llm_calls 표 합)에서 시작한다
- `call_llm`(llm.py)이 부르기 전에 `check()` 를 보고, 넘었으면 LLM 을 부르지 않고 규칙·템플릿으로 대신한다.
  기록에는 error_code `BUDGET_EXCEEDED` 를 남긴다 (llm_calls 표·decision_trace 에서 보임)
- 예산이 켜져 있지 않으면(그래프를 직접 돌리는 테스트·스크립트) call_llm 은 막지 않는다
- 도구 시간: `call_tool` 이 켜진 예산에 도구 ms 를 더한다. 조사 루프는 `state_check` 로 상태(llm_log·tool_timings)와
  켜진 예산을 함께 보고, LLM 한도를 넘으면 규칙으로 고르고, 도구 시간 한도를 넘으면 조사를 멈춘다(템플릿 문장).
  필수 근거 도구는 등급 입력이라 예산과 무관하게 항상 돈다
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


@dataclass
class Budget:
    max_calls: int | None
    max_cost_usd: float | None
    calls: int = 0
    cost_usd: float = 0.0
    max_tool_ms: float | None = None
    tool_ms: float = 0.0

    def check(self) -> str | None:
        """넘었으면 사유 (아니면 None)."""
        if self.max_calls is not None and self.calls >= self.max_calls:
            return f"실행당 LLM 호출 {self.max_calls}회 한도"
        if self.max_cost_usd is not None and self.cost_usd >= self.max_cost_usd:
            return f"실행당 LLM 비용 ${self.max_cost_usd} 한도"
        return None

    def check_tools(self) -> str | None:
        """도구 시간 한도를 넘었으면 사유 (조사 루프·다시 시도만 이 값을 본다)."""
        if self.max_tool_ms is not None and self.tool_ms >= self.max_tool_ms:
            return f"실행당 도구 시간 {self.max_tool_ms / 1000:g}초 한도"
        return None

    def add(self, record: dict) -> None:
        self.calls += 1
        self.cost_usd += float(record.get("cost_usd") or 0.0)

    def add_tool_ms(self, ms: float) -> None:
        self.tool_ms += float(ms or 0.0)


_CURRENT: ContextVar[Budget | None] = ContextVar("linkradar_llm_budget", default=None)


def limits() -> tuple[int | None, float | None]:
    from bigcontest.agent.llm import llm_config

    b = llm_config().get("budget") or {}
    calls, cost = b.get("max_calls_per_run"), b.get("max_cost_usd_per_run")
    return (int(calls) if calls is not None else None, float(cost) if cost is not None else None)


def tool_limit_ms() -> float | None:
    from bigcontest.agent.llm import llm_config

    v = (llm_config().get("budget") or {}).get("max_tool_ms_per_run")
    return float(v) if v is not None else None


def current() -> Budget | None:
    return _CURRENT.get()


def state_check(state: dict) -> tuple[str | None, str | None]:
    """그래프 상태로 본 예산: (LLM 초과 사유, 도구 시간 초과 사유). 넘지 않았으면 None.
    LLM 은 상태 llm_log 의 호출 수·비용 합, 도구는 tool_timings ms 합. 켜진 실행 예산(run_budget)도 함께 본다."""
    calls_max, cost_max = limits()
    log = [r for r in state.get("llm_log") or [] if r.get("slot") != "init" and r.get("error_code") != BUDGET_EXCEEDED]
    calls, cost = len(log), sum(float(r.get("cost_usd") or 0.0) for r in log)
    llm = None
    if calls_max is not None and calls >= calls_max:
        llm = f"실행당 LLM 호출 {calls_max}회 한도"
    elif cost_max is not None and cost >= cost_max:
        llm = f"실행당 LLM 비용 ${cost_max} 한도"
    tool_max = tool_limit_ms()
    ms = sum(float(t.get("ms") or 0.0) for t in state.get("tool_timings") or [])
    tool = f"실행당 도구 시간 {tool_max / 1000:g}초 한도" if tool_max is not None and ms >= tool_max else None
    b = current()
    if b is not None:
        llm = llm or b.check()
        tool = tool or b.check_tools()
    return llm, tool


@contextmanager
def run_budget(used_calls: int = 0, used_cost: float = 0.0, max_calls: int | None = None,
               max_cost_usd: float | None = None, max_tool_ms: float | None = None) -> Iterator[Budget]:
    """이 블록 안의 call_llm·call_tool 에 예산을 씌움. 한도를 주지 않으면 configs/llm.yaml 값."""
    if max_calls is None and max_cost_usd is None:
        max_calls, max_cost_usd = limits()
    b = Budget(max_calls, max_cost_usd, used_calls, used_cost,
               max_tool_ms=max_tool_ms if max_tool_ms is not None else tool_limit_ms())
    token = _CURRENT.set(b)
    try:
        yield b
    finally:
        _CURRENT.reset(token)
