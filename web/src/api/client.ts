// 화면의 모든 API 호출은 이 파일을 거친다 (docs/API_명세.md).
import type {
  ApproveRequest,
  ApproveResponse,
  CandidateListResponse,
  CandidateType,
  CapabilitiesResponse,
  City,
  EvidenceResponse,
  FollowupRequest,
  FollowupResponse,
  HealthResponse,
  MapResponse,
  OverviewResponse,
  QARequest,
  QAResponse,
  ReportResponse,
  ResourceListResponse,
  ReviewRequest,
  ReviewResponse,
  RunAccepted,
  RunCreateRequest,
  RunHistoryResponse,
  RunListResponse,
  RunResourcesResponse,
  RunStatusResponse,
  SnapshotInfo,
  ErrorResponse,
  FollowUpListResponse,
  OutcomeListResponse,
  OutcomeRequest,
  OutcomeResponse,
  OutcomeSummaryResponse,
} from './types'

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

// 데모용 토큰 (API의 LINKRADAR_API_TOKEN 과 같은 값). 빌드 결과에 그대로 들어가므로 시연용으로만 쓴다.
// 비우면 헤더를 보내지 않는다 (API 개발 모드).
export const API_TOKEN: string = (import.meta.env.VITE_API_TOKEN as string | undefined) ?? ''

// 검토·승인 요청에 적어 보내는 이름. 인증을 켜면 서버가 토큰의 이름으로 바꿔 기록하므로 화면은 늘 서버가 돌려준 이름을 보인다.
export const API_USER: string = (import.meta.env.VITE_API_USER as string | undefined) ?? ''
export const REVIEWER_NAME: string = API_USER || '담당자(시연)'
export const APPROVER_NAME: string = API_USER || '담당자(시연)'

