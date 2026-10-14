import { DISCLAIMER, type Candidate, type CandidateLevel, type CandidateType, type RunPhase } from '../api'
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

/** Guard 검사 결과 (화면용) */
export const VERDICT_LABEL: Record<string, string> = { PASS: '통과', REVISE: '다시 확인', ABSTAIN: '판단 보류' }

export const MODE_LABEL: Record<string, string> = {
  retrospective: '사후 분석',
  simulated_replay: '과거 재생',
  strict_as_of: '기준일 엄격',
}

/** 반증 결과를 화면용으로 ("반증"은 화면에서 "다른 원인 확인"으로 씀). `보류`는 사유 앞머리로 "검토 중" / "도시 공통 변화 / 확인 필요"를 나눈다. */
export type CounterView = { label: string; tone: 'pass' | 'reject' | 'hold' | 'check' | 'review' | 'none'; reason: string | null }

export function counterView(c: Pick<Candidate, 'counter_result' | 'counter_reason'>): CounterView {
  const reason = c.counter_reason ? plainText(c.counter_reason) : c.counter_reason
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
  access_gap: '접근성 (노인복지관·사회복지관까지 직선거리)',
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
  TIMEOUT: 'API가 제시간에 답하지 않았습니다. 잠시 뒤 다시 시도하세요.',
  FORBIDDEN: '이 일을 할 권한이 없습니다. 조사·질문·검토 저장은 검토 권한, 승인은 승인 권한이 필요합니다.',
  RATE_LIMITED: '짧은 시간에 요청이 많아 잠시 막혔습니다. 조금 뒤 다시 시도하세요.',
  BUDGET_EXCEEDED: '이 조사에 쓸 수 있는 AI 사용량을 다 써서, 규칙과 정해진 문장으로 대신 만들었습니다.',
  BAD_RESPONSE: 'API 응답을 읽을 수 없습니다.',
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
const PLAIN_FIRST = new Set(['FORBIDDEN', 'RATE_LIMITED', 'BUDGET_EXCEEDED', 'CANDIDATE_NOT_IN_RUN', 'RUN_NOT_READY', 'AGENT_ERROR', 'IDEMPOTENCY_KEY_REUSED', 'RUN_NOT_AWAITING_APPROVAL', 'EXTRA_ROUNDS_EXCEEDED'])

/** 오류를 화면에 보일 문장: 제목(쉬운 문장)과, 서버 문장이 다르면 보조 문장 */
export function errorText(
  code: string | null | undefined,
  message: string | null | undefined,
  fallback = '요청을 처리하지 못했습니다.',
  retryAfter: number | null = null,
): { title: string; detail: string | null } {
  let plain = code ? ERROR_TEXT[code] : undefined
  if (code === 'RATE_LIMITED' && retryAfter) plain = `짧은 시간에 요청이 많아 잠시 막혔습니다. ${retryAfter}초 뒤 다시 시도하세요.`
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

/** 추가 도구 한 줄 설명 (추가 조사 범위 고르기) */
export const TOOL_NOTE: Record<string, string> = {
  get_mobility_layer: '통신 체류 인구 구성 (참고)',
  get_validation_reference: '서울 시민생활 지표 (참고)',
  calc_holiday_gap: '연휴 중·뒤 결제가 평소와 얼마나 다른지',
  calc_winter_contraction: '춘천 읍·면 겨울 활동 변화',
  calc_heat_response: '폭염일 생필품 결제 차이',
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

/** 결정 이름 (승인 기록·인쇄·실행 기록이 같이 씀) */
export const DECISION_LABEL: Record<string, string> = { approved: '승인', held: '보류', more: '추가 조사' }
/** 보고서 판을 만든 이유 */
export const REVISION_REASON_LABEL: Record<string, string> = { initial: '처음', review_changed: '검토 변경', guard_revise: '문장 검사 뒤 고침', extra_round: '추가 조사' }

// ---- 접근성 거리 이름 (심사 지적: "거점 18.6km"인데 같은 화면에 "행정복지센터 1.7km")
// 서버 지표 이름 "관측 거점까지 직선거리"의 거점은 노인복지관·종합사회복지관(안부·관계 프로그램)만 뜻한다.
export const HUB_LABEL = '노인복지관·사회복지관(안부·관계 거점)까지 거리'
export const HUB_SHORT = '안부·관계 거점까지'
export const HUB_NOTE = '행정복지센터(주민센터)는 안부·관계 프로그램을 운영하지 않아 이 거리에 넣지 않습니다. 직선거리이며 실제 이동 시간이 아닙니다.'

/** 노인복지관·사회복지관 거리에 넣는 자원인가 (이름·종류로 판단). 행정복지센터는 넣지 않음 */
export function isHubResource(r: { name: string; resource_type: string }): boolean {
  const t = `${r.name} ${r.resource_type}`
  if (/행정복지센터|주민센터|읍사무소|면사무소/.test(t)) return false
  return /노인복지관|사회복지관/.test(t)
}

/** 행정 창구(행정복지센터 등): 거점 거리에 넣지 않는다는 짧은 표시용 */
export function isAdminOffice(r: { name: string; resource_type: string }): boolean {
  return /행정복지센터|주민센터|읍사무소|면사무소/.test(`${r.name} ${r.resource_type}`)
}

/** 서버 문장 속 낯선 이름을 화면 이름으로 (거점 거리 이름 등). 데이터 값은 그대로 둠 */
export function plainText(s: string): string {
  return hideInternal(s)
    .replace(/관측(된)? 거점(까지)?\s*(직선)?\s*거리/g, HUB_LABEL)
    .replace(/관측(된)? 거점/g, '안부·관계 거점(노인복지관·사회복지관)')
    .replace(/거리은\(는\)/g, '거리는')
}

// ---- 화면 문장에서 내부 표시 빼기: 규칙 판(rules-v1.1)·설정 파일(rules.yaml)·설정 열쇠(context_only)·규칙 ID(L2-…)·영어 값
// 판·규칙 ID 는 등급 조건 "자세히"와 판단 기록·인쇄 꼬리말에 남는다
const ENUM_LABEL: Record<string, string> = { ...TYPE_LABEL, ...MODE_LABEL, strict_as_of: '기준일 엄격 모드' }
const CONFIG_KEY_LABEL: Record<string, string> = {
  context_only: '참고용 맥락, 등급에 쓰지 않음',
}
// 등급 설명 첫 줄·이어 묻기 답 "적용 규칙 L2-… (rules-v1.1) → Level 2" → "적용 규칙 → Level 2"
const APPLIED_RULE_RE = /적용 규칙 (?!기록 없음)\S+(?: \([^→]*\))? → /g
const RULE_TAG_RE = /\s*\[rules-[^\]]*\]/g
const RULE_ID_SRC = String.raw`(?:L[0-3]|HOLD|CAP)-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*`
const RULE_IDS_RE = new RegExp(String.raw`\s*\b${RULE_ID_SRC}(?:\+${RULE_ID_SRC})*\b`, 'g')
const CONFIG_RE = /(?:\brules-v\d+(?:\.\d+)*|\b[a-z][a-z0-9_]*\.yaml\b)(?:\s+([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*))?/g
// 뒤에 띄어 쓴 조사(" 에서")는 붙여 씀
const ENUM_RE = /\b(scheduled_access_review|unexpected_activity_deviation|strict_as_of|simulated_replay|context_only)\b(?:\s(?=[에은는이가을를로]))?/g

/** 화면 문장에서 내부 표시를 뺌 (plainText 가 먼저 부름). 설정 열쇠 중 뜻이 있는 것은 한국어로 바꿈 */
export function hideInternal(s: string): string {
  const out = s
    .replace(APPLIED_RULE_RE, '적용 규칙 → ')
    .replace(RULE_TAG_RE, '')
    .replace(CONFIG_RE, (_m, key: string | undefined) => (key && CONFIG_KEY_LABEL[key.split('.')[0]]) || '')
    .replace(RULE_IDS_RE, '')
    .replace(ENUM_RE, (m, k: string) => ENUM_LABEL[k] ?? CONFIG_KEY_LABEL[k] ?? m)
  if (out === s) return s
  // 뺀 자리에 남은 빈 괄호·쉼표 정리 (바뀐 문장만)
  return out
    .replace(/\(\s*[,:]?\s*\)/g, '')
    .replace(/\(\s*[,:]\s*/g, '(')
    .replace(/\s*[,:]\s*\)/g, ')')
    .replace(/\s+([.,:)])/g, '$1')
    .replace(/ {2,}/g, ' ')
    .trim()
}

/** 이 문장이 거점 거리 이야기인가 (행정복지센터 안내를 붙일지) */
export function mentionsHub(s: string): boolean {
  return /거점/.test(s)
}

// ---- 근거 ID 를 이름표로 (FE-09). [EV-…] 를 "근거 1 · 지표 이름"으로
export type EvidenceRef = { evidence_id: string; metric: string }
/** 근거 ID → { 번호(1부터), 이름 } */
export function evidenceIndex(list: EvidenceRef[]): Map<string, { n: number; name: string }> {
  return new Map(list.map((e, i) => [e.evidence_id, { n: i + 1, name: plainText(e.metric) }]))
}
const EV_RE = /\[(EV-[0-9A-Za-z-]+)\]/g
/** 보고서 문장을 [EV-…] 기준으로 나눔: 글자 조각과 근거 ID 조각 */
export function splitRefs(text: string): ({ text: string } | { ref: string })[] {
  const out: ({ text: string } | { ref: string })[] = []
  let last = 0
  for (const m of text.matchAll(EV_RE)) {
    // "근거 [EV-…]" 는 버튼 글자가 이미 "근거 n"이라 앞의 "근거"를 뺀다 ("근거 근거 4" 방지)
    if (m.index! > last) out.push({ text: text.slice(last, m.index).replace(/근거\s*$/, '') })
    out.push({ ref: m[1] })
    last = m.index! + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last) })
  return out.filter((p) => !('text' in p) || p.text !== '')
}
/** 인쇄·글자용: [EV-…] → "(근거 n)", "근거 [EV-…]" → "근거 n". 모르는 ID 는 "(근거)" */
export function refsToText(text: string, idx: Map<string, { n: number; name: string }>): string {
  return text.replace(/(근거\s*)?\[(EV-[0-9A-Za-z-]+)\]/g, (_m, word: string | undefined, id: string) => {
    const r = idx.get(id)
    if (word) return r ? `근거 ${r.n}` : '근거'
    return r ? `(근거 ${r.n})` : '(근거)'
  })
}

