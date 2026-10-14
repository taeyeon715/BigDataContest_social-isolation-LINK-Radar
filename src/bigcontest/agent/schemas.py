"""도구 입출력·반환 봉투·근거 카드·실행 상태 스키마 (Pydantic v2).

- 반환 봉투(`ToolResult`) 필드는 에이전트 설계 §5.1 표를 그대로 옮긴 것이다.
  §5.1 에 없는 칸은 `value_state`, `error_code`, `calibrated_until` 셋뿐이고 아래에 이유를 적었다.
- 도시·기간·차원·지표는 화이트리스트(Literal)로만 받는다.
- 아래쪽 "API" 묶음은 docs/API_명세.md 의 요청·응답 예시와 같은 모양이다 (tests/test_schemas.py).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------- 화이트리스트

City = Literal["강남", "춘천"]
CITIES: tuple[str, ...] = ("강남", "춘천")
Sido = Literal[
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기",
    "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]
MerchantRegion = Literal["강남구", "춘천시"]
Sex = Literal["남", "여"]
Dimension = Literal[
    "city", "adm_cd", "cell", "month", "date", "time_of_day", "weekday", "sex", "age",
    "industry", "customer_residence", "household_size", "station", "resource_type",
]
Metric = Literal["체류인구", "카드결제", "주민등록인구", "1인세대비율", "시민생활지표", "날씨", "복지자원"]
DATA_START = date(2025, 7, 1)
DATA_END = date(2025, 12, 31)

Status = Literal["OK", "EMPTY", "ERROR"]
AnalysisMode = Literal["retrospective", "simulated_replay", "strict_as_of"]
AvailabilityBasis = Literal["reported", "assumed", "unknown"]
InterpretationStatus = Literal["직접 확인", "계산 재현", "가설", "미확인"]
Direction = Literal["감소", "증가", "변화 없음"]
CandidateType = Literal["scheduled_access_review", "unexpected_activity_deviation"]
CounterResult = Literal["기각", "보류", "통과", "해당 없음"]
InputStatus = Literal["충족", "부분 판정", "보류"]
CandidateLevel = Literal["Level 0", "Level 1", "Level 2", "Level 3", "판단 보류"]
Verdict = Literal["PASS", "REVISE", "ABSTAIN"]

# simulated_replay 결과에 자동으로 붙는 표시 (G-13)
ASSUMED_AVAILABILITY_LABEL = "가정한 공개 시점: 실제 공개일이 아니라 관측일 + 가정한 공급 지연으로 재생한 결과"
DISCLAIMER = "개인 판정 아님 · 최종 판단은 담당자"


class ValueState(str, Enum):
    """값 상태. 원본 0 / 비공개(*) / 결측 / 품질 의심을 섞지 않는다 (설계 §2.1)."""

    OBSERVED = "observed"  # 정상 관측값 (0 이 아닌 수)
    ZERO = "zero"  # 원본이 실제로 0
    SUPPRESSED = "suppressed"  # 원본에 `*` 등 비공개 표시
    MISSING = "missing"  # 원본에 값이 없음 / 숫자로 바꿀 수 없음
    QUALITY_SUSPECT = "quality_suspect"  # 값은 있으나 품질 의심 (예: 개포3동 7개 지표 0)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 반환 봉투


class ToolResult(_Model):
    """공통 반환 봉투. 에이전트 설계 §5.1 이 기준 정의."""

    evidence_id: str = Field(pattern=r"^EV-[0-9A-Za-z\-]+$")
    metric: str
    value: float | int | str | list[float | None] | None = None
    unit: str | None = None
    denominator: str | None = None
    n_obs: int | None = Field(default=None, ge=0)
    direction: Direction | None = None
    measured_grain: str | None = None
    applied_scope: str | None = None
    merchant_region: MerchantRegion | None = None
    customer_residence: Sido | None = None
    is_shared_context: bool = False
    analysis_cohort: str | None = None
    service_eligibility_group: str | None = None
    observed_from: date | None = None
    observed_to: date | None = None
    analysis_mode: AnalysisMode
    as_of: date
    actual_available_at: date | None = None
    simulated_available_at: date | None = None
    availability_basis: AvailabilityBasis = "unknown"
    trained_until: date | None = None
    rule_derived_from: str | None = None
    snapshot_id: str | None = None
    data_version: str | None = None
    rule_version: str | None = None
    interpretation_status: InterpretationStatus = "미확인"
    valid_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: Status
    # --- 설계 문서 §5.1에도 반영한 칸 ---
    # 값 상태: value 가 None 일 때 결측인지 비공개인지 구분하려면 필요
    value_state: ValueState | None = None
    # status=ERROR 의 오류 코드 (에이전트 설계 §9 "status=ERROR + 오류 코드")
    error_code: str | None = None
    # 보정(임계값 맞추기)에 쓴 자료의 마지막 날 (시간 계약)
    calibrated_until: date | None = None

    @model_validator(mode="after")
    def _check(self) -> ToolResult:
        if self.status == "ERROR" and not self.error_code:
            raise ValueError("status=ERROR 이면 error_code 가 필요함")
        if self.status != "ERROR" and self.error_code:
            raise ValueError("error_code 는 status=ERROR 일 때만")
        if self.observed_from and self.observed_to and self.observed_from > self.observed_to:
            raise ValueError("observed_from 이 observed_to 보다 뒤")
        if self.availability_basis == "reported" and self.actual_available_at is None:
            raise ValueError("availability_basis=reported 이면 actual_available_at 이 필요함")
        if self.customer_residence and not self.merchant_region:
            raise ValueError("카드 근거는 merchant_region 과 customer_residence 를 같이 적음")
        if self.status == "OK" and self.value is None and self.value_state is None:
            raise ValueError("OK 인데 값이 없으면 value_state 로 이유를 적음")
        # simulated_replay 에서 가정한 공개 시점을 썼으면 표시를 자동으로 붙임
        if (
            self.analysis_mode == "simulated_replay"
            and self.availability_basis != "reported"
            and ASSUMED_AVAILABILITY_LABEL not in self.warnings
        ):
            self.warnings.append(ASSUMED_AVAILABILITY_LABEL)
        return self


# ---------------------------------------------------------------- 요청·계약


class NormalizedRequest(_Model):
    """intake 가 질문을 화이트리스트 값으로 정규화한 요청."""

    city: City
    metric: Metric
    dimensions: list[Dimension] = Field(default_factory=list)
    adm_cd: str | None = Field(default=None, pattern=r"^\d{8}$")
    period_from: date = DATA_START
    period_to: date = DATA_END
    age_min: int | None = Field(default=None, ge=0, le=120)
    age_max: int | None = Field(default=None, ge=0, le=120)
    sex: Sex | None = None
    group: str | None = None  # 서비스 대상 집단 이름 (configs/cohorts.yaml groups)
    time_of_day: Literal["00_05", "06_11", "12_17", "18_23"] | None = None
    industry: str | None = None
    # 질문에서 찾은 동 (places.resolve_place). 조사 경로는 등급을 도시 전체로 정한 뒤 이 동 후보만 남김
    # (차원에 넣지 않음). 조회 경로는 nodes.scope_request 가 adm_cd·차원으로 옮김
    adm_cds: list[Annotated[str, Field(pattern=r"^\d{8}$")]] = Field(default_factory=list)
    period_label: str | None = None  # 질문의 기간 말 (periods.find_period). 창은 nodes.scope_request 가 정함

    @model_validator(mode="after")
    def _check(self) -> NormalizedRequest:
        if self.period_from > self.period_to:
            raise ValueError("기간 시작이 끝보다 뒤")
        if self.period_from < DATA_START or self.period_to > DATA_END:
            raise ValueError(f"기간은 {DATA_START}~{DATA_END} 안이어야 함")
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError("age_min 이 age_max 보다 큼")
        if self.adm_cd and "adm_cd" not in self.dimensions:
            self.dimensions.append("adm_cd")
        if (self.age_min is not None or self.age_max is not None or self.group) and "age" not in self.dimensions:
            self.dimensions.append("age")
        if self.sex and "sex" not in self.dimensions:
            self.dimensions.append("sex")
        if self.time_of_day and "time_of_day" not in self.dimensions:
            self.dimensions.append("time_of_day")
        if len(set(self.dimensions)) != len(self.dimensions):
            self.dimensions = list(dict.fromkeys(self.dimensions))
        return self


class ContractIssue(_Model):
    code: str  # 예: GRAIN_FORBIDDEN_COMBO
    rule: str | None = None  # 관련 Guard 규칙 (G-01, G-02, G-13)
    message: str


class ContractResult(_Model):
    """check_availability / check_request_contract 결과."""

    status: Literal["OK", "ERROR"]
    source: str | None = None  # 이 요청에 쓸 원천
    issues: list[ContractIssue] = Field(default_factory=list)
    analysis_cohort: str | None = None
    service_eligibility_group: str | None = None
    measured_grain: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def reason_codes(self) -> list[str]:
        return [i.code for i in self.issues]

    @model_validator(mode="after")
    def _check(self) -> ContractResult:
        if self.status == "ERROR" and not self.issues:
            raise ValueError("ERROR 이면 사유(issues)가 필요함")
        return self


# ---------------------------------------------------------------- 후보·근거 카드·Guard


Comparator = Literal[">=", "<=", ">", "<"]


class RuleCheck(_Model):
    """등급 규칙의 조건 하나 (explain_level 입력). 등급 엔진(assign_level)이 채운다.

    met: True 충족 / False 미충족 / None 자료 없음. value·threshold 가 둘 다 수이고 comparator 가 있으면 기준까지 거리를 계산한다.
    level_if_met: 이 조건이 (지금과 반대로) 충족되면 가능한 등급 (엔진이 아는 경우만)."""

    condition: str
    met: bool | None
    value: float | int | str | None = None
    threshold: float | int | str | None = None
    comparator: Comparator | None = None
    unit: str | None = None
    input: str | None = None  # 필수 근거 이름 (candidate_inputs.yaml), 자료 없음일 때 무엇이 필요한지
    level_if_met: CandidateLevel | None = None


class ContextSignal(_Model):
    """후보에 붙는 맥락 칸 (rules-v1.1). 등급·순서에 쓰지 않는다(affects_level=False). 반증 맥락은 판단 보류의 사유만 바꿈."""

    name: str  # 예: 연휴 외지 손님 비중
    value: float | int | str | None = None
    unit: str | None = None
    text: str  # 한 줄 설명 (값·비교·한계)
    role: Literal["맥락", "반증 맥락", "구조 별도 칸"] = "맥락"
    scope: str | None = None  # 도시 / 동 이름 등 (값의 단위)
    evidence_id: str | None = None
    affects_level: bool = False


class TimelineStep(_Model):
    """명절 전·중·후 묶음의 한 칸 (R1·R2·R3 를 한 사건으로)."""

    phase: Literal["전", "중", "후"]
    code: Literal["R1", "R2", "R3"]
    label: str
    start: date
    end: date
    status: Literal["지남", "지금", "예정"]
    applies: bool = True  # 이 집단·도시에 적용되는 단계인지 (R3 는 춘천 고령층만)


class Candidate(_Model):
    candidate_id: str
    candidate_type: CandidateType
    city: City
    adm_cd: str | None = Field(default=None, pattern=r"^\d{8}$")
    check_first_adm: list[str] = Field(default_factory=list)  # 이탈 후보: "먼저 확인할 동" (공유 맥락)
    analysis_cohort: str
    service_eligibility_group: str | None = None
    trigger: str | None = None  # 예: R1 추석 D-7
    input_status: InputStatus
    missing_inputs: list[str] = Field(default_factory=list)
    counter_result: CounterResult
    counter_reason: str | None = None
    candidate_level: CandidateLevel | None = None  # assign_level 전에는 None
    level_reason: str | None = None
    queue_rank: int | None = Field(default=None, ge=1)
    selected_for_review: bool = False
    selection_budget: int | None = Field(default=None, ge=0)
    evidence_ids: list[str] = Field(default_factory=list)
    is_shared_context: bool = False
    # 등급을 정한 규칙과 조건별 충족 여부 (등급 엔진이 채움. 없으면 explain_level 은 level_reason 만 씀)
    # --- 등급 규칙(configs/rules.yaml) 칸. 모두 선택 칸 ---
    rule_id: str | None = None  # 예: L2-STRUCT-TRIGGER, L3-...+CAP-PARTIAL
    rule_version: str | None = None  # 예: rules-v1.1
    rule_checks: list[RuleCheck] = Field(default_factory=list)
    event_id: str | None = None  # 같은 사건이면 같은 값 (예: 강남:R1:20251003). 같은 사건으로 한 번만 만듦
    event_opened_at: date | None = None  # 후보가 생긴 날 (트리거 공개일 = 연휴 D-7, 이탈은 경보일 + 공급 지연)
    decided_at: date | None = None  # 등급을 정한 기준일
    evidence_confidence: Literal["높음", "보통", "낮음"] | None = None  # 근거 신뢰도 (설계 §4)
    response_type: str | None = None  # 대응 종류: 현장 확인 / 담당자 추가 분석 / 안내 강화(청년) ...
    # 후보 목록 응답에만 채움 (조사 전 자원 미리 보기를 좁힘). 원인 유형 → 필요 유형 (configs/need_types.yaml)
    cause_type: str | None = None
    need_types: list[str] = Field(default_factory=list)
    # --- rules-v1.1 선택 칸 (표시용, 등급 입력 아님) ---
    context: list[ContextSignal] = Field(default_factory=list)  # 카드 맥락·수급자 비율·1인세대 순증 등
    event_bundle_id: str | None = None  # 명절 묶음 (예: 춘천:명절:20251003). R1·R2·R3 후보가 같은 값
    event_timeline: list[TimelineStep] = Field(default_factory=list)  # 명절 전·중·후
    heat_stage: str | None = None  # R4: 폭염주의보 / 폭염경보 / 폭염중대경보 (상황 시연에서만)

    @model_validator(mode="after")
    def _check(self) -> Candidate:
        if self.candidate_type == "scheduled_access_review" and self.counter_result != "해당 없음":
            raise ValueError("예정된 점검은 반증 대상이 아님 (counter_result=해당 없음)")
        if self.candidate_type == "unexpected_activity_deviation" and self.counter_result == "해당 없음":
            raise ValueError("예상 밖 이탈은 반증 결과가 있어야 함")
        if self.counter_result == "기각" and self.candidate_level not in (None, "Level 1"):
            raise ValueError("기각 후보는 Level 1 (기각 사유 기록)")
        if self.counter_result == "기각" and not self.counter_reason:
            raise ValueError("기각 사유가 필요함")
        if self.input_status != "충족" and not self.missing_inputs:
            raise ValueError("부분 판정·보류이면 missing_inputs 를 적음")
        if self.input_status == "보류" and self.candidate_level not in (None, "판단 보류"):
            raise ValueError("필수 근거가 빠진 보류 후보는 판단 보류")
        if self.check_first_adm and not self.is_shared_context:
            raise ValueError("동 목록을 붙인 도시 단위 후보는 is_shared_context=true")
        if self.selected_for_review:
            if self.queue_rank is None or self.selection_budget is None:
                raise ValueError("배정된 후보는 queue_rank 와 selection_budget 이 필요함")
            if self.queue_rank > self.selection_budget:
                raise ValueError("queue_rank 가 검토 예산(K)보다 큼")
        return self


class Fact(_Model):
    """사실 검사용 묶음 (지표, 지역, 기간, 값, 단위, 방향, 근거 ID). 에이전트 설계 §6."""

    metric: str
    region: str
    period: str
    value: float | int | str | None
    unit: str | None = None
    direction: Direction | None = None
    evidence_id: str


class EvidenceCard(_Model):
    card_id: str
    run_id: str | None = None
    candidate_id: str | None = None
    title: str
    snapshot_id: str
    analysis_mode: AnalysisMode
    as_of: date
    evidence_revision: int = Field(ge=1)
    facts: list[Fact] = Field(default_factory=list)
    evidence: list[ToolResult] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    time_label: str | None = None
    disclaimer: str = DISCLAIMER

    @model_validator(mode="after")
    def _check(self) -> EvidenceCard:
        ids = {e.evidence_id for e in self.evidence}
        bad = [f.evidence_id for f in self.facts if f.evidence_id not in ids]
        if bad:
            raise ValueError(f"근거 카드에 없는 근거 ID: {bad}")
        if self.analysis_mode == "simulated_replay" and not self.time_label:
            self.time_label = ASSUMED_AVAILABILITY_LABEL
        if self.disclaimer != DISCLAIMER:
            raise ValueError("고지 문구는 바꿀 수 없음")
        return self


class GuardResult(_Model):
    guard: Literal["guard_signal", "guard_report"]
    check: Literal["신호", "사실", "표현"]
    verdict: Verdict
    rule_ids: list[str] = Field(default_factory=list)  # 예: ["G-03"]
    reasons: list[str] = Field(default_factory=list)
    attempt: int = Field(default=1, ge=1)

    @field_validator("rule_ids")
    @classmethod
    def _rule_ids(cls, v: list[str]) -> list[str]:
        for r in v:
            if not (r.startswith("G-") and r[2:].isdigit() and 1 <= int(r[2:]) <= 14):
                raise ValueError(f"알 수 없는 Guard 규칙: {r}")
        return v

    @model_validator(mode="after")
    def _check(self) -> GuardResult:
        if self.verdict != "PASS" and not self.reasons:
            raise ValueError("REVISE·ABSTAIN 은 사유가 필요함")
        return self


class ResourceRecommendation(_Model):
    resource_id: str
    name: str
    resource_type: str
    city: City
    adm_cd: str | None = None
    target_group: str | None = None
    eligibility: Literal["적합", "확인 필요", "부적합"]
    operating_status: Literal["운영", "확인 필요", "종료"] = "확인 필요"
    distance_km: float | None = Field(default=None, ge=0)
    phone_based: bool = False
    source_url: str
    verified_date: date
    warnings: list[str] = Field(default_factory=list)


CHECK_AREA_NOTE = "판단도구 문항 채점 아님, 현장에서 먼저 물어볼 영역"
LIST_NOTE = "시스템은 명단을 만들지 않음. 기존 명단 중 어느 것을 먼저 꺼낼지만 안내"
DISTRIBUTION_INTERNAL = "내부용(대외비)"
EXTERNAL_NOTICE = "특정 동네가 위험하다는 뜻이 아니라, 명절·계절에 돌봄이 끊기기 쉬운 이웃을 먼저 살피는 기간입니다"
CheckAreaName = Literal["건강·복약", "식사·생활", "사회적 관계·연락", "이동·외출", "주거·안전", "경제·지원"]


class CheckArea(_Model):
    """확인할 영역. 판단도구 문항 채점이 아니라 현장에서 먼저 물어볼 영역."""

    area: CheckAreaName
    reasons: list[str] = Field(default_factory=list)  # 이 영역을 고른 근거 (예: "연휴 진료 단절(R1)")
    ask: str | None = None  # 먼저 물어볼 것


class ActionPlan(_Model):
    """대응 템플릿 5칸: 주체 / 방법 / 기한 / 확인할 영역 / 연계 자원. text 는 보고서용 한 줄."""

    owner: list[str]  # 주체
    method: str  # 방법 (전화 → 방문 대상 추리기 / 청년은 안내 강화)
    due: str  # 기한 (문구)
    due_date: date | None = None  # 기한 날짜 (계산할 수 있을 때)
    check_areas: list[CheckAreaName] = Field(default_factory=list)  # 확인할 영역
    linked_resources: list[str] = Field(default_factory=list)  # 연계 자원 (위급 시 안내 제외)
    tasks: list[str] = Field(default_factory=list)  # 시점 규칙별 할 일 (설계 문서 §5.4)
    resource_note: str | None = None  # 자원이 없을 때 이유
    emergency_contacts: list[str] = Field(default_factory=list)  # 위급 시 안내 (순위 밖, 따로 한 줄)
    text: str  # 보고서용 한 줄


class MorningChecklist(_Model):
    """내일 아침 체크리스트. Level 2·3 후보만."""

    lists: list[str]  # ① 꺼낼 명단 (기존 시스템의 명단. 이 동·집단으로 걸러 봄)
    contacts: list[str]  # ② 연락할 사람·기관
    method: str  # ③ 방법
    due: str  # ④ 기한
    check_areas: list[CheckAreaName]  # ⑤ 확인할 영역
    note: str = LIST_NOTE


class CandidateAction(_Model):
    """후보 하나의 담당자용 출력: 확인할 영역 + 대응 템플릿 + 내일 아침 체크리스트."""

    candidate_id: str
    candidate_level: CandidateLevel | None = None
    group: str | None = None  # 대응 템플릿 집단 (configs/action_templates.yaml groups)
    where: str | None = None  # 동 이름 (이탈 후보는 먼저 확인할 동)
    check_areas: list[CheckArea] = Field(default_factory=list)
    check_area_note: str = CHECK_AREA_NOTE
    action_plan: ActionPlan | None = None
    checklist: MorningChecklist | None = None


class RuleCheckView(_Model):
    """explain_level 결과의 조건 하나: 충족 여부 + 기준까지 거리 + 한 줄 설명."""

    condition: str
    met: bool | None
    value: float | int | str | None = None
    threshold: float | int | str | None = None
    distance: float | None = None  # 기준까지 남은 차이 (미충족) 또는 여유 (충족). 수일 때만
    text: str


class LevelExplanation(_Model):
    """등급 설명 (결정론): 어떤 규칙이 맞았나, 조건별 충족·미충족, 기준까지 거리, 빠진 근거가 들어오면 바뀔 등급."""

    candidate_id: str
    candidate_level: CandidateLevel | None = None
    rule_id: str | None = None
    rule_version: str | None = None
    checks: list[RuleCheckView] = Field(default_factory=list)
    what_if: list[str] = Field(default_factory=list)  # 무엇이 바뀌면 등급이 바뀌나
    lines: list[str] = Field(default_factory=list)  # 한국어 설명 문장 (템플릿)


TraceSource = Literal["rule", "llm", "guard", "human", "template"]


class TraceStep(_Model):
    """decision_trace 한 칸: 노드에서 무엇을 골랐고 왜 (규칙 ID 또는 LLM 이유). 질문 원문은 담지 않음."""

    node: str
    choice: str | None = None  # 이 노드의 결과 요약 (경로·판정·등급 분포 등)
    next: str | None = None  # 분기 노드면 다음 노드
    source: TraceSource | None = None  # 누가 정했나
    rule_ids: list[str] = Field(default_factory=list)  # 규칙·계약·Guard 코드
    reason: str | None = None  # LLM 이유 또는 규칙 사유 (짧게)
    inputs: dict[str, Any] = Field(default_factory=dict)  # 입력 요약 (원문 질문 없음)
    changed: list[str] = Field(default_factory=list)  # 바뀐 상태 칸
    # 시작 시각·소요 시간. 실행 상태(decision_trace)에는 있고, 보고서에 싣는 사본에서는 뺀다 (같은 입력 → 같은 보고서)
    started_at: datetime | None = None
    ms: float | None = Field(default=None, ge=0)


class Report(_Model):
    report_revision: int = Field(ge=1)
    evidence_card: EvidenceCard
    fact_sentences: list[str] = Field(default_factory=list)  # 근거 카드에서 템플릿으로 만든 문장
    narrative: str | None = None  # LLM 설명 문장 (없으면 템플릿만)
    is_abstain: bool = False
    needs_regeneration: bool = False
    # ---- 담당자용 출력. Level 2·3 후보마다
    actions: list[CandidateAction] = Field(default_factory=list)
    # ---- 등급 설명 (후보에 rule_checks 가 있을 때)
    level_explanations: list[LevelExplanation] = Field(default_factory=list)
    # ---- 이 보고서를 만들기까지의 판단 기록 (시각·소요 시간 없는 사본. 시각은 실행 상태에)
    decision_trace: list[TraceStep] = Field(default_factory=list)
    # ---- 추가 조사 뒤 "지난 회차 대비 달라진 점" (템플릿 문장, fact_sentences 에도 들어 있음)
    round_changes: list[str] = Field(default_factory=list)
    # ---- 이 판에 반영한 검토 ID (가장 최근 검토까지 반영됐는지 API 가 확인)
    review_ids: list[str] = Field(default_factory=list)
    # ---- 배포 표시와 대외용 문구
    distribution: Literal["내부용(대외비)"] = DISTRIBUTION_INTERNAL
    external_notice: str = EXTERNAL_NOTICE


class RunState(TypedDict, total=False):
    """LangGraph 상태. 에이전트 설계 §2 표의 필드 그대로."""

    run_id: str
    mode: Literal["monitoring", "qa"]
    question: str | None
    as_of: date
    analysis_mode: AnalysisMode
    snapshot_id: str
    request: NormalizedRequest
    route: Literal["lookup", "investigate", "out_of_scope"]
    contract_check: ContractResult
    evidence: list[ToolResult]
    candidates: list[Candidate]
    resources: list[ResourceRecommendation]
    evidence_card: EvidenceCard
    report: Report
    guard: list[GuardResult]
    revise_count: dict[str, int]
    extra_rounds: int
    evidence_revision: int
    report_revision: int
    decision: Literal["approved", "held", "more"] | None
    abstain_reason: str | None
    errors: list[dict[str, str]]


RUN_STATE_FIELDS: tuple[str, ...] = tuple(RunState.__annotations__)


# ---------------------------------------------------------------- API (docs/API_명세.md)


class ErrorResponse(_Model):
    error_code: str
    message: str
    details: list[ContractIssue] = Field(default_factory=list)


class HealthResponse(_Model):
    status: Literal["ok", "degraded"]
    active_snapshot_id: str | None
    time: datetime


class MeResponse(_Model):
    """GET /me: 지금 토큰의 이름·역할. 화면이 버튼을 잠글지 정할 때 쓴다."""
    name: str
    role: Literal["조회", "검토", "승인"]
    shared: bool = False  # 공용 토큰이면 true (기록 이름에 `공용:`이 붙음)
    authenticated: bool = True  # 토큰 설정이 없는 개발 모드면 false


class SnapshotInfo(_Model):
    snapshot_id: str
    created_at: datetime
    data_from: date
    data_to: date
    cleaning_rule_version: str
    rule_version: str | None = None
    ledger_version: str | None = None
    previous_snapshot_id: str | None = None
    row_counts: dict[str, int] = Field(default_factory=dict)


class TriggerInfo(_Model):
    trigger_id: str
    rule: str  # R1~R6, W
    name: str
    city: City
    starts_on: date
    note: str | None = None


class CandidateCountRange(_Model):
    count: int = Field(ge=0)
    low: int = Field(ge=0)  # 임계값 범위 안 최소
    high: int = Field(ge=0)
    note: str = "임계값에 따라 달라지는 수"

    @model_validator(mode="after")
    def _check(self) -> CandidateCountRange:
        if not (self.low <= self.count <= self.high):
            raise ValueError("count 는 low~high 안이어야 함")
        return self


class ReleasedCandidate(_Model):
    """지난 기준일(7일 전) 목록에는 있었고 이번 목록에는 없는 후보 + 해제 사유.
    후보 ID 는 지난 기준일 목록의 ID 다 (이번 목록에는 없으므로 /runs 로 열 수 없음)."""

    candidate_id: str
    candidate_type: CandidateType
    city: City
    adm_cd: str | None = None
    check_first_adm: list[str] = Field(default_factory=list)
    analysis_cohort: str
    service_eligibility_group: str | None = None
    candidate_level: CandidateLevel | None = None  # 지난 기준일의 등급
    trigger: str | None = None
    last_seen_as_of: date  # 지난 기준일
    release_reason: str


class OverviewResponse(_Model):
    as_of: date
    city: City
    snapshot_id: str
    analysis_mode: AnalysisMode
    triggers: list[TriggerInfo]
    candidate_counts: dict[CandidateType, CandidateCountRange]
    new_candidate_ids: list[str] = Field(default_factory=list)
    released_candidate_ids: list[str] = Field(default_factory=list)
    time_label: str | None = None
    disclaimer: str = DISCLAIMER
    # --- 선택 칸 ---
    released_candidates: list[ReleasedCandidate] = Field(default_factory=list)  # 해제된 후보 + 사유
    compared_as_of: date | None = None  # 새로 생긴/해제된 후보를 비교한 기준일 (7일 전, 자료 기간 밖이면 없음)
    monitoring_run_id: str | None = None  # 실제 그래프: 이 화면이 읽은 정기 감시 실행 (후보 ID 는 이 실행의 것)
    rule_version: str | None = None


class MapArea(_Model):
    adm_cd: str = Field(pattern=r"^\d{8}$")
    adm_nm: str
    structure_value: float | None  # 예: 1인세대 비율
    structure_rank: int | None = Field(default=None, ge=1)
    behavior_signal: Literal["예정된 점검", "이탈 경보(공유 맥락)", "없음"]
    is_shared_context: bool = False
    nearest_hub_km: float | None = None
    value_state: ValueState = ValueState.OBSERVED
    structure_top: bool | None = None  # 등급 규칙의 "구조 상위" (상위 비율 + 최소값, rules.yaml)


class MapResource(_Model):
    resource_id: str
    name: str
    resource_type: str
    lat: float | None
    lon: float | None
    phone_based: bool = False
    resource_kind: str | None = None  # 원장 v2 자원 종류 (시설 / 전화 상담 / 제도·사업 / 사람)


class MapResponse(_Model):
    as_of: date
    city: City
    snapshot_id: str
    structure_metric: str
    areas: list[MapArea]
    resources: list[MapResource]
    note: str = "구조 취약성 × 행동 신호 2차원 표시. 위험지도 아님"
    monitoring_run_id: str | None = None  # 행동 신호를 읽은 정기 감시 실행
    rule_version: str | None = None


class CandidateListResponse(_Model):
    as_of: date
    city: City
    snapshot_id: str
    selection_budget: int = Field(ge=0)
    items: list[Candidate]
    monitoring_run_id: str | None = None  # 실제 그래프. 후보 ID 는 이 정기 감시 실행의 것 (/runs 가 받는 ID)


# 요청 입력 한도. 질문은 LLM 토큰 비용이 되므로 짧게 받는다.
MAX_QUESTION = 500
MAX_MEMO = 2000
MAX_NAME = 50
MAX_LIST = 50
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9:._-]{1,100}$"
ShortId = Annotated[str, Field(min_length=1, max_length=100)]


class RunCreateRequest(_Model):
    mode: Literal["monitoring", "qa"]
    question: str | None = Field(default=None, max_length=MAX_QUESTION)
    candidate_id: str | None = Field(default=None, max_length=MAX_NAME)
    city: City | None = None  # 정기 감시 대상 도시. 비우면 두 도시 모두. 후보로 열 때는 그 도시 후보만
    as_of: date
    analysis_mode: AnalysisMode = "simulated_replay"
    # 후보로 열 때 그 후보를 읽은 정기 감시 실행 (현황·후보 목록 응답의 monitoring_run_id). 주면 그 실행에서만 찾음
    monitoring_run_id: str | None = Field(default=None, max_length=MAX_NAME)
    # 후보로 열 때 함께 볼 후보 (같은 정기 감시 실행·같은 도시). 조사 실행은 candidate_id + 이 후보만 남김
    candidate_ids: list[ShortId] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _check(self) -> RunCreateRequest:
        if self.mode == "qa" and not (self.question or self.candidate_id):
            raise ValueError("qa 실행은 question 이나 candidate_id 가 필요함")
        if self.monitoring_run_id and not self.candidate_id:
            raise ValueError("monitoring_run_id 는 candidate_id 와 함께만 씀")
        if self.candidate_ids and not self.candidate_id:
            raise ValueError("candidate_ids 는 candidate_id 와 함께만 씀")
        if not (DATA_START <= self.as_of <= DATA_END):
            raise ValueError("as_of 는 데이터 기간 안이어야 함")
        return self


class RunAccepted(_Model):
    run_id: str
    status: Literal["queued"]
    snapshot_id: str
    status_url: str


RunPhase = Literal["queued", "running", "awaiting_approval", "published", "answered", "abstained", "approved", "held", "failed"]


class RunStatusResponse(_Model):
    run_id: str
    status: RunPhase
    mode: Literal["monitoring", "qa"]
    route: Literal["lookup", "investigate", "out_of_scope"] | None = None
    as_of: date
    analysis_mode: AnalysisMode
    snapshot_id: str
    path: list[str] = Field(default_factory=list)  # 지나온 노드
    guard: list[GuardResult] = Field(default_factory=list)
    contract_check: ContractResult | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    evidence_revision: int | None = None
    report_revision: int | None = None
    report_stale: bool = False
    abstain_reason: str | None = None
    errors: list[dict[str, str]] = Field(default_factory=list)
    decision_trace: list[TraceStep] = Field(default_factory=list)  # "에이전트가 이렇게 판단함"
    # 선택 칸. report_revision 은 요청한 판(다시 만드는 중이면 다음 판), 이것은 실제로 발행된 판.
    # 승인은 report_stale=false 이고 둘이 같을 때만 됨
    published_report_revision: int | None = None
    extra_rounds: int = 0


class EvidenceResponse(_Model):
    run_id: str
    snapshot_id: str
    evidence_card: EvidenceCard


class ResourceJudgement(_Model):
    resource_id: str = Field(min_length=1, max_length=MAX_NAME)
    judgement: Literal["적합", "보류", "부적합"]
    memo: str | None = Field(default=None, max_length=MAX_MEMO)


class ReviewRequest(_Model):
    reviewer: str = Field(min_length=1, max_length=MAX_NAME)  # 인증을 켜면 서버가 토큰의 이름으로 바꿔 기록
    checked_evidence_ids: list[ShortId] = Field(default_factory=list, max_length=MAX_LIST)
    resource_judgements: list[ResourceJudgement] = Field(default_factory=list, max_length=MAX_LIST)
    memo: str | None = Field(default=None, max_length=MAX_MEMO)
    follow_up_on: date | None = None
    # 검토하면서 화면에서 본 보고서 판. 주면 지금 판과 다를 때 거절 (REVIEW_REVISION_STALE)
    report_revision: int | None = Field(default=None, ge=1)


class ReviewResponse(_Model):
    run_id: str
    review_id: str
    report_revision: int = Field(ge=1)
    report_stale: bool  # 저장 뒤 실행의 실제 상태. true 면 보고서를 다시 만들어야 승인 가능
    changed: bool = False  # 이번 저장으로 자원 판정이 새로 바뀌었나
    evidence_revision: int | None = None  # 검토 기준 근거 판


# 추가 조사 범위. 도구는 등급 계산에 쓰지 않는 추가 도구(tools.EXTRA_TOOLS)만
ExtraTool = Literal["get_mobility_layer", "get_validation_reference", "calc_holiday_gap", "calc_winter_contraction",
                    "calc_heat_response"]
CounterCheck = Literal["공휴일 기대값", "도시 공통요인", "강수", "학사일정"]
MAX_FOCUS_MEMO = 500


class MoreFocus(_Model):
    """추가 조사(more)에서 담당자가 정하는 범위. 메모는 LLM(없으면 규칙)이 화이트리스트 도구 계획으로 바꿈."""

    candidate_ids: list[ShortId] = Field(default_factory=list, max_length=10)  # 이 실행의 후보 ID만
    tools: list[ExtraTool] = Field(default_factory=list, max_length=5)
    counter_checks: list[CounterCheck] = Field(default_factory=list, max_length=4)
    memo: str | None = Field(default=None, max_length=MAX_FOCUS_MEMO)  # 보고서·로그에 원문을 넣지 않음


class ApproveRequest(_Model):
    decision: Literal["approved", "held", "more"]
    report_revision: int = Field(ge=1)
    approver: str = Field(min_length=1, max_length=MAX_NAME)  # 인증을 켜면 서버가 토큰의 이름으로 바꿔 기록
    idempotency_key: str = Field(pattern=IDEMPOTENCY_KEY_PATTERN)
    memo: str | None = Field(default=None, max_length=MAX_MEMO)
    focus: MoreFocus | None = None  # decision=more 일 때만

    @model_validator(mode="after")
    def _check(self) -> ApproveRequest:
        if self.focus is not None and self.decision != "more":
            raise ValueError("focus 는 추가 조사(more)일 때만 씀")
        return self


class ApproveResponse(_Model):
    run_id: str
    decision: Literal["approved", "held", "more"]
    report_revision: int
    approval_id: str
    duplicate: bool = False  # 같은 키로 다시 보낸 요청이면 true (기록은 1건)
    approver: str | None = None  # 서버가 기록한 승인자 이름 (공용 토큰이면 `공용:` 붙음)


class QARequest(_Model):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    as_of: date
    analysis_mode: AnalysisMode = "simulated_replay"
    city: City | None = None


class QAResponse(_Model):
    run_id: str
    status: Literal["answered", "abstained"]
    evidence_card: EvidenceCard | None = None
    answer: str | None = None  # 사실 검사가 끝난 문장만
    guard: list[GuardResult] = Field(default_factory=list)
    abstain_reason: str | None = None
    needed_data: list[str] = Field(default_factory=list)


class FollowupRequest(_Model):
    """이어 묻기: 이미 끝난 실행의 근거 카드 안에서만 답함 (새 도구 실행 없음)."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION)


