"""사용자별 실행 만들기 한도 (간단한 메모리 창).

`POST /runs`·`POST /qa`·`POST /runs/{id}/followup` 은 그래프·LLM 비용을 쓰므로 사용자(감사 actor)마다
창(초) 안에 몇 번까지만 받는다. 넘으면 429 RATE_LIMITED + `Retry-After`.

- 설정: `LINKRADAR_RUN_RATE` = "<횟수>/<초>". prod 기본 20/600 (10분에 20번), dev 는 이 값을 줄 때만 켬. "0" 이면 끔
- 프로세스 메모리에만 둔다 (워커 1개 전제, api/app.py 운영 메모). 다시 켜면 비워진다
- 실행 하나 안의 LLM 호출 수·비용 한도는 configs/llm.yaml `budget` (bigcontest/agent/budget.py)
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Callable

from api.errors import ApiError

DEFAULT_RATE = "20/600"


def parse_rate(text: str | None) -> tuple[int, float] | None:
    """"20/600" → (20, 600.0). "0"·빈 값이면 None (끔)."""
    text = (text or "").strip()
    if not text or text == "0":
        return None
    n, _, sec = text.partition("/")
    count, window = int(n), float(sec or 60)
    if count <= 0 or window <= 0:
        return None
    return count, window


class RateLimiter:
    def __init__(self, count: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.count = count
        self.window = window_s
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        """한 번 셈. 한도를 넘으면 429 (넘은 요청은 세지 않음)."""
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.count:
                wait = max(1, math.ceil(q[0] + self.window - now))
                err = ApiError(429, "RATE_LIMITED",
                               f"요청이 너무 잦습니다. {wait}초 뒤에 다시 시도하세요 "
                               f"({int(self.window)}초에 {self.count}번까지).")
                err.headers = {"Retry-After": str(wait)}  # type: ignore[attr-defined]
                raise err
            q.append(now)