/** 자원 원장 ID 모양: R0000, EP-C…, EH-A…, EE-A…, WN-WLF…-GN/CC, WL-WLF…, MC·CP·PP·JB·AD-CC/GN…, 가짜 STUB-R… */
export const RESOURCE_ID_SRC = String.raw`(?:R\d{4}|E[PHE]-[AC]\d+|WN-WLF\d+-(?:GN|CC)|WL-WLF\d+|(?:MC|CP|PP|JB|AD)-(?:CC|GN)\d+|STUB-R\d+)`
const RES_WITH_NAME_RE = new RegExp(String.raw`\b(${RESOURCE_ID_SRC})\b(?: \(([^()]+)\))?`, 'g')
const RES_BEFORE_NAME_RE = new RegExp(String.raw`\b${RESOURCE_ID_SRC}\b\s+(?=[가-힣])`, 'g')

/** 문장 속 자원 ID → 이름. "ID (이름)"은 이름 한 번만. 이름을 모르는 ID 뒤에 이름이 바로 오면 ID 를 뺌 */
export function resourceIdsToNames(s: string, resourceNames: Record<string, string> = {}): string {
  return s
    .replace(RES_WITH_NAME_RE, (m, id: string, paren: string | undefined) => {
      const n = resourceNames[id]
      if (!n) return m
      return paren === undefined || paren === n ? n : `${n} (${paren})`
    })
    .replace(RES_BEFORE_NAME_RE, '')
}