class FollowupResponse(_Model):
    run_id: str
    followup_id: str
    status: Literal["answered", "abstained"]
    answer: str | None = None  # 보고서 Guard(사실·표현)를 지난 문장만
    cited_evidence_ids: list[str] = Field(default_factory=list)  # 모두 이 실행 근거 카드 안의 ID
    source: Literal["llm", "template"] | None = None
    report_revision: int | None = None  # 답의 근거가 된 보고서 판
    guard: list[GuardResult] = Field(default_factory=list)
    abstain_reason: str | None = None
    needed_data: list[str] = Field(default_factory=list)


class RunResource(ResourceRecommendation):
    """실행에 묶인 추천 자원 (실행 시작 때 고정한 스냅샷·원장으로 고른 것) + 담당자 판정.

    자원 하나에 한 줄 (판정은 자원 ID 단위). 여러 후보에 함께 추천된 자원은 candidate_ids 에 그 후보를 모두 적는다.
    거리는 그 후보들 중 가장 가까운 값, 자격은 가장 조심스러운 값(부적합 > 확인 필요 > 적합), 경고는 합친 것."""

    candidate_id: str | None = None  # candidate_ids 의 첫 후보 (옛 칸)
    candidate_ids: list[str] = Field(default_factory=list)  # 이 자원을 추천받은 후보 (실행의 후보 순서)
    judgement: Literal["적합", "보류", "부적합"] | None = None  # 가장 최근 검토의 판정


