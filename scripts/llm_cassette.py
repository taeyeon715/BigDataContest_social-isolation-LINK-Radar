"""Gemini 응답 녹화 (AG-10). 실제 API 를 부르므로 프롬프트 판을 올릴 때만 손으로 돌린다 (테스트는 재생만, 무료 등급).

실행: uv run python scripts/llm_cassette.py record [--max-cost 0.3]
- 가짜 도구(stub) + 고정 기준일로 녹화 묶음(SMOKE)을 돌리며 `record:` LLM 으로 응답을 tests/fixtures/llm_cassettes/smoke_v1.json 에 저장
- 녹화본에는 열쇠(입력 해시)·모델·응답 글·토큰 수만 들어간다. API 키·질문 원문은 넣지 않는다
- 비용 합이 --max-cost 를 넘으면 그 뒤 호출은 예산 초과로 막힌다 (budget.run_budget)
- 이미 녹화한 요청은 다시 부르지 않는다 (바뀐 프롬프트·입력만 비용). 끝나면 안 쓴 옛 응답을 지운다
- 끝나면 호출 수·토큰·비용(USD, configs/llm.yaml 공식 유료 가격. 무료 등급이라 실제 청구는 없음)을 찍는다. 재사용한 응답의 토큰·비용도 합에 들어가므로
  실제로 새로 쓴 비용은 이번 녹화의 새 응답 몫이다. 키 값은 찍지 않는다

재생: 상태의 llm 값을 `replay:tests/fixtures/llm_cassettes/smoke_v1.json` 으로 (tests/test_llm_replay.py, scripts/eval_agent.py)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CASSETTE = "tests/fixtures/llm_cassettes/smoke_v1.json"
AS_OF = "2025-09-26"
# 녹화 묶음: 조사 1개(질문 해석·도구 선택·조사 고르기·설명), 조회 1개, 이어 묻기 3개(종류별)
SMOKE_RUNS = [
    ("smoke-inv", "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?", AS_OF),
    ("smoke-lookup", "강남 1인세대 비율 상위 동은?", "2025-10-20"),
]
SMOKE_FOLLOWUPS = ["남면 후보는 왜 이 등급이야?", "그 동네에 연결할 자원은?", "담당자에게 한 줄로 알려 줘"]


def run_smoke(llm: str, db_dir: Path) -> list[dict]:
    """녹화·재생이 같은 요청을 만들도록 같은 순서로 돈다. 상태 값 목록 (이어 묻기 결과는 llm_log 와 함께 뒤에)."""
    from bigcontest.agent.graph import Agent

    agent = Agent(db_dir / "smoke.db", tools_backend="stub")
    out = []
    try:
        for rid, q, as_of in SMOKE_RUNS:
            out.append(agent.start(rid, "qa", as_of, question=q, llm=llm)["values"])
        fus = [agent.followup("smoke-inv", q, llm=llm) for q in SMOKE_FOLLOWUPS]
    finally:
        agent.close()
    return out + [{"followup": f} for f in fus]


def usage(results: list[dict]) -> dict:
    logs = [r for v in results for r in (v.get("llm_log") or (v.get("followup") or {}).get("llm_log") or [])]
    return {"calls": sum(int(r.get("api_calls") or 0) for r in logs),
            "input_tokens": sum(int(r.get("input_tokens") or 0) for r in logs),
            "output_tokens": sum(int(r.get("output_tokens") or 0) for r in logs),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in logs), 4),
            "failed": sum(1 for r in logs if not r.get("ok"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["record"])
    ap.add_argument("--max-cost", type=float, default=0.3)
    ap.add_argument("--out", default=CASSETTE)
    args = ap.parse_args()
    from bigcontest.agent.budget import run_budget
    from bigcontest.agent.cassette import client_for

    name = f"record:{args.out}"
    with tempfile.TemporaryDirectory() as d, run_budget(max_calls=40, max_cost_usd=args.max_cost):
        results = run_smoke(name, Path(d))
    client = client_for(name)
    pruned = client.prune()
    u = usage(results)
    print(f"녹화: {args.out} (재사용 {client.reused}건, 지운 옛 응답 {pruned}건)")
    n, ins, outs, cost = client.new_cost()
    print(f"이번에 새로 부른 API {n}회 · 입력 {ins} 토큰 · 출력 {outs} 토큰 · 비용 ${cost}")
    print(f"녹화 묶음 전체(재사용 포함): 호출 {u['calls']}회 · 입력 {u['input_tokens']} · 출력 {u['output_tokens']} · "
          f"${u['cost_usd']} · 실패 {u['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
