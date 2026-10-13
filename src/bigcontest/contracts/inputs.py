"""후보 종류별 필수 근거 / 선택 맥락 판정 (configs/candidate_inputs.yaml).

- 핵심(core) 필수 근거가 빠짐 → "보류"
- 그 밖 필수 근거가 빠짐 → 가능한 축만 판정하는 "부분 판정" + missing_inputs
- 선택 맥락만 빠짐 → "충족" + 경고
빠진 값을 0이나 기본값으로 채우지 않는다.
"""

from dataclasses import dataclass, field

from bigcontest.contracts import candidate_inputs_config


@dataclass
class InputCheck:
    candidate_type: str
    status: str  # 충족 / 부분 판정 / 보류
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def missing_inputs(self) -> list[str]:
        return self.missing_required


def _spec(candidate_type: str) -> tuple[str, dict]:
    cfg = candidate_inputs_config()
    if candidate_type in cfg["candidate_types"]:
        return candidate_type, cfg["candidate_types"][candidate_type]
    if candidate_type in cfg.get("variants", {}):
        v = cfg["variants"][candidate_type]
        base = dict(cfg["candidate_types"][v["type"]])
        base.update({k: v[k] for k in ("required_inputs", "optional_context") if k in v})
        return v["type"], base
    raise KeyError(f"알 수 없는 후보 종류: {candidate_type}")


def required_tools(candidate_type: str) -> list[str]:
    """등급 계산 경로에서 LLM 선택과 무관하게 항상 실행할 도구 이름."""
    cfg = candidate_inputs_config()
    _, spec = _spec(candidate_type)
    names: list[str] = []
    for item in spec["required_inputs"]:
        for key in item.get("any_of", [item.get("input")]):
            tool = cfg["inputs"][key]["tool"]
            if tool not in names:
                names.append(tool)
    return names


def evaluate_inputs(candidate_type: str, available: set[str] | list[str]) -> InputCheck:
    """있는 근거 목록으로 후보의 근거 충족 상태를 정한다."""
    cfg = candidate_inputs_config()
    names = cfg["inputs"]
    base_type, spec = _spec(candidate_type)
    have = set(available)
    unknown = have - set(names)
    if unknown:
        raise KeyError(f"알 수 없는 근거 이름: {sorted(unknown)}")

    out = InputCheck(candidate_type=base_type, status="충족")
    core_missing = False
    for item in spec["required_inputs"]:
        if "any_of" in item:
            if not have & set(item["any_of"]):
                out.missing_required.append(" 또는 ".join(item["any_of"]))
            continue
        key = item["input"]
        if key not in have:
            out.missing_required.append(key)
            core_missing = core_missing or item.get("core", False)
    for key in spec["optional_context"]:
        if key not in have:
            out.missing_optional.append(key)
            out.warnings.append(f"선택 맥락 없음: {names[key]['name']} (판정에는 영향 없음)")

    if core_missing:
        out.status = "보류"
    elif out.missing_required:
        out.status = "부분 판정"
    return out
