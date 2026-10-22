"""태연 2축 결과의 사후 참고 조회. 등급·순서 결정 뒤 카드에만 붙인다.

ToolResult 시간 계약·등급 입력과 분리된 StructuralRiskReference를 반환한다.
고정 스냅샷의 선택 자료만 읽고, 곱셈 점수·추가 후보·대응 등급은 만들지 않는다.
"""
from __future__ import annotations

import pandas as pd

from bigcontest.agent.schemas import Candidate, StructuralRiskCell, StructuralRiskReference
from bigcontest.agent.tools.datasource import DataUnavailable, SnapshotMismatch, open_datasource

SOURCE = "reports/t1_2축위험도/위험도_2축_강남_춘천_동x성x연령.csv"
PERIOD = "2025.12 기준 사후 계산"
GROUPS = {
    "고령층": (["남성", "여성"], ["60대+"], "65세 이상 대상에 60대+ 자료를 대응해 60~64세가 포함됩니다."),
    "중장년 남성 1인가구": (["남성"], ["40대", "50대"], "40~64세 남성 대상에 남성 40·50대를 대응해 60~64세가 빠집니다. 실제 1인 여부는 알 수 없습니다."),
    "50~69세 남성": (["남성"], ["50대", "60대+"], "남성 50대·60대+ 자료를 대응해 70세 이상도 포함됩니다."),
    "청년 남성": (["남성"], ["20대", "30대"], "20~34세 대상에 남성 20·30대를 대응해 35~39세가 포함됩니다."),
}


def get_structural_risk(ctx, candidates: list[Candidate]) -> list[StructuralRiskReference]:
    """정해진 후보에 대응하는 성·연령 칸. 미래 참고 자료는 strict_as_of에서 표시하지 않는다."""
    if not candidates or ctx.backend != "real":
        return []
    out = []
    source = None
    load_error = False
    ds = None
    for c in candidates:
        group = ctx.request.group if ctx.request else None
        ref = StructuralRiskReference(candidate_id=c.candidate_id, city=c.city, adm_cd=c.adm_cd,
                                      policy_group=group, period_label=PERIOD, source_path=SOURCE,
                                      comparison_note=f"대응 등급 {c.candidate_level or '미정'}은 기준일의 구조·접근성·시점과 다른 원인 확인으로 정합니다. 구조 위험 단계는 지역 안 상대 순위라 서로 다를 수 있습니다.")
        if ctx.analysis_mode == "strict_as_of":
            ref.note = "기준일 당시 공개 여부를 확인하지 못한 사후 자료여서 이 모드에서는 표시하지 않습니다."
        elif not c.adm_cd:
            ref.status = "not_applicable"
            ref.note = "도시 단위 후보에는 동별 구조 위험 단계를 붙이지 않습니다."
        elif group not in GROUPS:
            ref.note = "요청 집단과 성·연령 자료의 대응을 정할 수 없습니다."
        else:
            if source is None and not load_error:
                try:
                    ds = open_datasource(ctx.snapshot_id)
                    source = ds.structural_risk()
                    required = {"지역", "SGIS코드", "읍면동명", "성별", "연령5", "적용 H", "V", "위험 유형", "단계"}
                    if not required.issubset(source.columns):
                        raise ValueError("구조 위험 자료 칸 누락")
                except SnapshotMismatch:
                    raise
                except (DataUnavailable, OSError, ValueError, KeyError):
                    source = None
                    load_error = True
            if source is None:
                ref.note = "이 실행에 고정된 구조 위험 참고 자료를 읽을 수 없습니다. 대응 등급은 유지합니다."
            else:
                sexes, ages, mapping = GROUPS[group]
                rows = source[(source["SGIS코드"] == c.adm_cd) & (source["지역"] == {"강남": "서울 강남구", "춘천": "강원 춘천시"}[c.city])
                              & source["성별"].isin(sexes) & source["연령5"].isin(ages)]
                keys = set(zip(rows["성별"], rows["연령5"]))
                expected = {(s, a) for s in sexes for a in ages}
                if keys != expected or len(rows) != len(expected):
                    ref.note = "대응하는 성·연령 칸이 없거나 중복돼 참고 단계를 표시하지 않습니다."
                else:
                    try:
                        ref.cells = [StructuralRiskCell(sex=r["성별"], age_band=r["연령5"], stage=r["단계"],
                                                       risk_type=r["위험 유형"], behavior=float(r["적용 H"]), vulnerability=float(r["V"]))
                                     for _, r in rows.sort_values(["성별", "연령5"]).iterrows()]
                        ref.status = "available"
                        ref.dong = str(rows.iloc[0]["읍면동명"])
                        ref.mapping_note = mapping
                        ref.source_hash = ds.hashes["structural_risk"]
                        ref.note = "H·V는 같은 지역·성·연령 안 백분위입니다. 연령 간 위험 수준·실제 고립 확률·개인 판정으로 읽지 않습니다."
                    except ValueError:
                        ref.cells = []
                        ref.note = "참고 자료의 단계·유형·백분위 값이 올바르지 않아 표시하지 않습니다."
        out.append(ref)
    return out