/** 요청 헤더: 본문이 있으면 Content-Type, 토큰이 있으면 Authorization: Bearer. */
export function requestHeaders(hasBody: boolean, token: string = API_TOKEN): Record<string, string> {
  const h: Record<string, string> = {}
  if (hasBody) h['Content-Type'] = 'application/json'
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

/** API 오류: 오류 코드와 화면에 그대로 보여도 되는 문장만. */
export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

// 응답 헤더 X-Data-Source 를 경로 종류별로 기억한다. 같은 서버라도 현황·지도는 가짜, 실행은 실제일 수 있어
// 화면마다 자기가 부른 경로의 출처를 띠로 보인다 (Layout 의 SourceBadge).
export type Source = 'fake' | 'config' | 'real'
/** 경로 첫 마디: overview / map / candidates / resources / runs / qa / snapshots ... (/runs/{id}/resources 는 runs) */
export function sourceKind(path: string): string {
  return path.replace(/^\//, '').split(/[/?]/)[0] || 'root'
}
const sources = new Map<string, Source>()
const listeners = new Set<() => void>()
export function dataSources(): ReadonlyMap<string, Source> {
  return sources
}
export function onDataSource(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
export function noteSource(path: string, h: string | null) {
  if (h !== 'fake' && h !== 'real' && h !== 'config') return
  const k = sourceKind(path)
  if (sources.get(k) !== h) {
    sources.set(k, h)
    listeners.forEach((fn) => fn())
  }
}

type Query = Record<string, string | number | undefined | null>

function qs(q?: Query): string {
  if (!q) return ''
  const p = new URLSearchParams()
  Object.entries(q).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  })
  const s = p.toString()
  return s ? `?${s}` : ''
}

/** 요청 하나의 옵션: 취소 신호, 시간 제한(ms) */
export type ReqOpts = { signal?: AbortSignal; timeoutMs?: number }

/** 기본 시간 제한. 실제 그래프 첫 계산이 십여 초 걸려 넉넉히 둔다 (FE-04) */
export const REQUEST_TIMEOUT_MS = 60_000

/** 화면이 일부러 끊은 요청 (페이지를 떠남·조건이 바뀜). 화면에 오류로 보이지 않는다 */
export function isAborted(e: unknown): boolean {
  return e instanceof ApiError && e.code === 'ABORTED'
}

/** 잠깐 끊긴 것일 수 있는 오류 (연결 실패·시간 초과·서버 5xx). 폴링은 이때 간격을 늘려 다시 시도한다 */
export function isTransient(e: unknown): boolean {
  return e instanceof ApiError && (e.code === 'NETWORK_ERROR' || e.code === 'TIMEOUT' || e.status >= 500)
}

async function request<T>(
  method: 'GET' | 'POST',
  path: string,
  opts: { query?: Query; body?: unknown } & ReqOpts = {},
): Promise<T> {
  const ac = new AbortController()
  let timedOut = false
  const outer = opts.signal
  const onOuter = () => ac.abort()
  if (outer) {
    if (outer.aborted) ac.abort()
    else outer.addEventListener('abort', onOuter, { once: true })
  }
  const timeoutMs = opts.timeoutMs ?? REQUEST_TIMEOUT_MS
  const timer = timeoutMs > 0 ? setTimeout(() => {
    timedOut = true
    ac.abort()
  }, timeoutMs) : undefined
  const failed = (): ApiError =>
    timedOut
      ? new ApiError(0, 'TIMEOUT', `API가 ${Math.round(timeoutMs / 1000)}초 안에 답하지 않았습니다. 잠시 뒤 다시 시도하세요.`)
      : ac.signal.aborted
        ? new ApiError(0, 'ABORTED', '요청을 취소했습니다.')
        : new ApiError(0, 'NETWORK_ERROR', 'API 서버에 연결할 수 없습니다. API가 켜져 있는지 확인하세요.')
  try {
    let res: Response
    let text: string
    try {
      res = await fetch(`${API_BASE}${path}${qs(opts.query)}`, {
        method,
        headers: requestHeaders(opts.body !== undefined),
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: ac.signal,
      })
      noteSource(path, res.headers.get('X-Data-Source'))
      text = await res.text()
    } catch {
      throw failed()
    }
    // 프록시 502 HTML 처럼 JSON 이 아닌 응답도 ApiError 로 (FE-23)
    let data: unknown = null
    try {
      data = text ? JSON.parse(text) : null
    } catch {
      data = null
    }
    if (!res.ok) {
      const e = (data ?? {}) as Partial<ErrorResponse>
      const fallback = res.status >= 500 ? `API 서버에 문제가 생겼습니다 (HTTP ${res.status}). 잠시 뒤 다시 시도하세요.` : '요청을 처리할 수 없습니다.'
      throw new ApiError(res.status, e.error_code ?? 'HTTP_ERROR', e.message ?? fallback)
    }
    if (text && data === null) throw new ApiError(res.status, 'BAD_RESPONSE', 'API 응답을 읽을 수 없습니다.')
    return data as T
  } finally {
    if (timer !== undefined) clearTimeout(timer)
    outer?.removeEventListener('abort', onOuter)
  }
}

export const api = {
  health: (o?: ReqOpts) => request<HealthResponse>('GET', '/health', { ...o }),
  activeSnapshot: (o?: ReqOpts) => request<SnapshotInfo>('GET', '/snapshots/active', { ...o }),
  capabilities: (o?: ReqOpts) => request<CapabilitiesResponse>('GET', '/data/capabilities', { ...o }),
  overview: (as_of: string, city: City, o?: ReqOpts) => request<OverviewResponse>('GET', '/overview', { query: { as_of, city }, ...o }),
  map: (as_of: string, city: City, o?: ReqOpts) => request<MapResponse>('GET', '/map', { query: { as_of, city }, ...o }),
  candidates: (as_of: string, city: City, type?: CandidateType, level?: string, o?: ReqOpts) =>
    request<CandidateListResponse>('GET', '/candidates', { query: { as_of, city, type, level }, ...o }),
  createRun: (body: RunCreateRequest) => request<RunAccepted>('POST', '/runs', { body }),
  runs: (candidate_id?: string, limit = 50, o?: ReqOpts) => request<RunListResponse>('GET', '/runs', { query: { candidate_id, limit }, ...o }),
  run: (run_id: string, o?: ReqOpts) => request<RunStatusResponse>('GET', `/runs/${encodeURIComponent(run_id)}`, { ...o }),
  evidence: (run_id: string, o?: ReqOpts) => request<EvidenceResponse>('GET', `/runs/${encodeURIComponent(run_id)}/evidence`, { ...o }),
  report: (run_id: string, o?: ReqOpts) => request<ReportResponse>('GET', `/runs/${encodeURIComponent(run_id)}/report`, { ...o }),
  history: (run_id: string, o?: ReqOpts) => request<RunHistoryResponse>('GET', `/runs/${encodeURIComponent(run_id)}/history`, { ...o }),
  review: (run_id: string, body: ReviewRequest) =>
    request<ReviewResponse>('POST', `/runs/${encodeURIComponent(run_id)}/review`, { body }),
  approve: (run_id: string, body: ApproveRequest) =>
    request<ApproveResponse>('POST', `/runs/${encodeURIComponent(run_id)}/approve`, { body }),
  runResources: (run_id: string, o?: ReqOpts) => request<RunResourcesResponse>('GET', `/runs/${encodeURIComponent(run_id)}/resources`, { ...o }),
  followup: (run_id: string, body: FollowupRequest) =>
    request<FollowupResponse>('POST', `/runs/${encodeURIComponent(run_id)}/followup`, { body }),
  qa: (body: QARequest) => request<QAResponse>('POST', '/qa', { body }),
  resources: (q: {
    adm_cd?: string | null
    need_type?: string
    group?: string | null
    city?: City
    limit?: number
    cause_type?: string | null
  }, o?: ReqOpts) =>
    request<ResourceListResponse>('GET', '/resources', { query: q, ...o }),
  followups: (q: { city?: City; as_of?: string; week?: string; from?: string; to?: string } = {}, o?: ReqOpts) =>
    request<FollowUpListResponse>('GET', '/followups', { query: q, ...o }),
  recordOutcome: (run_id: string, body: OutcomeRequest) =>
    request<OutcomeResponse>('POST', `/runs/${encodeURIComponent(run_id)}/outcomes`, { body }),
  runOutcomes: (run_id: string, o?: ReqOpts) => request<OutcomeListResponse>('GET', `/runs/${encodeURIComponent(run_id)}/outcomes`, { ...o }),
  outcomeSummary: (q: { city?: City; by?: 'trigger' | 'event' } = {}, o?: ReqOpts) =>
    request<OutcomeSummaryResponse>('GET', '/outcomes/summary', { query: q, ...o }),
}

export type Api = typeof api
