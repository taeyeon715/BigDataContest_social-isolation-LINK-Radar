// src/bigcontest/agent/schemas.py 의 API 모델을 그대로 옮긴 타입 (docs/API_명세.md).
// 이름·칸이 바뀌면 여기도 같이 바꾼다. 조회용 모델은 api/models.py 에 있다.

export type City = '강남' | '춘천'
export const CITIES: City[] = ['강남', '춘천']
export type AnalysisMode = 'retrospective' | 'simulated_replay' | 'strict_as_of'
export type CandidateType = 'scheduled_access_review' | 'unexpected_activity_deviation'
export type CounterResult = '기각' | '보류' | '통과' | '해당 없음'
export type InputStatus = '충족' | '부분 판정' | '보류'
export type CandidateLevel = 'Level 0' | 'Level 1' | 'Level 2' | 'Level 3' | '판단 보류'
export type Verdict = 'PASS' | 'REVISE' | 'ABSTAIN'
export type ValueState = 'observed' | 'zero' | 'suppressed' | 'missing' | 'quality_suspect'
export type InterpretationStatus = '직접 확인' | '계산 재현' | '가설' | '미확인'
export type Direction = '감소' | '증가' | '변화 없음'
export type RunPhase =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'published'
  | 'answered'
  | 'abstained'
  | 'approved'
  | 'held'
  | 'failed'
export type Decision = 'approved' | 'held' | 'more'
export type Judgement = '적합' | '보류' | '부적합'

export const DISCLAIMER = '개인 판정 아님 · 최종 판단은 담당자'

/** API 오류 코드 (docs/API_명세.md §4). 모르는 코드도 올 수 있어 string 도 받음 */
export type ApiErrorCode =
  | 'INVALID_REQUEST'
  | 'EVIDENCE_NOT_IN_RUN'
  | 'RESOURCE_NOT_IN_RUN'
  | 'CANDIDATE_NOT_IN_RUN'
  | 'RUN_NOT_READY'
  | 'UNAUTHENTICATED'
  | 'FORBIDDEN'
  | 'PAYLOAD_TOO_LARGE'
  | 'REVIEW_REVISION_STALE'
  | 'RUN_NOT_FOUND'
  | 'SNAPSHOT_NOT_FOUND'
  | 'REPORT_REVISION_STALE'
  | 'RUN_NOT_AWAITING_APPROVAL'
  | 'APPROVAL_BLOCKED_BY_ABSTAIN'
  | 'EXTRA_ROUNDS_EXCEEDED'
  | 'IDEMPOTENCY_KEY_REUSED'
  | 'NO_ACTIVE_SNAPSHOT'
  | 'INTERNAL_ERROR'
  | 'AGENT_ERROR' // 실행 상태 errors[].error_code (그래프 예외)
  | 'RATE_LIMITED' // 429, Retry-After(초) 뒤 다시
  | 'BUDGET_EXCEEDED' // 실행 하나의 LLM 예산을 다 씀 (llm_calls 기록, 규칙·템플릿으로 대신)

export interface ErrorResponse {
  error_code: string
  message: string
  details: { code: string; rule?: string | null; message: string }[]
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  active_snapshot_id: string | null
  time: string
}

export interface SnapshotInfo {
  snapshot_id: string
  created_at: string
  data_from: string
  data_to: string
  cleaning_rule_version: string
  rule_version: string | null
  ledger_version: string | null
  previous_snapshot_id: string | null
  row_counts: Record<string, number>
}

export interface TriggerInfo {
  trigger_id: string
  rule: string
  name: string
  city: City
  starts_on: string
  note: string | null
}

export interface CandidateCountRange {
  count: number
  low: number
  high: number
  note: string
}

/** 지난 기준일(7일 전) 목록에는 있었고 이번 목록에는 없는 후보 + 해제 사유. 후보 정보는 지난 기준일 것 */
export interface ReleasedCandidate {
  candidate_id: string
  candidate_type: CandidateType
  city: City
  adm_cd: string | null
  check_first_adm: string[]
  analysis_cohort: string
  service_eligibility_group: string | null
  candidate_level: CandidateLevel | null
  trigger: string | null
  last_seen_as_of: string
  release_reason: string
}

