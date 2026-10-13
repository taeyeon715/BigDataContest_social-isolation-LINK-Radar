import type { Candidate, CandidateLevel, CandidateType, RunPhase } from '../api'
import { CENTROIDS } from '../map/centroids'

export const TYPE_LABEL: Record<CandidateType, string> = {
  scheduled_access_review: '예정된 점검',
  unexpected_activity_deviation: '예상 밖 이탈',
}

export const LEVEL_NAME: Record<CandidateLevel, string> = {
  'Level 0': '일반 관찰',
  'Level 1': '변화 관찰',
  'Level 2': '검토 후보',
  'Level 3': '우선 현장 검토',
  '판단 보류': '근거 불충분',
}

export const PHASE_LABEL: Record<RunPhase, string> = {
  queued: '대기',
  running: '실행 중',
  awaiting_approval: '승인 대기',
  published: '후보 목록 발행',
  answered: '답변',
  abstained: '보류 답변',
  approved: '확정',
  held: '보류 기록',
  failed: '실패',
}

export const NODE_LABEL: Record<string, string> = {
  intake: '요청 정리',
  classify: '경로 분류',
  check_availability: '계약 검사',
  lookup: '값 조회',
  collect_signals: '신호 모으기',
  classify_candidate: '후보 종류',
  counter_evidence: '반증',
  guard_signal: '신호 검사',
  assign_level: '등급',
  assign_queue: '검토 배정',
  match_resources: '자원 찾기',
  build_card: '근거 카드',
  write_report: '보고서',
  guard_report: '보고서 검사',
  await_approval: '승인 대기',
  publish_candidates: '후보 발행',
  abstain_report: '보류 보고서',
  finalize: '확정',
}

export const MODE_LABEL: Record<string, string> = {
  retrospective: '사후 분석',
  simulated_replay: '과거 재생',
  strict_as_of: '기준일 엄격',
}

/** 반증 결과를 화면용으로 ("반증"은 화면에서 "다른 원인 확인"으로 씀). `보류`는 사유 앞머리로 "검토 중" / "도시 공통 변화 / 확인 필요"를 나눈다. */
export type CounterView = { label: string; tone: 'pass' | 'reject' | 'hold' | 'check' | 'review' | 'none'; reason: string | null }

export function counterView(c: Pick<Candidate, 'counter_result' | 'counter_reason'>): CounterView {
  const reason = c.counter_reason
  switch (c.counter_result) {
    case '통과':
      return { label: '다른 원인 없음', tone: 'pass', reason }
    case '기각':
      return { label: '기각', tone: 'reject', reason }
    case '해당 없음':
      return { label: '다른 원인 확인 해당 없음', tone: 'none', reason: '예정된 점검은 다른 원인 확인(반증) 대상이 아님' }
    case '보류': {
      if (reason?.startsWith('검토 중')) return { label: '검토 중', tone: 'review', reason: stripHead(reason) }
      if (reason?.startsWith('도시 공통 변화')) return { label: '도시 공통 변화 / 확인 필요', tone: 'check', reason: stripHead(reason) }
      return { label: '다른 원인 확인 보류', tone: 'hold', reason }
    }
  }
}

function stripHead(s: string): string {
  const i = s.indexOf(':')
  return i >= 0 ? s.slice(i + 1).trim() : s
}

export function admName(adm_cd: string | null | undefined): string {
  if (!adm_cd) return ''
  return CENTROIDS[adm_cd]?.adm_nm ?? adm_cd
}

/** 후보 지역 표시: 동 후보는 동 이름, 이탈 후보는 "도시 × 코호트" */
export function candidatePlace(c: Pick<Candidate, 'city' | 'adm_cd' | 'analysis_cohort'>): string {
  if (c.adm_cd) return `${c.city} ${admName(c.adm_cd)}`
  return `${c.city} 전체 · ${c.analysis_cohort}`
}

const GROUP_BY_SERVICE: Record<string, string> = {
  '65세 이상': '고령층',
  '40~64세 남성': '중장년 남성 1인가구',
  '20~34세 남성': '청년 남성',
}

export function groupOf(c: Pick<Candidate, 'service_eligibility_group'>): string | null {
  return c.service_eligibility_group ? GROUP_BY_SERVICE[c.service_eligibility_group] ?? null : null
}