class RunResourcesResponse(_Model):
    run_id: str
    snapshot_id: str
    evidence_revision: int | None = None
    report_revision: int | None = None
    items: list[RunResource]


class ResourceListResponse(_Model):
    adm_cd: str | None
    need_type: str | None
    group: str | None
    items: list[ResourceRecommendation]
    ledger_version: str | None = None  # 실제 원장 버전 (가짜는 없음)
    total: int | None = None  # 거른 뒤 전체 수 (items 는 limit 까지)
    # 원인 유형으로 좁힐 때. need_types 는 그 원인의 필요 유형, scope_total 은 필요 유형으로 거르기 전 (관할·대상·제외 규칙만) 수
    cause_type: str | None = None
    need_types: list[str] = Field(default_factory=list)
    scope_total: int | None = None


FollowState = Literal["지남", "이번 주", "나중"]


class FollowUpItem(_Model):
    """후속 확인 한 건 (GET /followups): 후보마다 가장 최근 조사 실행의 마지막 후속 확인일."""

    candidate_id: str
    run_id: str
    city: City | None = None
    follow_up_on: date
    memo: str | None = None
    review_id: str
    reviewer: str
    run_status: RunPhase
    approved: bool = False
    state: FollowState | None = None  # as_of 를 주면: 지남 / 이번 주(7일 안) / 나중