export interface OverviewResponse {
  as_of: string
  city: City
  snapshot_id: string
  analysis_mode: AnalysisMode
  triggers: TriggerInfo[]
  candidate_counts: Partial<Record<CandidateType, CandidateCountRange>>
  new_candidate_ids: string[]
  released_candidate_ids: string[]
  time_label: string | null
  disclaimer: string
  // 선택 칸 (옛 응답에는 없음)
  released_candidates?: ReleasedCandidate[]
  compared_as_of?: string | null
  monitoring_run_id?: string | null
  rule_version?: string | null
}

export interface MapArea {
  adm_cd: string
  adm_nm: string
  structure_value: number | null
  structure_rank: number | null
  behavior_signal: '예정된 점검' | '이탈 경보(공유 맥락)' | '없음'
  is_shared_context: boolean
  nearest_hub_km: number | null
  value_state: ValueState
}

export interface MapResource {
  resource_id: string
  name: string
  resource_type: string
  lat: number | null
  lon: number | null
  phone_based: boolean
}

export interface MapResponse {
  as_of: string
  city: City
  snapshot_id: string
  structure_metric: string
  areas: MapArea[]
  resources: MapResource[]
  note: string
  monitoring_run_id?: string | null
}

export interface Candidate {
  candidate_id: string
  candidate_type: CandidateType
  city: City
  adm_cd: string | null
  check_first_adm: string[]
  analysis_cohort: string
  service_eligibility_group: string | null
  trigger: string | null
  input_status: InputStatus
  missing_inputs: string[]
  counter_result: CounterResult
  counter_reason: string | null
  candidate_level: CandidateLevel | null
  level_reason: string | null
  queue_rank: number | null
  selected_for_review: boolean
  selection_budget: number | null
  evidence_ids: string[]
  is_shared_context: boolean
  // 등급을 정한 규칙과 조건별 충족 여부 (없을 수 있음)
  rule_id?: string | null
  rule_version?: string | null
  rule_checks?: RuleCheck[]
  // 선택 칸 (없을 수 있음)
  event_id?: string | null
  event_opened_at?: string | null
  decided_at?: string | null
  evidence_confidence?: '높음' | '보통' | '낮음' | null
  response_type?: string | null
  cause_type?: string | null // 원인 유형 (후보 목록 응답). 자원 미리 보기를 이 필요 유형으로 좁힘
  need_types?: string[] // G
  // rules-v1.1 선택 칸 (옛 응답에는 없음)
  context?: ContextSignal[] // 카드 맥락·수급자 비율·1인세대 순증 등. 등급·순서에 쓰지 않음
  event_bundle_id?: string | null // 명절 묶음 (예: 춘천:명절:20251003). R1·R2·R3 후보가 같은 값
  event_timeline?: TimelineStep[] // 명절 전·중·후
  heat_stage?: string | null // R4: 폭염주의보 / 폭염경보 / 폭염중대경보 (상황 시연에서만)
}

/** 후보에 붙는 맥락 칸 (rules-v1.1). affects_level 은 늘 false: 등급·순서에 쓰지 않음 */
export interface ContextSignal {
  name: string
  value?: number | string | null
  unit?: string | null
  text: string
  role: '맥락' | '반증 맥락' | '구조 별도 칸'
  scope?: string | null
  evidence_id?: string | null
  affects_level?: boolean
}

/** 명절 전·중·후 묶음의 한 칸 (후보 event_timeline) */
export interface TimelineStep {
  phase: '전' | '중' | '후'
  code: 'R1' | 'R2' | 'R3'
  label: string
  start: string
  end: string
  status: '지남' | '지금' | '예정'
  applies?: boolean // 이 집단·도시에 적용되는 단계인지 (R3 는 춘천 고령층만)
}

export type Comparator = '>=' | '<=' | '>' | '<'

/** 등급 규칙의 조건 하나. met: true 충족 / false 미충족 / null 자료 없음 */
export interface RuleCheck {
  condition: string
  met: boolean | null
  value?: number | string | null
  threshold?: number | string | null
  comparator?: Comparator | null
  unit?: string | null
  input?: string | null
  level_if_met?: CandidateLevel | null
}

export interface CandidateListResponse {
  as_of: string
  city: City
  snapshot_id: string
  selection_budget: number
  items: Candidate[]
  monitoring_run_id?: string | null // 실제 그래프: 후보 ID 는 이 정기 감시 실행의 것 (POST /runs 에 같이 보냄)
}

