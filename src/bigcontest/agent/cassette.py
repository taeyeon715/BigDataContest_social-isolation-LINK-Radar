"""Claude 응답 녹화·재생 (AG-10). 실제 호출은 한 번만 녹화하고, 테스트는 녹화본을 재생한다 (비용 0, 결과 고정).

- 상태의 `llm` 값이 `replay:<파일>` 이면 `ReplayClient`, `record:<파일>` 이면 `RecordingClient` 를 AnthropicLLM 에 끼운다
  (llm.get_llm). 그래서 프롬프트·구조화 출력·Pydantic 검증·sanitize 는 실제 Claude 때와 같은 코드를 지난다
- 열쇠: 모델 ID + 시스템 프롬프트 + 사용자 입력(JSON)의 SHA-256. 프롬프트 판이나 입력이 바뀌면 녹화에 없음(miss) →
  LLMError → call_llm 이 규칙·템플릿으로 대신 (테스트는 miss 수를 봄). 프롬프트 판을 올리면 다시 녹화
- 녹화본에는 열쇠·모델·프롬프트 해시·응답 글·토큰 수·stop_reason 만 둔다. API 키·요청 원문(질문)은 넣지 않는다
- 다시 녹화할 때 이미 있는 열쇠는 API 를 부르지 않고 재사용하고(바뀐 요청만 비용), 끝나면 안 쓴 열쇠를 지운다(prune)
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bigcontest.config import ROOT


def _path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


def request_key(model: str, system: str, messages: list[dict]) -> str:
    content = messages[-1]["content"] if messages else ""
    raw = json.dumps({"model": model, "system": system, "user": content}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _response(entry: dict) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=entry["text"])],
        usage=SimpleNamespace(input_tokens=entry.get("input_tokens", 0), output_tokens=entry.get("output_tokens", 0)),
        stop_reason=entry.get("stop_reason", "end_turn"),
    )


def load(path: str | Path) -> dict:
    p = _path(str(path))
    if not p.exists():
        return {"version": 1, "entries": {}}
    return json.loads(p.read_text(encoding="utf-8"))


class ReplayClient:
    """녹화본에서 응답을 돌려주는 가짜 anthropic 클라이언트. 네트워크·키 없음."""

    def __init__(self, path: str | Path):
        self.path = _path(str(path))
        self.data = load(self.path)
        self.hits = 0
        self.misses = 0
        self.messages = self

    def create(self, *, model: str, system: str, messages: list[dict], **_: Any) -> Any:
        from bigcontest.agent.llm import LLMError

        entry = self.data["entries"].get(request_key(model, system, messages))
        if entry is None:
            self.misses += 1
            raise LLMError("녹화에 없는 요청 (프롬프트·입력이 바뀜, 다시 녹화 필요)")
        self.hits += 1
        return _response(entry)


class RecordingClient:
    """실제 anthropic 클라이언트를 감싸 응답을 녹화한다 (scripts/llm_cassette.py record 가 씀)."""

    _lock = threading.Lock()

    def __init__(self, path: str | Path, inner: Any | None = None):
        self.path = _path(str(path))
        self.data = load(self.path)
        if inner is None:
            import anthropic

            from bigcontest.agent.llm import LLMInitError, _api_key, llm_config

            key = _api_key()
            if not key:
                raise LLMInitError("LLM_NO_API_KEY", "ANTHROPIC_API_KEY 없음")
            inner = anthropic.Anthropic(api_key=key, max_retries=0, timeout=llm_config().get("timeout_s", 60))
        self.inner = inner
        self.messages = self
        self.used: set[str] = set()  # 이번 녹화에서 쓴 열쇠 (prune 이 나머지를 지움)
        self.new_keys: list[str] = []  # 이번에 실제로 API 를 불러 새로 녹화한 열쇠
        self.reused = 0

    def create(self, *, model: str, system: str, messages: list[dict], **kw: Any) -> Any:
        key = request_key(model, system, messages)
        self.used.add(key)
        if key in self.data.get("entries", {}):  # 이미 녹화한 요청은 다시 부르지 않음 (비용 0)
            self.reused += 1
            return _response(self.data["entries"][key])
        resp = self.inner.messages.create(model=model, system=system, messages=messages, **kw)
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        usage = getattr(resp, "usage", None)
        entry = {"model": model, "system_sha": hashlib.sha256(system.encode("utf-8")).hexdigest()[:12], "text": text,
                 "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                 "output_tokens": int(getattr(usage, "output_tokens", 0) or 0), "stop_reason": resp.stop_reason}
        with self._lock:
            self.data.setdefault("entries", {})[key] = entry
            self.new_keys.append(key)
            self.data["version"] = 1
            self.data["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        return resp


    def new_cost(self) -> tuple[int, int, int, float | None]:
        """이번에 새로 녹화한 응답의 (호출 수, 입력 토큰, 출력 토큰, 비용 USD). 비용은 configs/llm.yaml 공식 가격."""
        from bigcontest.agent.llm import cost_usd, llm_config

        key_of = {m["id"]: k for k, m in llm_config()["models"].items()}
        ins = outs = 0
        cost: float | None = 0.0
        for k in self.new_keys:
            e = self.data["entries"][k]
            ins, outs = ins + e["input_tokens"], outs + e["output_tokens"]
            c = cost_usd(key_of.get(e["model"], ""), e["input_tokens"], e["output_tokens"]) if e["model"] in key_of else None
            cost = None if cost is None or c is None else cost + c
        return len(self.new_keys), ins, outs, (round(cost, 4) if cost is not None else None)

    def prune(self) -> int:
        """이번 녹화에서 쓰지 않은 열쇠(바뀐 프롬프트·입력의 옛 응답)를 지움. 지운 수."""
        old = set(self.data.get("entries", {})) - self.used
        for k in old:
            del self.data["entries"][k]
        if old:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        return len(old)


_CLIENTS: dict[str, Any] = {}


def client_for(name: str) -> Any:
    """`replay:<파일>` / `record:<파일>` → 클라이언트 (같은 이름이면 같은 객체: hits·misses 를 모아 셈)."""
    kind, _, path = name.partition(":")
    if name not in _CLIENTS:
        _CLIENTS[name] = ReplayClient(path) if kind == "replay" else RecordingClient(path)
    return _CLIENTS[name]
