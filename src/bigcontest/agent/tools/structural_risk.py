"""후보 카드의 칸 근거 축 (rules-v2.0 S·H·V·A). 등급·순서 결정 뒤 카드에만 붙인다.

후보 동 × 정책 집단의 성·연령 칸마다, 기준일에 쓸 수 있던 달로 계산한 근거 축(cell_axes.axes_at)을 보여 준다.
연휴 주의·동절기 칸 판정이 쓰는 것과 같은 계산이라 화면마다 지표가 갈리지 않는다.
ToolResult·facts·LLM 입력과 분리된 StructuralRiskReference 를 돌려주고, 후보·등급은 바꾸지 않는다.
기준일에 공개된 달이 없으면(strict_as_of 등) 표시하지 않는다.
"""
from __future__ import annotations

import pandas as pd

from bigcontest.agent.schemas import Candidate, StructuralRiskCell, StructuralRiskReference
from bigcontest.agent.tools import report as R
from bigcontest.agent.tools.datasource import open_datasource

SOURCE = "rules-v2.0 칸 근거 축 (기준일에 공개된 주민등록·통신·수급 통계·자원 원장)"
GROUPS = {
    "고령층": (["남", "여"], ["60대+"], "65세 이상 대상에 60대 이상 칸을 대응해 60~64세가 포함됩니다."),
    "중장년 남성 1인가구": (["남"], ["40대", "50대"], "40~64세 남성 대상에 남성 40·50대 칸을 대응해 60~64세가 빠집니다. 실제 1인 여부는 알 수 없습니다."),
    "50~69세 남성": (["남"], ["50대", "60대+"], "남성 50대·60대 이상 칸을 대응해 70세 이상도 포함됩니다."),
    "청년 남성": (["남"], ["20대", "30대"], "20~34세 대상에 남성 20·30대 칸을 대응해 35~39세가 포함됩니다."),
}
CITY_NOTE = "도시 단위 후보에는 동별 칸 근거 축을 붙이지 않습니다."
READ_NOTE = ("S·H·V는 같은 도시·성·연령 칸 안 상대 순위로 세운 축입니다(상위 1/3). 연령 간 위험 수준·실제 고립 확률·"
             "개인 판정으로 읽지 않습니다.")


def _num(x) -> float | None:
    return None if pd.isna(x) else float(x)


def _cell(r, rules: dict) -> StructuralRiskCell:
    from bigcontest.holiday_watch import cell_facts
    from bigcontest.rules.levels_v2 import axes_on, cell_type

    on = axes_on(cell_facts(r, "참고", rules))
    return StructuralRiskCell(sex=r["sex"], age_band=r["age5"], pop=None if pd.isna(r["pop"]) else int(r["pop"]),
                              axes_on=on, cell_type=cell_type(on), s=_num(r["s_eb"]),
                              h=_num(r["h"]) if r.get("h_state") == "observed" else None,
                              v=_num(r["v_eb"]) if r.get("v_state") == "observed" else None,
                              a_km=_num(r["a_km"]), small=bool(r["s_small"]))


def _group(ctx, c: Candidate) -> str | None:
    g = ctx.request.group if ctx.request else None
    return g if g in GROUPS else (R.group_key(c) if R.group_key(c) in GROUPS else None)


def get_structural_risk(ctx, candidates: list[Candidate]) -> list[StructuralRiskReference]:
    """정해진 후보에 대응하는 성·연령 칸의 근거 축. 기준일에 공개된 자료로만 계산한다."""
    if not candidates or ctx.backend != "real":
        return []
    from bigcontest.agent.tools.cell_axes import axes_at
    from bigcontest.rules.levels_v2 import load_rules_v2

    rules = load_rules_v2()
    ax, loaded = None, False
    out = []
    for c in candidates:
        group = _group(ctx, c)
        v2 = (c.rule_id or "").startswith("V2-")
        ref = StructuralRiskReference(
            candidate_id=c.candidate_id, city=c.city, adm_cd=c.adm_cd, policy_group=group, period_label="",
            source_path=SOURCE,
            comparison_note=("이 칸 근거 축으로 대응 등급을 정했습니다." if v2 else
                             f"대응 등급 {c.candidate_level or '미정'}은 동 단위 구조·접근성·시점으로 정했고, "
                             "칸 근거 축은 참고입니다."))
        if not c.adm_cd:
            ref.status = "not_applicable"
            ref.note = CITY_NOTE
        elif group is None:
            ref.note = "후보 집단과 성·연령 칸의 대응을 정할 수 없습니다."
        else:
            if not loaded:
                ax, loaded = axes_at(open_datasource(ctx.snapshot_id), ctx.as_of, ctx.analysis_mode), True
            if ax is None:
                ref.note = "기준일에 공개된 주민등록·통신 달을 확인하지 못해 칸 근거 축을 표시하지 않습니다."
            else:
                sexes, ages, mapping = GROUPS[group]
                rows = ax.df[(ax.df["adm_cd"] == c.adm_cd) & ax.df["sex"].isin(sexes) & ax.df["age5"].isin(ages)]
                if len(rows) != len(sexes) * len(ages):
                    ref.note = "대응하는 성·연령 칸이 없거나 중복돼 칸 근거 축을 표시하지 않습니다."
                else:
                    ref.cells = [_cell(r, rules) for _, r in rows.sort_values(["sex", "age5"]).iterrows()]
                    ref.status = "available"
                    ref.dong = R.adm_name(c.adm_cd)
                    ref.period_label = f"기준일 {ctx.as_of} 공개 자료: {ax.months_label}"
                    ref.mapping_note = mapping
                    ref.note = READ_NOTE
        out.append(ref)
    return out