/** 탭: 기각·판단 보류는 종류와 상관없이 "기각·보류" */
export type CandidateTab = 'scheduled' | 'deviation' | 'rejected'
export function tabOf(c: Candidate): CandidateTab {
  if (c.counter_result === '기각' || c.candidate_level === '판단 보류') return 'rejected'
  return c.candidate_type === 'scheduled_access_review' ? 'scheduled' : 'deviation'
}

export function fmtValue(v: unknown, unit?: string | null): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') {
    const s = unit === '비율' || unit?.includes('비율') ? v.toFixed(2) : String(v)
    return unit && !unit.includes('비율') ? `${s} ${unit}` : s
  }
  if (Array.isArray(v)) return v.map((x) => (x === null ? '—' : String(x))).join(', ')
  return String(v)
}

export function fmtTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function daysBetween(fromIso: string, toIso: string): number {
  return Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 86400000)
}

/** 등급 범례 (0~3). 위험 점수가 아니라 먼저 확인할 순서 */
export const LEVEL_LEGEND = 'Level 0 일반 관찰 · Level 1 변화 관찰 · Level 2 검토 후보 · Level 3 우선 현장 검토. 위험 점수가 아니라 먼저 확인할 순서입니다.'

/** 근거 해석 상태를 쉬운 말로 */
export const INTERP_LABEL: Record<string, string> = {
  '직접 확인': '직접 확인',
  '계산 재현': '계산값',
  가설: '참고(확정 아님)',
  미확인: '확인 안 됨',
}

/** 필수 근거 이름 (configs/candidate_inputs.yaml inputs + 가짜 API 의 품질 사유) */
export const INPUT_LABEL: Record<string, string> = {
  calendar_trigger: '달력·계절·기상특보 트리거',
  target_group: '대상 집단',
  structure_vulnerability: '구조 취약성 (주민등록 1인세대)',
  resident_structure: '주민등록 세대 구조',
  access_gap: '접근성 (관측 거점까지 직선거리)',
  card_cohort_series: '카드 결제 일별 흐름',
  expected_activity: '평소 활동 기준선',
  cohort_deviation: '이탈 경보',
  holiday_expectation: '공휴일 기대값 (다른 원인 확인)',
  city_common_factor: '도시 전체 변화 (다른 원인 확인)',
  weather_asos: '기상 관측 날씨 (ASOS)',
  academic_calendar: '학사일정 (다른 원인 확인)',
  mobility_layer: '통신 인구 구성',
  winter_contraction: '춘천 읍·면 겨울 변화',
  seasonal_baseline: '계절 기준선',
  validation_reference: '시민생활 지표 (참고)',
  resources: '복지 자원',
}

/** "weather_asos" / "resident_structure (개포3동 7개 지표 0, 품질 의심)" → 한국어 이름 — 사유 */
export function missingLabel(raw: string): string {
  const m = raw.match(/^([a-z_]+)\s*(?:\((.*)\))?\s*$/)
  if (!m) return raw
  const name = INPUT_LABEL[m[1]] ?? m[1]
  return m[2] ? `${name} — ${m[2]}` : name
}

/** 문장 안에 섞인 근거 코드를 한국어 이름으로 (예: "빠진 근거: weather_asos") */
export function replaceInputCodes(text: string): string {
  return text.replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (code) => INPUT_LABEL[code] ?? code)
}