/** 보고서 문장 가공 (화면·인쇄 같이 씀): 자원 코드 → 이름, 입력 코드 → 한국어, 거점 이름 */
export function reportText(s: string, resourceNames: Record<string, string> = {}): string {
  return plainText(replaceInputCodes(resourceIdsToNames(s, resourceNames)))
    .replace(/ \((?:11|32)\d{6}\)/g, '') // 동 이름 옆 행정동 코드 (이름이 이미 있음)
    .replace(/후보 C-\d{8}-\d{3} · /g, '') // 후보 ID (화면 제목에 동·집단 이름이 있음)
    .replace(/(대응안|집중 확인) C-\d{8}-\d{3}:/g, '$1:')
}

/** 공용 토큰으로 기록된 이름 ("공용:<이름>") 인가. 화면은 이름을 그대로 보이고 "공용 계정" 표시만 붙인다 */
export function isSharedName(name: string | null | undefined): boolean {
  return !!name && name.startsWith('공용:')
}
/** 글자용 (인쇄): "공용:demo (공용 계정)" */
export function nameText(name: string): string {
  return isSharedName(name) ? `${name} (공용 계정 · 누가 했는지 따로 적어 두세요)` : name
}

// ---- 등급 조건 값 표시 (단위: 비율→%, km, 일, 위)
const CMP_KO: Record<string, string> = { '>=': '이상', '>': '초과', '<=': '이하', '<': '미만' }

