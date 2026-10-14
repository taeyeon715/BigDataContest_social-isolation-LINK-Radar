"""에이전트 품질 평가 묶음 (AG-08~10). 기본은 가짜 도구 + LLM 없음/가짜/녹화 재생이라 비용 0, 결과 고정.

- 골든 질문(tests/eval/golden.yaml): 질문 → 기대 경로·대상(도시·동·집단)·끝점. 지표: 경로 정확도, 대상 정확도, 끝점 정확도
- 이어 묻기 골든: 질문 → 기대 종류(intent)·상태(answered / NO_TARGET / NO_DATA)
- 설명 지표 (보고서·이어 묻기 상태에서 코드로 계산):
  - 인용률: LLM 설명 문장 중 근거 ID 를 하나 이상 인용한 비율
  - 1회 Guard 통과율: 첫 보고서가 사실·표현 검사를 한 번에 지난 비율
  - 대상 충실도: 후보 ID 를 말한 설명 문장이 인용한 근거가 그 후보의 근거(evidence_ids)이거나 동 없는 도시 근거인 비율
  - 사전 제거율: LLM 이 쓴 설명 문장 중 Guard 전에 뺀 비율 (이유 코드는 llm_log.dropped_reasons)
- `scripts/eval_agent.py` 가 표로 찍는다
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from bigcontest.config import ROOT

GOLDEN = ROOT / "tests" / "eval" / "golden.yaml"
_EV = re.compile(r"EV-[0-9A-Za-z\-]+")
_CID = re.compile(r"C-\d{8}-\d{3}")


def load_golden(path: str | Path = GOLDEN) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _endpoint(st: dict) -> str:
    if st.get("waiting"):
        return "await_approval"
    path = (st.get("values") or {}).get("path") or []
    return path[-1] if path else "-"


def run_case(agent, case: dict, run_id: str, llm: str, default_as_of: str) -> tuple[dict, dict]:
    """골든 질문 하나 → (결과 행, 상태 값)."""
    st = agent.start(run_id, "qa", case.get("as_of") or default_as_of, question=case["question"], llm=llm)
    v = st["values"]
    req = v.get("request") or {}
    want = case["expect"]
    got = {"route": v.get("route"), "city": req.get("city"), "adm_cds": sorted(req.get("adm_cds") or []),
           "group": req.get("group"), "endpoint": _endpoint(st)}
    target_ok = got["city"] == want.get("city") and got["adm_cds"] == sorted(want.get("adm_cds") or [])
    if "group" in want:
        target_ok = target_ok and got["group"] == want["group"]
    if want.get("adm_cds") and v.get("route") == "investigate" and v.get("candidates"):
        # 조사 경로: 남은 후보가 모두 질문한 동(또는 먼저 볼 동)의 후보인가
        adms = set(want["adm_cds"])
        target_ok = target_ok and all(adms & {c.get("adm_cd"), *(c.get("check_first_adm") or [])}
                                      for c in v["candidates"])
    row = {"id": case["id"], "question": case["question"], "want": want, "got": got,
           "route_ok": got["route"] == want["route"], "target_ok": target_ok,
           "endpoint_ok": got["endpoint"] == want["endpoint"]}
    return row, v


def run_golden(llm: str = "none", backend: str = "stub", golden: dict | None = None,
               db_dir: str | Path | None = None) -> dict[str, Any]:
    """골든 질문 전부 + 이어 묻기 골든. {rows, followups, states, metrics}."""
    from bigcontest.agent.graph import Agent

    golden = golden or load_golden()
    tmp = Path(db_dir or tempfile.mkdtemp(prefix="golden-"))
    agent = Agent(tmp / "golden.db", tools_backend=backend)
    rows, states = [], []
    try:
        for i, case in enumerate(golden["cases"]):
            row, v = run_case(agent, case, f"gold-{i:02d}", llm, golden["as_of"])
            rows.append(row)
            states.append(v)
        fu = golden.get("followups") or {}
        f_rows = []
        if fu:
            agent.start("gold-fu", "qa", fu.get("as_of") or golden["as_of"], question=fu["run_question"], llm=llm)
            for case in fu["cases"]:
                out = agent.followup("gold-fu", case["q"])
                code = (out.get("abstain_reason") or "").split(":")[0] if out["status"] == "abstained" else "answered"
                f_rows.append({"q": case["q"], "want_intent": case["intent"], "got_intent": out.get("intent"),
                               "want_status": case["status"], "got_status": code,
                               "intent_ok": out.get("intent") == case["intent"], "status_ok": code == case["status"],
                               "contains_ok": all(w in (out.get("answer") or "") for w in case.get("contains") or []),
                               "out": out})
    finally:
        agent.close()
    n = max(1, len(rows))
    metrics = {
        "golden_n": len(rows),
        "route_accuracy": sum(r["route_ok"] for r in rows) / n,
        "target_accuracy": sum(r["target_ok"] for r in rows) / n,
        "endpoint_accuracy": sum(r["endpoint_ok"] for r in rows) / n,
    }
    if f_rows:
        m = len(f_rows)
        metrics |= {"followup_n": m, "intent_accuracy": sum(r["intent_ok"] for r in f_rows) / m,
                    "followup_status_accuracy": sum(r["status_ok"] for r in f_rows) / m,
                    "resource_name_in_answer": sum(r["contains_ok"] for r in f_rows) / m}
    metrics |= explanation_metrics(states, [r["out"] for r in f_rows])
    return {"rows": rows, "followups": f_rows, "states": states, "metrics": metrics}


# ---------------------------------------------------------------- 설명 지표


def _narrative(text: str | None) -> list[str]:
    from bigcontest.agent.guard import split_sentences

    return split_sentences(text)


def _cands(v: dict) -> dict[str, dict]:
    return {c["candidate_id"]: c for c in v.get("candidates") or []}


def sentence_faithful(sentence: str, cands: dict[str, dict], card_ev: dict[str, dict]) -> list[bool]:
    """문장이 말한 대상(후보 ID, 없으면 카드의 동 이름)에 대해 인용 근거마다: 그 대상의 근거이거나 동 없는 도시 근거인가.
    대상을 말하지 않은 문장은 빈 목록 (재지 않음)."""
    named = [c for c in _CID.findall(sentence) if c in cands]
    if named:
        own = {i for c in named for i in cands[c].get("evidence_ids") or []}
        dongs: set[str] = set()
    else:
        own = set()
        names = {}
        for e in card_ev.values():
            m = re.search(r"(\S+)\s*\((\d{8})\)", e.get("applied_scope") or "")
            if m:
                names[m.group(1)] = m.group(2)
        dongs = {cd for nm, cd in names.items() if nm in sentence}
        if not dongs:
            return []
    out = []
    for eid in _EV.findall(sentence):
        e = card_ev.get(eid) or {}
        scope = e.get("applied_scope") or ""
        city_level = bool(e) and not re.search(r"\(\d{8}\)", scope)
        in_dong = any(f"({cd})" in scope for cd in dongs)
        out.append(eid in own or in_dong or city_level)
    return out


def explanation_metrics(states: list[dict], followups: list[dict] | None = None) -> dict[str, Any]:
    """보고서 설명 문장·이어 묻기 답의 인용률·1회 Guard 통과율·대상 충실도·사전 제거율."""
    from bigcontest.agent.state import report_dict

    sent = cited = 0
    faith: list[bool] = []
    first_pass, reports = 0, 0
    drafted = dropped = 0
    reasons: dict[str, int] = {}
    for v in states:
        rep = report_dict(v) if v.get("report") else None
        for r in v.get("llm_log") or []:
            if r.get("slot") in ("explain", "brief") and r.get("ok"):
                drafted += int(r.get("drafted_sentences") or 0)
                dropped += int(r.get("dropped_sentences") or 0)
                for code in r.get("dropped_reasons") or []:
                    reasons[code] = reasons.get(code, 0) + 1
        if not rep or rep.get("is_abstain"):
            continue
        reports += 1
        g = [x for x in v.get("guard") or [] if x.get("guard") == "guard_report" and x.get("attempt") == 1]
        if g and all(x["verdict"] == "PASS" for x in g):
            first_pass += 1
        card_ev = {e["evidence_id"]: e for e in rep["evidence_card"]["evidence"]}
        for s in _narrative(rep.get("narrative")):
            sent += 1
            cited += bool(_EV.search(s))
            faith += sentence_faithful(s, _cands(v), card_ev)
    answered = [o for o in followups or [] if o.get("status") == "answered"]
    return {
        "reports": reports,
        "narrative_sentences": sent,
        "citation_coverage": (cited / sent) if sent else None,
        "first_pass_guard_rate": (first_pass / reports) if reports else None,
        "target_faithfulness": (sum(faith) / len(faith)) if faith else None,
        "faithfulness_checked": len(faith),
        "pre_guard_drop_rate": (dropped / drafted) if drafted else None,
        "dropped_reason_codes": reasons,
        # 이어 묻기 답 중 LLM 설명 문장이 Guard 를 지나 함께 나간 비율 (나머지는 템플릿만)
        "followup_llm_answer_rate": (sum(o.get("source") == "llm" for o in answered) / len(answered))
        if answered else None,
    }


def fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.0%}" if 0 <= v <= 1 else f"{v:g}"
    return str(v)


def table(metrics_by_llm: dict[str, dict]) -> str:
    """LLM 변형별 지표 → 마크다운 표."""
    keys = ["golden_n", "route_accuracy", "target_accuracy", "endpoint_accuracy", "followup_n", "intent_accuracy",
            "followup_status_accuracy", "reports", "narrative_sentences", "citation_coverage", "first_pass_guard_rate",
            "target_faithfulness", "pre_guard_drop_rate"]
    names = list(metrics_by_llm)
    out = ["| 지표 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for k in keys:
        out.append(f"| {k} | " + " | ".join(fmt(metrics_by_llm[n].get(k)) for n in names) + " |")
    return "\n".join(out)
