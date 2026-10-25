"""에이전트 품질 지표 표 (AG-08~10). 비용 0: 가짜 도구 + LLM 없음 / 가짜 LLM / 녹화한 Gemini 응답 재생.

실행: uv run python scripts/eval_agent.py [--llm none fake replay]
- 골든 질문 20개: 경로·대상·끝점 정확도. 이어 묻기 골든 15개: 종류·상태 정확도
- 설명 지표: 인용률, 1회 Guard 통과율, 대상 충실도, Guard 전 제거율
- Guard 처치 사례(G-11 폐기 지표, G-06 반증 시간 초과)의 신호 수집 도구 호출 수
- 녹화 재생(replay)은 tests/fixtures/llm_cassettes/smoke_v1.json 이 있을 때만, 녹화 묶음(SMOKE)으로 계산
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

# 예전 그래프(Guard REVISE → 전체 다시 수집, HEAD 685d4da)에서 같은 가짜 도구·가짜 LLM 으로 잰 값
BEFORE = {"deprecated_metric": 27, "confounder_timeout_once": 18, "confounder_timeout_always": 27}


def remedy_table() -> str:
    from bigcontest.agent.graph import build_graph
    from bigcontest.agent.state import initial_state

    rows = ["| 사례 | 처치 | 신호 수집 도구 (전) | 신호 수집 도구 (후) | 조사 루프 도구 | 끝 |", "|---|---|---|---|---|---|"]
    for sc, remedy in (("deprecated_metric", "drop_evidence"), ("confounder_timeout_once", "retry_failed_tool"),
                       ("confounder_timeout_always", "retry_failed_tool → abstain")):
        out = build_graph().invoke(initial_state("ev", "monitoring", "2025-09-26", tools_backend="stub", scenario=sc,
                                                 llm="fake"), {"recursion_limit": 100})
        tt = out["tool_timings"]
        sig = sum(1 for t in tt if t["tool"] != "find_resources" and t.get("phase") != "investigate")
        inv = sum(1 for t in tt if t.get("phase") == "investigate")
        end = "보류" if (out.get("report") or {}).get("is_abstain") else "통과"
        rows.append(f"| {sc} | {remedy} | {BEFORE[sc]} | {sig} | {inv} | {end} |")
    return "\n".join(rows)


def replay_metrics(path: str) -> dict:
    from bigcontest.agent.cassette import client_for
    from bigcontest.agent.evaluation import explanation_metrics
    from llm_cassette import run_smoke

    name = f"replay:{path}"
    with tempfile.TemporaryDirectory() as d:
        res = run_smoke(name, Path(d))
    states = [v for v in res if "followup" not in v]
    fus = [v["followup"] for v in res if "followup" in v]
    m = explanation_metrics(states, fus)
    c = client_for(name)
    m |= {"golden_n": "-", "replay_hits": c.hits, "replay_misses": c.misses,
          "followup_answered": sum(f["status"] == "answered" for f in fus)}
    return m


def main() -> int:
    from bigcontest.agent.evaluation import run_golden, table

    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", nargs="*", default=["none", "fake", "replay"])
    args = ap.parse_args()
    ms: dict[str, dict] = {}
    for llm in args.llm:
        if llm == "replay":
            path = "tests/fixtures/llm_cassettes/smoke_v1.json"
            if (ROOT / path).exists():
                ms["gemini(재생)"] = replay_metrics(path)
            continue
        ms[llm] = run_golden(llm)["metrics"]
    print("## 품질 지표\n")
    print(table(ms))
    if "gemini(재생)" in ms:
        m = ms["gemini(재생)"]
        print(f"\n녹화 재생: 맞음 {m['replay_hits']} · 녹화에 없음 {m['replay_misses']} · "
              f"Guard 전에 뺀 이유 {m.get('dropped_reason_codes') or '-'}")
    print("\n## Guard 처치 전후 (G-11·G-06, 가짜 도구 + 가짜 LLM)\n")
    print(remedy_table())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