export interface RunCreateRequest {
  mode: 'monitoring' | 'qa'
  question?: string | null
  candidate_id?: string | null
  city?: City | null
  as_of: string
  analysis_mode?: AnalysisMode
  monitoring_run_id?: string | null // 후보로 열 때 그 후보를 읽은 정기 감시 실행
  candidate_ids?: string[] // 후보로 열 때 함께 볼 후보 (같은 정기 감시 실행·같은 도시)
}

export interface RunAccepted {
  run_id: string
  status: 'queued'
  snapshot_id: string
  status_url: string
}

export interface GuardResult {
  guard: 'guard_signal' | 'guard_report'
  check: '신호' | '사실' | '표현'
  verdict: Verdict
  rule_ids: string[]
  reasons: string[]
  attempt: number
}

export interface ContractResult {
  status: 'OK' | 'ERROR'
  source: string | null
  issues: { code: string; rule?: string | null; message: string }[]
  analysis_cohort: string | null
  service_eligibility_group: string | null
  measured_grain: string | null
  warnings: string[]
}

export interface RunStatusResponse {
  run_id: string
  status: RunPhase
  mode: 'monitoring' | 'qa'
  route: 'lookup' | 'investigate' | 'out_of_scope' | null
  as_of: string
  analysis_mode: AnalysisMode
  snapshot_id: string
  path: string[]
  guard: GuardResult[]
  contract_check: ContractResult | null
  candidates: Candidate[]
  evidence_revision: number | null
  report_revision: number | null
  report_stale: boolean
  abstain_reason: string | null
  errors: Record<string, string>[]
  decision_trace?: TraceStep[] // "에이전트가 이렇게 판단함"
  // report_revision 은 요청한 판(다시 만드는 중이면 다음 판), 이것은 실제로 발행된 판.
  // 승인은 report_stale=false 이고 둘이 같을 때만 됨. 옛 응답에는 없음
  published_report_revision?: number | null
  extra_rounds?: number // 추가 조사 횟수 (최대 MAX_EXTRA_ROUNDS)
}

export interface ToolResult {
  evidence_id: string
  metric: string
  value: number | string | (number | null)[] | null
  unit: string | null
  denominator: string | null
  n_obs: number | null
  direction: Direction | null
  measured_grain: string | null
  applied_scope: string | null
  merchant_region: string | null
  customer_residence: string | null
  is_shared_context: boolean
  analysis_cohort: string | null
  service_eligibility_group: string | null
  observed_from: string | null
  observed_to: string | null
  analysis_mode: AnalysisMode
  as_of: string
  actual_available_at: string | null
  simulated_available_at: string | null
  availability_basis: 'reported' | 'assumed' | 'unknown'
  trained_until: string | null
  rule_derived_from: string | null
  snapshot_id: string | null
  data_version: string | null
  rule_version: string | null
  interpretation_status: InterpretationStatus
  valid_claims: string[]
  forbidden_claims: string[]
  missing_inputs: string[]
  warnings: string[]
  status: 'OK' | 'EMPTY' | 'ERROR'
  value_state: ValueState | null
  error_code: string | null
  calibrated_until: string | null
}

export interface Fact {
  metric: string
  region: string
  period: string
  value: number | string | null
  unit: string | null
  direction: Direction | null
  evidence_id: string
}

export interface StructuralRiskReference {
  candidate_id: string
  city: City
  adm_cd: string | null
  dong: string | null
  policy_group: string | null
  status: 'available' | 'unavailable' | 'not_applicable'
  period_label: string
  mapping_note: string | null
  comparison_note: string
  note: string
  source_path: string
  source_hash: string | null
  cells: {
    sex: string; age_band: string; pop: number | null; axes_on: ('S' | 'H' | 'V' | 'A')[]
    cell_type: '이중 위험' | '관계 단절형' | '구조·경제 취약형' | '해당 없음'
    s: number | null; h: number | null; v: number | null; a_km: number | null; small: boolean
    unverified?: ('H' | 'S')[]; check_notes?: string[]
  }[]
}

export interface EvidenceCard {
  card_id: string
  run_id: string | null
  candidate_id: string | null
  title: string
  snapshot_id: string
  analysis_mode: AnalysisMode
  as_of: string
  evidence_revision: number
  structural_risk?: StructuralRiskReference[]
  facts: Fact[]
  evidence: ToolResult[]
  limitations: string[]
  time_label: string | null
  disclaimer: string
}