class FollowUpListResponse(_Model):
    as_of: date | None = None
    city: City | None = None
    date_from: date | None = None
    date_to: date | None = None
    items: list[FollowUpItem]


# ---------------------------------------------------------------- 현장 확인 결과
MAX_COUNT = 100_000
Count = Annotated[int, Field(ge=0, le=MAX_COUNT)]


class OutcomeRequest(_Model):
    """승인된 조사 실행의 후보 하나(동·집단)에 대한 현장 확인 결과. 건수만 받고 이름·주소·메모는 받지 않음."""

    candidate_id: ShortId
    checked_households: Count  # 확인한 가구 수
    contacted: Count  # 연락됨
    absent: Count  # 부재
    new_links: Count  # 신규 연계 (서비스·자원에 새로 연결)
    crises_found: Count  # 위기 발견
    idempotency_key: str = Field(pattern=IDEMPOTENCY_KEY_PATTERN)


class OutcomeRecord(_Model):
    outcome_id: str
    run_id: str
    candidate_id: str
    city: City
    adm_cd: str | None = None
    group: str | None = None  # 서비스 대상 집단
    trigger_code: str  # R1·R4·R6·W·DEV …
    event_id: str | None = None
    checked_households: int
    contacted: int
    absent: int
    new_links: int
    crises_found: int
    recorded_by: str
    created_at: datetime


