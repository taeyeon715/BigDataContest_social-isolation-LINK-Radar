"""민감도: 임계값 ±, 지표 하나씩 빼기 → Top-K 겹침률(Jaccard)과 후보 수 범위.

후보 수는 범위와 함께만 말한다. 같은 근거(도구 한 번 호출)에 규칙만 바꿔 다시 낸다.
"""

from __future__ import annotations

from datetime import date

from bigcontest.agent.schemas import NormalizedRequest, ToolResult
from bigcontest.rules import load_rules
from bigcontest.rules.compare import VARIANT_INPUTS, jaccard, key_of, run_variant, top_k

# 바꿔 보는 임계값 (기본값 포함). 기본값은 configs/rules.yaml
THRESHOLD_GRID: dict[str, list[float]] = {
    "thresholds.structure.top_share": [0.20, 0.30, 0.40],
    "thresholds.structure.abs_min": [0.30, 0.35, 0.40],
    "thresholds.access.far_km": [3.0, 5.0, 8.0],
    "thresholds.deviation.min_drop": [0.05, 0.10, 0.15],
}
# 하나씩 빼 보는 지표 (비교안 D 기준)
LEAVE_ONE_OUT: dict[str, set[str]] = {
    "구조(1인세대)": {"structure_vulnerability"},
    "접근성(직선거리)": {"access_gap"},
    "통신 겨울 변화": {"winter_contraction"},
    "통신 체류 구성": {"mobility_layer"},
    "카드 이탈·반증": {"cohort_deviation", "card_cohort_series", "expected_activity", "holiday_expectation",
                   "city_common_factor", "weather_asos", "academic_calendar"},
}


def summarize(cands, k: int) -> dict:
    n_eligible = sum(c.candidate_level in ("Level 2", "Level 3") for c in cands)
    return {"후보 수": len(cands), "Level 2 이상": n_eligible,
            "Level 3": sum(c.candidate_level == "Level 3" for c in cands),
            f"Top-{k}": {key_of(c) for c in top_k(cands, k)}}


def threshold_runs(evidence: list[ToolResult], req: NormalizedRequest, as_of: date, k: int = 10,
                   variant: str = "D") -> list[dict]:
    """임계값 하나씩 바꾼 결과 (다른 임계값은 기본값)."""
    base = summarize(run_variant(evidence, variant, req, as_of, k).candidates, k)
    rows = []
    for path, values in THRESHOLD_GRID.items():
        for v in values:
            s = summarize(run_variant(evidence, variant, req, as_of, k, load_rules({path: v})).candidates, k)
            rows.append({"바꾼 것": path.split(".", 1)[1], "값": v, "후보 수": s["후보 수"],
                         "Level 2 이상": s["Level 2 이상"], "Level 3": s["Level 3"],
                         f"Top-{k} 겹침률": jaccard(s[f"Top-{k}"], base[f"Top-{k}"])})
    return rows


def leave_one_out_runs(evidence: list[ToolResult], req: NormalizedRequest, as_of: date, k: int = 10) -> list[dict]:
    base = summarize(run_variant(evidence, "D", req, as_of, k).candidates, k)
    rows = []
    for name, drop in LEAVE_ONE_OUT.items():
        if not drop & VARIANT_INPUTS["D"]:
            continue
        s = summarize(run_variant(evidence, "D", req, as_of, k, drop=drop).candidates, k)
        rows.append({"뺀 지표": name, "후보 수": s["후보 수"], "Level 2 이상": s["Level 2 이상"], "Level 3": s["Level 3"],
                     f"Top-{k} 겹침률": jaccard(s[f"Top-{k}"], base[f"Top-{k}"])})
    return rows
