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

async function request<T>(method: 'GET' | 'POST', path: string, opts: { query?: Query; body?: unknown } = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}${qs(opts.query)}`, {
      method,
      headers: requestHeaders(opts.body !== undefined),
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', 'API 서버에 연결할 수 없습니다. API가 켜져 있는지 확인하세요.')
  }
  noteSource(path, res.headers.get('X-Data-Source'))
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    const e = (data ?? {}) as Partial<ErrorResponse>
    throw new ApiError(res.status, e.error_code ?? 'HTTP_ERROR', e.message ?? '요청을 처리할 수 없습니다.')
  }
  return data as T
}

export const api = {
  health: () => request<HealthResponse>('GET', '/health'),
  activeSnapshot: () => request<SnapshotInfo>('GET', '/snapshots/active'),
  capabilities: () => request<CapabilitiesResponse>('GET', '/data/capabilities'),
  overview: (as_of: string, city: City) => request<OverviewResponse>('GET', '/overview', { query: { as_of, city } }),
  map: (as_of: string, city: City) => request<MapResponse>('GET', '/map', { query: { as_of, city } }),
  candidates: (as_of: string, city: City, type?: CandidateType, level?: string) =>
    request<CandidateListResponse>('GET', '/candidates', { query: { as_of, city, type, level } }),
  createRun: (body: RunCreateRequest) => request<RunAccepted>('POST', '/runs', { body }),
  runs: (candidate_id?: string, limit = 50) => request<RunListResponse>('GET', '/runs', { query: { candidate_id, limit } }),
  run: (run_id: string) => request<RunStatusResponse>('GET', `/runs/${encodeURIComponent(run_id)}`),
  evidence: (run_id: string) => request<EvidenceResponse>('GET', `/runs/${encodeURIComponent(run_id)}/evidence`),
  report: (run_id: string) => request<ReportResponse>('GET', `/runs/${encodeURIComponent(run_id)}/report`),
  history: (run_id: string) => request<RunHistoryResponse>('GET', `/runs/${encodeURIComponent(run_id)}/history`),
  review: (run_id: string, body: ReviewRequest) =>
    request<ReviewResponse>('POST', `/runs/${encodeURIComponent(run_id)}/review`, { body }),
  approve: (run_id: string, body: ApproveRequest) =>
    request<ApproveResponse>('POST', `/runs/${encodeURIComponent(run_id)}/approve`, { body }),
  runResources: (run_id: string) => request<RunResourcesResponse>('GET', `/runs/${encodeURIComponent(run_id)}/resources`),
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
  }) =>
    request<ResourceListResponse>('GET', '/resources', { query: q }),
  followups: (q: { city?: City; as_of?: string; week?: string; from?: string; to?: string } = {}) =>
    request<FollowUpListResponse>('GET', '/followups', { query: q }),
  recordOutcome: (run_id: string, body: OutcomeRequest) =>
    request<OutcomeResponse>('POST', `/runs/${encodeURIComponent(run_id)}/outcomes`, { body }),
  runOutcomes: (run_id: string) => request<OutcomeListResponse>('GET', `/runs/${encodeURIComponent(run_id)}/outcomes`),
  outcomeSummary: (q: { city?: City; by?: 'trigger' | 'event' } = {}) =>
    request<OutcomeSummaryResponse>('GET', '/outcomes/summary', { query: q }),
}

export type Api = typeof api