class OutcomeResponse(_Model):
    run_id: str
    outcome: OutcomeRecord
    duplicate: bool = False  # 같은 멱등 키로 다시 보낸 요청이면 true (기록은 1건)


class OutcomeListResponse(_Model):
    """실행 하나의 결과 (그 실행 상세 화면용이라 10 미만도 그대로). items 는 후보마다 가장 최근 기록."""

    run_id: str
    items: list[OutcomeRecord]
    history_count: int = 0  # 고쳐 적은 것까지 모든 기록 수


class OutcomeSummaryRow(_Model):
    city: City
    trigger_code: str
    event_id: str | None = None
    runs: int  # 실행 수
    places: int  # 동·집단 수 (후보 수)
    # 합계가 10 미만이면 None 이고 small_cells 에 칸 이름 (화면은 "10 미만")
    checked_households: int | None = None
    contacted: int | None = None
    absent: int | None = None
    new_links: int | None = None
    crises_found: int | None = None
    small_cells: list[str] = Field(default_factory=list)
    contact_rate: float | None = None  # 연락됨 / 확인 가구 (두 칸 모두 10 이상일 때만)
    link_rate: float | None = None
    crisis_rate: float | None = None


class OutcomeSummaryResponse(_Model):
    city: City | None = None
    by: Literal["trigger", "event"] = "trigger"
    small_cell: int = 10
    rows: list[OutcomeSummaryRow]
    note: str = "건수만 모음 (개인 정보 없음). 10 미만 칸은 숫자를 보이지 않음. 등급 계산에는 쓰지 않음"


class SourceCapability(_Model):
    source: str
    name: str
    dimensions: list[Dimension]
    cities: list[City]
    measured_grain: str
    period_from: date
    period_to: date
    age_bands: list[str] = Field(default_factory=list)
    observed_window_status: str | None = None


class CapabilitiesResponse(_Model):
    grain_version: str
    sources: list[SourceCapability]
    forbidden_combos: list[dict[str, Any]]

