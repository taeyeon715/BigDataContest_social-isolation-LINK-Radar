import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { DISCLAIMER, api, dataSources, onDataSource, type Source, type SnapshotInfo } from '../api'
import { CITIES } from '../api'
import { PRESETS, useFilters } from '../lib/filters'
import { STATIC_MODE, loadIndex } from '../lib/home'

// ---- 하단 영역에 보일 실행 ID (페이지가 정함)
const RunCtx = createContext<{ runId: string | null; setRunId: (id: string | null) => void }>({ runId: null, setRunId: () => {} })
export function useFooterRun(runId: string | null | undefined) {
  const { setRunId } = useContext(RunCtx)
  useEffect(() => {
    setRunId(runId ?? null)
    return () => setRunId(null)
  }, [runId, setRunId])
}

const NAV: { to: string; label: string; icon: ReactNode }[] = [
  { to: '/', label: '한눈에', icon: <path d="M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10" /> },
  { to: '/overview', label: '현황', icon: <path d="M4 19V9m6 10V5m6 14v-7m4 7H2" /> },
  { to: '/map', label: '지도', icon: <path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2-6-2zm0 0v14m6-12v14" /> },
  { to: '/candidates', label: '후보', icon: <path d="M4 6h16M4 12h16M4 18h10" /> },
  { to: '/qa', label: '질의응답', icon: <path d="M4 5h16v10H8l-4 4V5z" /> },
  { to: '/runs', label: '실행 기록', icon: <path d="M12 7v5l3 2M12 3a9 9 0 100 18 9 9 0 000-18z" /> },
]

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {children}
    </svg>
  )
}

function Sidebar({ query }: { query: string }) {
  const loc = useLocation()
  return (
    <nav
      aria-label="페이지"
      className="sticky top-0 z-20 flex shrink-0 print:hidden items-center gap-1 overflow-x-auto border-b border-line bg-surface px-3 py-2 md:h-screen md:w-[84px] md:flex-col md:border-b-0 md:border-r md:px-0 md:py-6"
    >
      <div className="mr-3 text-sm font-black tracking-tight text-brand-600 md:mb-6 md:mr-0">LINK</div>
      {NAV.filter((n) => !STATIC_MODE || n.to === '/').map((n) => {
        const active = n.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(n.to)
        return (
          <NavLink
            key={n.to}
            to={`${n.to}${query}`}
            className={`flex min-w-[64px] flex-col items-center gap-1 rounded-xl px-2 py-2 text-xs font-semibold ${
              active ? 'bg-brand-50 text-brand-600' : 'text-ink-600 hover:bg-canvas'
            }`}
          >
            <Icon>{n.icon}</Icon>
            {n.label}
          </NavLink>
        )
      })}
    </nav>
  )
}

const KIND_LABEL: Record<string, string> = {
  overview: '현황',
  map: '지도',
  candidates: '후보 목록',
  resources: '자원',
  runs: '조사 실행',
  qa: '질의응답',
}

/** 이 화면이 부른 경로들의 X-Data-Source. 아직 응답이 없으면 빠짐 */
export function useDataSources(kinds: string[]): { kind: string; source: Source }[] {
  const [, bump] = useState(0)
  useEffect(() => onDataSource(() => bump((n) => n + 1)), [])
  const m = dataSources()
  return kinds.flatMap((k) => {
    const v = m.get(k)
    return v && v !== 'config' ? [{ kind: k, source: v }] : []
  })
}

/** 화면마다 작은 출처 띠: 실제 데이터 / 가상 데이터(LINK-Sim) / 가짜 데이터 (경로마다 다르면 나눠서) */
export function SourceBadge({ kinds }: { kinds: string[] }) {
  const list = useDataSources(kinds)
  if (list.length === 0) return null
  const same = list.every((x) => x.source === list[0].source)
  const tone = (s: Source) => (s === 'real' ? 'bg-okay-soft text-okay' : s === 'synthetic' ? 'bg-brand-50 text-brand-700' : 'bg-caution text-white')
  const word = (s: Source) => (s === 'real' ? '실제 데이터' : s === 'synthetic' ? '가상 데이터(LINK-Sim)' : '가짜 데이터')
  return (
    <div className="flex flex-wrap gap-1 print:hidden" data-testid="source-badge">
      {same ? (
        <span className={`chip text-xs font-semibold ${tone(list[0].source)}`} title="응답 헤더 X-Data-Source">
          {word(list[0].source)}
          {list[0].source === 'fake' && ' · 화면 확인용, 분석 결과 아님'}
          {list[0].source === 'synthetic' && ' · 합성 자료, 실제 지역 결과 아님'}
        </span>
      ) : (
        list.map((x) => (
          <span key={x.kind} className={`chip text-xs font-semibold ${tone(x.source)}`} title="응답 헤더 X-Data-Source">
            {KIND_LABEL[x.kind] ?? x.kind} {word(x.source)}
          </span>
        ))
      )}
    </div>
  )
}

