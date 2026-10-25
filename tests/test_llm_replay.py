"""녹화한 Gemini 응답 재생 (AG-10). 키·네트워크·비용 없이 실제 Gemini 응답 모양으로 그래프를 돌린다.

녹화: uv run python scripts/llm_cassette.py record (프롬프트 판을 올릴 때만, 무료 등급)
- 녹화 묶음(조사 1개·조회 1개·이어 묻기 3개)을 재생하면 녹화에 없는 요청이 0 (프롬프트·입력이 그대로)
- 등급·순서·반증은 LLM 없이 돌린 것과 같음
- 실제 Gemini 가 고른 조사 질문·이유가 보고서 "추가로 확인한 것"에 들어감
- 녹화본에 API 키·질문 원문이 없음
"""

import json
import sys
from pathlib import Path

import pytest

from bigcontest.agent.cassette import ReplayClient, client_for, request_key
from bigcontest.agent.evaluation import explanation_metrics
from bigcontest.config import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
from llm_cassette import CASSETTE, SMOKE_FOLLOWUPS, SMOKE_RUNS, run_smoke  # noqa: E402

PATH = ROOT / CASSETTE


@pytest.fixture(scope="module")
def replayed(tmp_path_factory):
    name = f"replay:{CASSETTE}"
    res = run_smoke(name, tmp_path_factory.mktemp("rp"))
    return res, client_for(name)


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    return run_smoke("none", tmp_path_factory.mktemp("rp0"))


def _levels(v: dict) -> dict:
    return {c["candidate_id"]: (c["candidate_level"], c["counter_result"], c.get("queue_rank"))
            for c in v.get("candidates") or []}


def test_cassette_has_no_secrets_or_questions():
    raw = PATH.read_text(encoding="utf-8")
    assert "AIza" not in raw and "GEMINI_API_KEY" not in raw  # Google API 키 모양·이름 없음
    for _, q, _ in SMOKE_RUNS:
        assert q not in raw
    data = json.loads(raw)
    for e in data["entries"].values():
        assert set(e) <= {"model", "system_sha", "text", "input_tokens", "output_tokens", "stop_reason"}


def test_replay_has_no_misses(replayed):
    res, client = replayed
    assert client.misses == 0 and client.hits == len(json.loads(PATH.read_text(encoding="utf-8"))["entries"])
    logs = [r for v in res for r in (v.get("llm_log") or (v.get("followup") or {}).get("llm_log") or [])]
    assert logs and all(r["ok"] for r in logs)
    assert all(r["model_id"].startswith("gemini-") for r in logs)
    assert {r["slot"] for r in logs} >= {"classify", "choose_extra_tools", "investigate", "explain", "followup"}


def test_replay_levels_same_as_no_llm(replayed, plain):
    res, _ = replayed
    for a, b in zip(res[: len(SMOKE_RUNS)], plain[: len(SMOKE_RUNS)]):
        assert _levels(a) == _levels(b)
        assert a["route"] == b["route"]


def test_replay_investigation_picked_by_gemini(replayed):
    res, _ = replayed
    inv = res[0]["investigation"]
    assert inv["rounds"] and inv["rounds"][0]["source"] == "llm"
    assert all(i["why"] for i in inv["items"])
    rep = res[0]["report"]
    assert rep.get("narrative")  # 설명 문장이 Guard 를 지남


def test_replay_followups_answered_with_intent(replayed):
    res, _ = replayed
    fus = [v["followup"] for v in res[len(SMOKE_RUNS):]]
    assert len(fus) == len(SMOKE_FOLLOWUPS)
    assert [f["intent"] for f in fus] == ["why_level", "resources", "other"]
    assert all(f["status"] == "answered" for f in fus)


def test_replay_metrics(replayed):
    res, _ = replayed
    states = [v for v in res if "followup" not in v]
    m = explanation_metrics(states, [v["followup"] for v in res if "followup" in v])
    assert m["narrative_sentences"] > 0 and m["first_pass_guard_rate"] is not None
    assert 0 <= m["citation_coverage"] <= 1
    assert m["target_faithfulness"] is None or m["target_faithfulness"] >= 0.5


def test_miss_raises_llm_error(tmp_path):
    from bigcontest.agent.llm import LLMError

    c = ReplayClient(PATH)
    with pytest.raises(LLMError):
        c.create(model="gemini-3.5-flash-lite", system="다른 프롬프트", messages=[{"role": "user", "content": "x"}])
    assert c.misses == 1
    assert request_key("m", "s", [{"content": "a"}]) != request_key("m", "s", [{"content": "b"}])