export interface EvidenceResponse {
  run_id: string
  snapshot_id: string
  evidence_card: EvidenceCard
}

// ---- 담당자용 출력, 등급 설명, 판단 기록

export type CheckAreaName = '건강·복약' | '식사·생활' | '사회적 관계·연락' | '이동·외출' | '주거·안전' | '경제·지원'
export const CHECK_AREA_NOTE = '판단도구 문항 채점 아님, 현장에서 먼저 물어볼 영역'
export const LIST_NOTE = '시스템은 명단을 만들지 않음. 기존 명단 중 어느 것을 먼저 꺼낼지만 안내'
export const DISTRIBUTION_INTERNAL = '내부용(대외비)'

/** 확인할 영역. 판단도구 문항 채점이 아니라 현장에서 먼저 물어볼 영역 */
export interface CheckArea {
  area: CheckAreaName
  reasons: string[]
  ask: string | null
}

/** 대응 템플릿 5칸: 주체 / 방법 / 기한 / 확인할 영역 / 연계 자원 */
export interface ActionPlan {
  owner: string[]
  method: string
  due: string
  due_date: string | null
  check_areas: CheckAreaName[]
  linked_resources: string[]
  tasks: string[]
  resource_note: string | null
  emergency_contacts: string[] // 위급 시 안내 (순위 밖, 따로 한 줄)
  text: string // 보고서용 한 줄
  // action-v2 선택 칸 (옛 응답에는 없음)
  heat_stage?: string | null // 폭염주의보·경보·중대경보 / 특보 단계 확인 필요 (R4 일 때만)
  contact_frequency?: string | null // 그 단계의 안부 횟수 + 순서
  holiday_timeline?: HolidayPhase[] // 명절 전·중·후 3칸
  partners?: string[] // 사전 안부 협력자 (임대인·관리사무소·고시원 운영자)
  partner_note?: string | null
  link_steps?: LinkStep[] // 사회적 처방형 3단계 (안내 → 연결 → 확인)
  bundle?: BundleItem[] // 연결 묶음 자원 (중장년 남성)
}

/** 명절 전·중·후 한 칸. current 는 이 후보의 트리거 단계 */
export interface HolidayPhase {
  phase: '전' | '중' | '후'
  rule: string
  label: string
  window: string
  tasks: string[]
  contact: string | null
  current: boolean
}

export interface LinkStep {
  step: '안내' | '연결' | '확인'
  text: string
}

export interface BundleItem {
  need: string
  resource_id: string
  name: string
  phone: string | null
}

// ---- 보호 자원 칸 (GET /resources/protective). 위험 표시가 아니라 그 동에 이미 있는 연결 자원

export interface ProtectiveItem {
  resource_id: string
  name: string
  subtype: string
  address: string | null
  phone: string | null
  schedule: string | null
  holiday_open: string
  source_url: string | null
  last_verified: string | null
  scope: '동' | '도시'
}

export interface ProtectiveGroup {
  group: string
  label: string
  in_dong: number | null // null = 동별 자료 없음 (0 과 다름)
  city_total: number | null
  items: ProtectiveItem[]
  status: '수집' | '도시 집계만' | '미수집'
  note: string | null
}

export interface ProtectiveResponse {
  city: City
  adm_cd: string | null
  adm_nm: string | null
  groups: ProtectiveGroup[]
  note: string
}

// ---- 이의·정정 기록 (POST/GET /objections)

export const OBJECTION_REASONS = [
  '후보가 아님(현장 사정)',
  '근거 자료 오류',
  '등급이 맞지 않음',
  '자원 정보 오류',
  '낙인·표현 우려',
  '기타',
] as const
export type ObjectionReason = (typeof OBJECTION_REASONS)[number]
export type ObjectionStatus = '접수' | '검토 중' | '반영' | '기각'

export interface ObjectionRequest {
  target_type: 'candidate' | 'run'
  run_id?: string | null
  candidate_id?: string | null
  city: City
  adm_cd?: string | null
  reason_category: ObjectionReason
  text?: string | null
  idempotency_key: string
}