function trimNum(x: number, digits: number): string {
  return String(Number(x.toFixed(digits)))
}

/** 등급 조건 값 하나: 0.153 (비율) → "15.3%", 18.57 (km) → "18.57km", 5 (위) → "5위" */
export function fmtCheckValue(v: unknown, unit?: string | null): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v !== 'number') return String(v)
  if (unit && unit.includes('비율')) return `${trimNum(v * 100, 1)}%`
  return `${trimNum(v, 4)}${unit ?? ''}`
}

/** 기준까지 차이: 비율이면 %p */
export function fmtCheckGap(d: number, unit?: string | null): string {
  if (unit && unit.includes('비율')) return `${trimNum(d * 100, 1)}%p`
  return `${trimNum(d, 2)}${unit ?? ''}`
}

/** "값 15.3% (기준 10% 이상)" */
export function fmtCheck(c: { value?: unknown; threshold?: unknown; unit?: string | null; comparator?: string | null }): string {
  const v = `값 ${fmtCheckValue(c.value, c.unit)}`
  if (c.threshold === null || c.threshold === undefined) return v
  const cmp = c.comparator ? CMP_KO[c.comparator] : undefined
  return `${v} (기준 ${fmtCheckValue(c.threshold, c.unit)}${cmp ? ` ${cmp}` : ''})`
}

// ---- 자료 이름 (한계 문장 앞머리 "card1: …")
export const DATA_SOURCE_LABEL: Record<string, string> = {
  card1: '카드 데이터1',
  card2: '카드 데이터2',
  tel_age: '통신 성연령 표',
  tel_time: '통신 시간대 표',
  tel_wkdy: '통신 요일 표',
  resident_hh: '주민등록 세대',
  resident_pop: '주민등록 인구',
  seoul_living: '서울 시민생활 지표',
  weather_asos: '기상 관측(ASOS)',
}

/** 한계 문장: 앞머리 자료 코드를 쉬운 이름으로 ("card1: 공개일…" → "카드 데이터1: 공개일…") */
export function limitText(s: string): string {
  return plainText(
    s
      .replace(/^([a-z][a-z0-9_]*):\s*/, (m, code: string) => (DATA_SOURCE_LABEL[code] ? `${DATA_SOURCE_LABEL[code]}: ` : m))
      .replace(/^[A-Z][A-Z0-9_]+:\s*(?=[가-힣])/, ''), // 앞머리 오류 코드 (TIME_AVAILABILITY_UNKNOWN: …)
  )
}

// ---- 답 문장 나누기: 핵심 사실(템플릿) / 설명(LLM). 서버는 사실 줄 → "개인 판정 아님 · 최종 판단은 담당자" → 설명 줄 순서로 보냄
export const ANSWER_DISCLAIMER = DISCLAIMER
export function splitAnswer(text: string | null | undefined): { facts: string[]; explanation: string[]; disclaimer: boolean } {
  const lines = (text ?? '').split('\n').map((l) => l.trim()).filter(Boolean)
  const i = lines.indexOf(ANSWER_DISCLAIMER)
  if (i < 0) return { facts: lines, explanation: [], disclaimer: false }
  return { facts: lines.slice(0, i), explanation: lines.slice(i + 1), disclaimer: true }
}
