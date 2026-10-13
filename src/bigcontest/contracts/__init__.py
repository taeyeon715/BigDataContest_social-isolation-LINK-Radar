"""계약: 무엇을 어떤 단위·시점·연령으로 말할 수 있는가.

- grain: 데이터별 지원 차원, 교차 금지 조합 → check_request_contract
- time: 분석 모드, 공개일(실제/가정), 관측 기간, 모델·규칙 시점
- cohort: 분석 코호트 vs 서비스 대상 집단
- claims: 근거 유형별 해도 되는 / 안 되는 주장
- inputs: 후보 종류별 필수 근거 / 선택 맥락
- values: 값 상태 (원본 0 / 비공개 / 결측 / 품질 의심)

설정은 configs/*.yaml 에 있다.
"""

from functools import lru_cache

import yaml

from bigcontest.config import ROOT

CONFIG_DIR = ROOT / "configs"


@lru_cache
def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def grain_contract() -> dict:
    return load_yaml("grain_contract.yaml")


def cohorts_config() -> dict:
    return load_yaml("cohorts.yaml")


def candidate_inputs_config() -> dict:
    return load_yaml("candidate_inputs.yaml")