export interface ObjectionRecord {
  objection_id: string
  target_type: 'candidate' | 'run'
  run_id: string | null
  candidate_id: string | null
  city: string
  adm_cd: string | null
  reason_category: string
  text: string | null
  status: ObjectionStatus
  resolution: string | null
  submitted_by: string
  resolved_by: string | null
  created_at: string
  updated_at: string
}

export interface ObjectionResponse {
  objection: ObjectionRecord
  duplicate: boolean
}

export interface ObjectionListResponse {
  items: ObjectionRecord[]
  total: number
}

/** 내일 아침 체크리스트. Level 2·3 후보만 */
export interface MorningChecklist {
  lists: string[] // ① 꺼낼 명단 (기존 시스템의 명단)
  contacts: string[] // ② 연락할 사람·기관
  method: string // ③ 방법
  due: string // ④ 기한
  check_areas: CheckAreaName[] // ⑤ 확인할 영역
  note: string
}

export interface CandidateAction {
  candidate_id: string
  candidate_level: CandidateLevel | null
  group: string | null
  where: string | null
  check_areas: CheckArea[]
  check_area_note: string
  action_plan: ActionPlan | null
  checklist: MorningChecklist | null
}

export interface RuleCheckView {
  condition: string
  met: boolean | null
  value: number | string | null
  threshold: number | string | null
  distance: number | null // 기준까지 남은 차이 (미충족) 또는 여유 (충족)
  text: string
}

export interface LevelExplanation {
  candidate_id: string
  candidate_level: CandidateLevel | null
  rule_id: string | null
  rule_version: string | null
  checks: RuleCheckView[]
  what_if: string[]
  lines: string[]
}

export type TraceSource = 'rule' | 'llm' | 'guard' | 'human' | 'template'

export interface TraceStep {
  node: string
  choice: string | null
  next: string | null
  source: TraceSource | null
  rule_ids: string[]
  reason: string | null
  inputs: Record<string, unknown>
  changed: string[]
  started_at: string | null
  ms: number | null
}

export interface Report {
  report_revision: number
  evidence_card: EvidenceCard
  fact_sentences: string[]
  narrative: string | null
  is_abstain: boolean
  needs_regeneration: boolean
  // 아래는 선택 칸. 옛 응답에는 없을 수 있음
  actions?: CandidateAction[]
  level_explanations?: LevelExplanation[]
  decision_trace?: TraceStep[]
  distribution?: '내부용(대외비)'
  external_notice?: string
  // 추가 조사 뒤 "N회차 대비 달라진 점" (fact_sentences 에도 들어 있음), 이 판에 반영한 검토 ID
  round_changes?: string[]
  review_ids?: string[]
  investigation?: Investigation | null // 조사 루프에서 추가로 확인한 것 (등급·반증 입력 아님)
}

/** 조사 루프에서 확인한 질문 하나 */
export interface InvestigationItem {
  key: string
  qid: string
  label: string
  question: string
  city?: string | null
  adm_cd?: string | null
  candidate_ids?: string[]
  tool: string
  round: number
  source: 'llm' | 'rule'
  why?: string | null
  status: 'answered' | 'unanswered' | 'no_data' | 'error' | 'dropped' | 'quarantined'
  finding: string
  evidence_ids: string[]
  /** 확인 못 함(quarantined)의 사유 코드 (G-08·G-11·G-14·도구 오류 코드) */
  reason_code?: string | null
}

/** 보고서 "추가로 확인한 것" 칸 */
export interface Investigation {
  rounds: number
  items: InvestigationItem[]
  not_picked?: string[]
  stopped?: string | null
  note?: string
}

export interface ResourceJudgement {
  resource_id: string
  judgement: Judgement
  memo?: string | null
}

export interface ReviewRequest {
  reviewer: string
  checked_evidence_ids: string[]
  resource_judgements: ResourceJudgement[]
  memo?: string | null
  follow_up_on?: string | null
  report_revision?: number | null // 화면에서 본 보고서 판. 지금 판과 다르면 409 REVIEW_REVISION_STALE
}

export interface ReviewResponse {
  run_id: string
  review_id: string
  report_revision: number
  report_stale: boolean // 저장 뒤 실행의 실제 상태
  changed?: boolean // 이번 저장으로 자원 판정이 바뀌었나
  evidence_revision?: number | null
}