/** 오류·보류 코드 → 쉬운 문장 (코드는 작게 따로 보임) */
export const ERROR_TEXT: Record<string, string> = {
  NETWORK_ERROR: 'API 서버에 연결할 수 없습니다.',
  GRAIN_FORBIDDEN_COMBO: '질문에 필요한 조합(예: 나이와 시간대)을 함께 담은 자료가 없습니다.',
  TIME_NO_USABLE_DATA: '이 기준일에 쓸 수 있는 자료가 없습니다.',
  RUN_NOT_READY: '조사가 아직 끝나지 않았습니다. 잠시 뒤 다시 보세요.',
  RUN_NOT_APPROVED: '승인된 조사에만 현장 확인 결과를 적을 수 있습니다.',
  REPORT_REVISION_STALE: '보고서가 새 판으로 바뀌었습니다.',
  REVIEW_REVISION_STALE: '보고서가 새 판으로 바뀌어 검토를 저장하지 못했습니다.',
  REPORT_NOT_SHOWN: '화면에 보이는 보고서와 지금 판이 다릅니다.',
  // 실제 그래프가 보내는 코드. 서버 문장보다 이 문장을 먼저 보인다 (PLAIN_FIRST)
  CANDIDATE_NOT_IN_RUN: '고른 후보 중 이 조사에 없는 후보가 있습니다. 이 조사의 후보만 고르세요.',
  AGENT_ERROR: '조사 중 문제가 생겨 멈췄습니다. 새 실행으로 다시 조사하세요.',
  IDEMPOTENCY_KEY_REUSED: '같은 요청이 다른 내용으로 이미 들어와 있습니다. 화면을 새로 고친 뒤 다시 하세요.',
  RUN_NOT_AWAITING_APPROVAL: '이미 승인·보류로 끝났거나 승인 대기가 아닌 조사입니다. 화면을 새로 고쳐 지금 상태를 보세요.',
  EXTRA_ROUNDS_EXCEEDED: '추가 조사는 2번까지입니다. 이제 승인하거나 보류하세요.',
  // 이어 묻기 보류 사유 (abstain_reason 앞의 코드)
  OUT_OF_SCOPE_INDIVIDUAL: '특정 개인을 가리키는 질문에는 답하지 않습니다.',
  OUT_OF_SCOPE: '이 보고서 근거에 없는 범위라 답하지 않습니다. 새 실행으로 물어보세요.',
  NO_TARGET: '이 보고서에 맞는 대상이 없습니다. 새 조사를 시작하세요.',
  EMPTY: '이 실행에는 근거 카드가 없어 답할 수 없습니다.',
  'G-05': '답이 사실 검사를 통과하지 못해 답하지 않았습니다.',
}

/** 서버 문장보다 쉬운 문장을 먼저 보일 오류 코드 */
const PLAIN_FIRST = new Set(['CANDIDATE_NOT_IN_RUN', 'RUN_NOT_READY', 'AGENT_ERROR', 'IDEMPOTENCY_KEY_REUSED', 'RUN_NOT_AWAITING_APPROVAL', 'EXTRA_ROUNDS_EXCEEDED'])

/** 오류를 화면에 보일 문장: 제목(쉬운 문장)과, 서버 문장이 다르면 보조 문장 */
export function errorText(code: string | null | undefined, message: string | null | undefined, fallback = '요청을 처리하지 못했습니다.'): { title: string; detail: string | null } {
  const plain = code ? ERROR_TEXT[code] : undefined
  const title = (code && PLAIN_FIRST.has(code) ? plain : undefined) || message || plain || fallback
  return { title, detail: message && message !== title ? message : null }
}

/** 보류 사유 "CODE: 설명" 을 코드와 설명으로 나눔 (코드가 없으면 code=null) */
export function splitReason(reason: string | null | undefined): { code: string | null; text: string } {
  const r = (reason ?? '').trim()
  const m = r.match(/^([A-Z][A-Z0-9_]*|G-\d+):\s*(.*)$/s)
  return m ? { code: m[1], text: m[2] } : { code: null, text: r }
}

/** 추가 조사에 고를 수 있는 도구 (등급 계산에는 쓰지 않는 추가 도구만) */
export const TOOL_LABEL: Record<string, string> = {
  get_mobility_layer: '이동·체류 자료 더 보기',
  get_validation_reference: '검증용 참고 자료',
  calc_holiday_gap: '연휴 전후 차이 계산',
  calc_winter_contraction: '겨울철 활동 위축 계산',
  calc_heat_response: '폭염 때 반응 계산',
}

/** 판단 기록의 "누가 정했나" */
export const TRACE_SOURCE_LABEL: Record<string, string> = {
  rule: '규칙',
  llm: 'LLM',
  guard: '검사',
  human: '사람',
  template: '템플릿',
}

/** 보고서 문장 중 대응안 한 줄 (대응안 표로 따로 보이므로 사실 문장 목록에서는 뺌) */
export function isActionSentence(s: string, actionTexts: string[]): boolean {
  return actionTexts.includes(s) || s.startsWith('대응안 ')
}

/** "N회차 대비 달라진 점" 제목과 문장 (문장 앞의 "N회차 대비"는 떼고 도구 이름은 쉬운 말로) */
export function roundChangesView(lines: string[]): { title: string; items: string[] } {
  const n = lines.map((l) => l.match(/^(\d+)회차 대비/)?.[1]).find(Boolean)
  const items = lines.map((l) =>
    l
      .replace(/^\d+회차 대비\s*(달라진 점:\s*)?/, '')
      .replace(/\b(get_[a-z_]+|calc_[a-z_]+)\b/g, (m) => TOOL_LABEL[m] ?? m),
  )
  return { title: n ? `${n}회차 대비 달라진 점` : '지난 회차 대비 달라진 점', items }
}
