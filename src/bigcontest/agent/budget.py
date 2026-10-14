"""실행 하나의 LLM 예산 (호출 수·비용 한도).

- 한도: configs/llm.yaml `budget` (max_calls_per_run, max_cost_usd_per_run). 값이 없으면 한도 없음
- API(api/services/agent_runs.py)가 그래프를 돌리거나 이어 묻기를 할 때 `run_budget(...)` 으로 이 실행의 예산을
  켠다. 이미 쓴 호출 수·비용(llm_calls 표 합)에서 시작한다
- `call_llm`(llm.py)이 부르기 전에 `check()` 를 보고, 넘었으면 LLM 을 부르지 않고 규칙·템플릿으로 대신한다.
  기록에는 error_code `BUDGET_EXCEEDED` 를 남긴다 (llm_calls 표·decision_trace 에서 보임)
- 예산이 켜져 있지 않으면(그래프를 직접 돌리는 테스트·스크립트) 아무것도 막지 않는다
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

    def check(self) -> str | None:
        """넘었으면 사유 (아니면 None)."""
        if self.max_calls is not None and self.calls >= self.max_calls:
            return f"실행당 LLM 호출 {self.max_calls}회 한도"
        if self.max_cost_usd is not None and self.cost_usd >= self.max_cost_usd:
            return f"실행당 LLM 비용 ${self.max_cost_usd} 한도"
        return None

    def add(self, record: dict) -> None:
        self.calls += 1
        self.cost_usd += float(record.get("cost_usd") or 0.0)


_CURRENT: ContextVar[Budget | None] = ContextVar("linkradar_llm_budget", default=None)


def limits() -> tuple[int | None, float | None]:
    from bigcontest.agent.llm import llm_config

    b = llm_config().get("budget") or {}
    calls, cost = b.get("max_calls_per_run"), b.get("max_cost_usd_per_run")
    return (int(calls) if calls is not None else None, float(cost) if cost is not None else None)


def current() -> Budget | None:
    return _CURRENT.get()


@contextmanager
def run_budget(used_calls: int = 0, used_cost: float = 0.0, max_calls: int | None = None,
               max_cost_usd: float | None = None) -> Iterator[Budget]:
    """이 블록 안의 call_llm 에 예산을 씌움. 한도를 주지 않으면 configs/llm.yaml 값."""
    if max_calls is None and max_cost_usd is None:
        max_calls, max_cost_usd = limits()
    b = Budget(max_calls, max_cost_usd, used_calls, used_cost)
    token = _CURRENT.set(b)
    try:
        yield b
    finally:
        _CURRENT.reset(token)