// ---- 추가 조사 범위 (ApproveRequest.focus). 값은 schemas.py 의 화이트리스트와 같다
export const MAX_EXTRA_ROUNDS = 2
export const MAX_FOCUS_MEMO = 500
export const MAX_QUESTION = 500
export const EXTRA_TOOLS = ['get_mobility_layer', 'get_validation_reference', 'calc_holiday_gap', 'calc_winter_contraction', 'calc_heat_response'] as const
export type ExtraTool = (typeof EXTRA_TOOLS)[number]
export const COUNTER_CHECKS = ['공휴일 기대값', '도시 공통요인', '강수', '학사일정'] as const
export type CounterCheck = (typeof COUNTER_CHECKS)[number]

/** 추가 조사에서 담당자가 정하는 범위. 메모 원문은 보고서·기록에 남지 않음 */
export interface MoreFocus {
  candidate_ids?: string[] // 이 실행의 후보 ID만 (아니면 400 CANDIDATE_NOT_IN_RUN)
  tools?: ExtraTool[]
  counter_checks?: CounterCheck[]
  memo?: string | null // MAX_FOCUS_MEMO 자
}

export interface ApproveRequest {
  decision: Decision
  report_revision: number
  approver: string
  idempotency_key: string
  memo?: string | null
  focus?: MoreFocus | null // decision=more 일 때만
}

export interface ApproveResponse {
  run_id: string
  decision: Decision
  report_revision: number
  approval_id: string
  duplicate: boolean
  approver?: string // 서버가 기록한 승인자 (인증을 켜면 토큰의 이름). 옛 서버는 없음 → 이력의 approver 를 씀
}

export interface QARequest {
  question: string
  as_of: string
  analysis_mode?: AnalysisMode
  city?: City | null
}

export interface QAResponse {
  run_id: string
  status: 'answered' | 'abstained'
  evidence_card: EvidenceCard | null
  answer: string | null
  guard: GuardResult[]
  abstain_reason: string | null
  needed_data: string[]
}

export interface ResourceRecommendation {
  resource_id: string
  name: string
  resource_type: string
  city: City
  adm_cd: string | null
  target_group: string | null
  eligibility: '적합' | '확인 필요' | '부적합'
  operating_status: '운영' | '확인 필요' | '종료'
  distance_km: number | null
  phone_based: boolean
  source_url: string
  verified_date: string
  warnings: string[]
}

/** 이어 묻기: 이 실행 근거 카드 안에서만 답함 */
export interface FollowupRequest {
  question: string
}

export interface FollowupResponse {
  run_id: string
  followup_id: string
  status: 'answered' | 'abstained'
  answer: string | null
  cited_evidence_ids: string[]
  source: 'llm' | 'template' | null
  report_revision: number | null
  guard: GuardResult[]
  abstain_reason: string | null
  needed_data: string[]
  // 질문 종류와 답한 대상 (후보 ID·동 코드만, 질문 원문 없음). 옛 응답에는 없음
  intent?: FollowupIntent | null
  target?: Record<string, string[]> | null
}

export type FollowupIntent = 'why_level' | 'resources' | 'compare' | 'data_limits' | 'other'

/** 실행에 묶인 추천 자원 (실행 시작 때 고정한 스냅샷·원장) + 가장 최근 검토의 판정.
 * 자원 하나에 한 줄. candidate_ids 는 이 자원을 추천받은 후보 모두, candidate_id 는 그 첫 후보 (옛 칸) */
export interface RunResource extends ResourceRecommendation {
  candidate_id?: string | null
  candidate_ids?: string[]
  judgement?: Judgement | null
}

export interface RunResourcesResponse {
  run_id: string
  snapshot_id: string
  evidence_revision?: number | null
  report_revision?: number | null
  items: RunResource[]
}

export interface ResourceListResponse {
  adm_cd: string | null
  need_type: string | null
  group: string | null
  items: ResourceRecommendation[]
  ledger_version?: string | null // 실제 원장 버전
  total?: number | null // 거른 뒤 전체 수 (items 는 limit 까지)
  cause_type?: string | null // 원인 유형으로 좁힌 경우
  need_types?: string[] // 그 원인의 필요 유형
  scope_total?: number | null // 필요 유형으로 거르기 전 (관할·대상·제외 규칙만) 수
}