function FilterBar() {
  const { city, as_of, set } = useFilters()
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex rounded-pill bg-ink-900/25 p-1" role="group" aria-label="도시">
        {CITIES.map((c) => (
          <button
            key={c}
            className={`rounded-pill px-4 py-1.5 text-sm font-semibold ${c === city ? 'bg-white text-brand-700' : 'text-white hover:bg-ink-900/40'}`}
            onClick={() => set({ city: c })}
            aria-pressed={c === city}
          >
            {c}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 rounded-pill bg-ink-900/25 px-3 py-1.5 text-sm text-white">
        기준일
        <input
          type="date"
          aria-label="기준일"
          className="min-w-[9.5rem] rounded-md bg-white px-2 py-0.5 text-ink-900"
          min="2025-07-01"
          max="2025-12-31"
          value={as_of}
          onChange={(e) => e.target.value && set({ as_of: e.target.value })}
        />
      </label>
      <div className="flex flex-wrap gap-1">
        {PRESETS.map((p) => (
          <button key={p.label} className="rounded-pill border border-white/70 px-3 py-1 text-xs text-white hover:bg-ink-900/40" onClick={() => set({ city: p.city, as_of: p.as_of })}>
            {p.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/** 페이지 위 파란 영역 (현황은 크게, 나머지는 얇게) */
export function PageHero({ title, children, big = false, sources = [] }: { title: string; children?: ReactNode; big?: boolean; sources?: string[] }) {
  return (
    <div className={`bg-hero px-4 text-white print:hidden sm:px-6 ${big ? 'pb-24 pt-8' : 'pb-16 pt-6'}`}>
      <div className="mx-auto flex max-w-[1240px] flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold">{title}</h1>
            <SourceBadge kinds={sources} />
          </div>
          <FilterBar />
        </div>
        {children}
      </div>
    </div>
  )
}

export function PageBody({ children }: { children: ReactNode }) {
  return <div className="mx-auto -mt-12 flex w-full max-w-[1240px] flex-col gap-5 px-4 pb-10 sm:px-6 print:mt-0 print:max-w-none print:px-0 print:pb-0">{children}</div>
}

export function Footer({ runId, snapshot }: { runId: string | null; snapshot: SnapshotInfo | null }) {
  return (
    <footer className="mt-auto bg-ink-900 print:hidden px-4 py-8 text-sm text-ink-200 sm:px-6" data-testid="footer">
      <div className="mx-auto flex max-w-[1240px] flex-col gap-3">
        <div className="text-base font-bold text-white">LINK-Radar</div>
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
          {runId && <span>실행 ID: {runId}</span>}
          <span>스냅샷: {snapshot?.snapshot_id ?? '—'}</span>
          <span>
            데이터 기간: {snapshot ? `${snapshot.data_from} ~ ${snapshot.data_to}` : '—'}
          </span>
        </div>
        <div className="font-semibold text-white" data-testid="disclaimer">
          {DISCLAIMER}
        </div>
        <div className="text-xs text-ink-200">집계 데이터로 먼저 확인할 동·집단을 제안합니다. 개인의 고립을 판정하지 않습니다.</div>
      </div>
    </footer>
  )
}

export default function Layout() {
  const { query } = useFilters()
  const [runId, setRunId] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState<SnapshotInfo | null>(null)
  useEffect(() => {
    if (STATIC_MODE) {
      loadIndex()
        .then((i) => setSnapshot({ snapshot_id: `${i.snapshot_id} (정적 배포, ${i.generated_at.slice(0, 10)} 계산)`, data_from: '2025-07-01', data_to: '2025-12-31' } as SnapshotInfo))
        .catch(() => setSnapshot(null))
      return
    }
    api.activeSnapshot().then(setSnapshot).catch(() => setSnapshot(null))
  }, [])
  return (
    <RunCtx.Provider value={{ runId, setRunId }}>
      <div className="flex min-h-screen flex-col md:flex-row md:bg-[linear-gradient(to_right,#FFFDF8_84px,transparent_84px)] print:block print:bg-none">
        <Sidebar query={query} />
        <div className="flex min-w-0 flex-1 flex-col">
          <main className="flex flex-1 flex-col">
            <Outlet />
          </main>
          <Footer runId={runId} snapshot={snapshot} />
        </div>
      </div>
    </RunCtx.Provider>
  )
}