/** 후속 확인 한 건 (GET /followups): 후보마다 가장 최근 조사 실행의 마지막 후속 확인일 */
export type FollowState = '지남' | '이번 주' | '나중'
export interface FollowUpItem {
  candidate_id: string
  run_id: string
  city: City | null
  follow_up_on: string
  memo: string | null
  review_id: string
  reviewer: string
  run_status: RunPhase
  approved: boolean
  state?: FollowState | null // as_of 를 주면
}

export interface FollowUpListResponse {
  as_of: string | null
  city: City | null
  date_from: string | null
  date_to: string | null
  items: FollowUpItem[]
}

export interface SourceCapability {
  source: string
  name: string
  dimensions: string[]
  cities: City[]
  measured_grain: string
  period_from: string
  period_to: string
  age_bands: string[]
  observed_window_status: string | null
}

export interface CapabilitiesResponse {
  grain_version: string
  sources: SourceCapability[]
  forbidden_combos: Record<string, unknown>[]
}

// ---- 실행 목록·이력 등 (api/models.py)

export interface RunSummary {
  run_id: string
  mode: 'monitoring' | 'qa'
  status: RunPhase
  route: string | null
  question: string | null
  candidate_id: string | null
  city: string | null
  as_of: string
  analysis_mode: AnalysisMode
  snapshot_id: string
  cleaning_rule_version: string | null
  rule_version: string | null
  ledger_version: string | null
  evidence_revision: number
  report_revision: number
  report_stale: boolean
  extra_rounds: number
  created_at: string
  updated_at: string
}

export interface RunListResponse {
  items: RunSummary[]
}

export interface RunHistoryResponse {
  run_id: string
  report_revisions: { report_revision: number; evidence_revision: number; snapshot_id: string; reason: string; created_at: string }[]
  reviews: {
    review_id: string
    reviewer: string
    checked_evidence_ids: string[]
    resource_judgements: ResourceJudgement[]
    memo: string | null
    follow_up_on: string | null
    report_revision_before: number
    report_revision_after: number
    created_at: string
  }[]
  approvals: {
    approval_id: string
    idempotency_key: string
    decision: Decision
    report_revision: number
    approver: string
    memo: string | null
    created_at: string
  }[]
  finalized: string[]
}

export interface ReportResponse {
  run_id: string
  snapshot_id: string
  report_stale: boolean
  report: Report
}

/** 현장 확인 결과. 승인된 조사 실행의 후보(동·집단)마다 건수만 (이름·주소·메모 없음) */
export const OUTCOME_FIELDS = ['checked_households', 'contacted', 'absent', 'new_links', 'crises_found'] as const
export type OutcomeField = (typeof OUTCOME_FIELDS)[number]
export const OUTCOME_LABEL: Record<OutcomeField, string> = {
  checked_households: '확인 가구',
  contacted: '연락됨',
  absent: '부재',
  new_links: '신규 연계',
  crises_found: '위기 발견',
}
export type OutcomeCounts = Record<OutcomeField, number>

export interface OutcomeRequest extends OutcomeCounts {
  candidate_id: string
  idempotency_key: string
}

export interface OutcomeRecord extends OutcomeCounts {
  outcome_id: string
  run_id: string
  candidate_id: string
  city: City
  adm_cd: string | null
  group: string | null
  trigger_code: string
  event_id: string | null
  recorded_by: string
  created_at: string
}

export interface OutcomeResponse {
  run_id: string
  outcome: OutcomeRecord
  duplicate: boolean
}

export interface OutcomeListResponse {
  run_id: string
  items: OutcomeRecord[]
  history_count: number
}

/** 모아 보기 한 줄. 합계 10 미만 칸은 null + small_cells (화면은 "10 미만") */
export type OutcomeSummaryRow = { [K in OutcomeField]: number | null } & {
  city: City
  trigger_code: string
  event_id: string | null
  runs: number
  places: number
  small_cells: string[]
  contact_rate: number | null
  link_rate: number | null
  crisis_rate: number | null
}

export interface OutcomeSummaryResponse {
  city: City | null
  by: 'trigger' | 'event'
  small_cell: number
  rows: OutcomeSummaryRow[]
  note: string
}

export interface MeResponse {
  name: string
  role: '조회' | '검토' | '승인'
  shared?: boolean
  authenticated?: boolean
}
