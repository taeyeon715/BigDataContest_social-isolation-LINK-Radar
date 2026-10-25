import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError, CITIES, type CandidateLevel, type City, type HomeCandidate, type HomeResponse } from '../api'
import { DatePicker } from '../components/DatePicker'
import { DongMap, LEVEL_FILL, LevelLegend } from '../components/DongMap'
import { ErrorBox, LevelBadge, Loading } from '../components/ui'
import { PRESETS } from '../lib/filters'
import { LIMIT_MESSAGE } from '../lib/geminiChat'
import {
  DATA_FROM,
  DATA_TO,
  EXAMPLE_QUESTIONS,
  HOME_SECTIONS,
  STATIC_MODE,
  askGemini,
  geminiLimited,
  loadHome,
  loadIndex,
  localAnswer,
  markGeminiLimited,
  sameDayLastData,
  snapDate,
  todayKST,
  upcomingEvents,
} from '../lib/home'
import { LEVEL_NAME, plainText } from '../lib/labels'

// 첫 화면: ① 요약 → ② 먼저 볼 곳 지도 → ③ 근거 → ④ 에이전트에게 묻기.
// '오늘 기준'은 작년 같은 날의 결과다 (자료는 2025년 7~12월).
type Loaded = { as_of: string; data: Partial<Record<City, HomeResponse>>; error: string | null }

const RANK: Record<string, number> = { 'Level 3': 3, 'Level 2': 2, '판단 보류': 1, 'Level 1': 1, 'Level 0': 0 }

function useHome(as_of: string): Loaded & { loading: boolean } {
  const [state, setState] = useState<Loaded>({ as_of, data: {}, error: null })
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    const ac = new AbortController()
    setLoading(true)
    Promise.all(CITIES.map((c) => loadHome(as_of, c, { signal: ac.signal }).then((d) => [c, d] as const)))
      .then((rows) => setState({ as_of, data: Object.fromEntries(rows), error: null }))
      .catch((e) => {
        if (!ac.signal.aborted) setState({ as_of, data: {}, error: e instanceof Error ? e.message : '자료를 불러오지 못했습니다.' })
      })
      .finally(() => !ac.signal.aborted && setLoading(false))
    return () => ac.abort()
  }, [as_of])
  return { ...state, loading }
}

function l3Names(h: HomeResponse | undefined): string[] {
  return h ? h.areas.filter((a) => a.level === 'Level 3').map((a) => a.adm_nm) : []
}

/** ①~④ 섹션: 번호 배지 + 큰 굵은 제목(오른쪽에 aside) + 한 줄 설명. id 는 왼쪽 바 바로가기용 */
function Section({ no, title, desc, aside, children, testId }: { no: string; title: string; desc?: string; aside?: ReactNode; children: ReactNode; testId?: string }) {
  return (
    <section id={HOME_SECTIONS[Number(no) - 1].id} className="card scroll-mt-6 p-6 sm:p-8" data-testid={testId}>
      <header className="mb-6 flex items-center gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-brand-600 text-lg font-black text-white">{no}</span>
        <div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <h2 className="text-2xl font-extrabold tracking-tight text-ink-900">{title}</h2>
            {aside}
          </div>
          {desc && <p className="mt-0.5 text-sm text-ink-600">{desc}</p>}
        </div>
      </header>
      {children}
    </section>
  )
}

export default function Home() {
  const [params, setParams] = useSearchParams()
  const today = todayKST()
  const todayData = sameDayLastData(today)
  const explicit = params.get('as_of')
  const city = (CITIES.includes(params.get('city') as City) ? params.get('city') : '춘천') as City
  const [dates, setDates] = useState<string[] | null>(null)
  useEffect(() => {
    if (STATIC_MODE) loadIndex().then((i) => setDates(i.dates)).catch(() => setDates([]))
  }, [])
  const wanted = explicit ?? todayData ?? DATA_TO
  const as_of = STATIC_MODE && dates?.length ? snapDate(wanted, dates) : wanted
  const ready = !STATIC_MODE || dates !== null
  const set = (next: { as_of?: string | null; city?: City }) => {
    const p = new URLSearchParams(params)
    if (next.as_of === null) p.delete('as_of')
    else if (next.as_of) p.set('as_of', next.as_of)
    if (next.city) p.set('city', next.city)
    setParams(p, { replace: true })
  }
  const events = upcomingEvents(today)
  const pill = (on: boolean) => `rounded-pill px-4 py-1.5 text-sm font-semibold transition ${on ? 'bg-ink-900 text-white shadow-card' : 'bg-white/70 text-ink-900 hover:bg-white'}`

  return (
    <>
      <div className="bg-hero px-4 pb-28 pt-10 sm:px-6">
        <div className="mx-auto flex max-w-[1240px] flex-col gap-5">
          <header className="flex flex-col items-center gap-2 text-center" data-testid="home-title">
            <h1 className="text-4xl font-black tracking-tight text-ink-900 sm:text-5xl">
              LINK-Radar <span className="text-brand-700">·</span> 사회적 고립 예방 에이전트
            </h1>
            <p className="text-sm font-semibold text-ink-800 sm:text-base">2026 빅콘테스트 AI데이터 활용 분야 · 사회적 고립 예방 및 대응을 위한 AI 에이전트</p>
            <p className="flex flex-wrap justify-center gap-1.5 text-xs font-semibold text-ink-800">
              <span className="rounded-pill bg-white/70 px-3 py-1">신한카드 결제 데이터</span>
              <span className="rounded-pill bg-white/70 px-3 py-1">SK텔레콤 유동인구 데이터</span>
              <span className="rounded-pill bg-white/70 px-3 py-1">공공 통계·지역 자원 (참고)</span>
            </p>
          </header>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-2xl font-black tracking-tight text-ink-900">한눈에 보기</h2>
            <div className="rounded-pill bg-white/70 px-4 py-2 text-sm font-semibold text-ink-900" data-testid="basis-line">
              {explicit ? (
                <>기준일 {explicit}{explicit !== as_of ? ` · 계산일 ${as_of}` : ''} 결과</>
              ) : todayData ? (
                <>
                  오늘 {today} · 작년 같은 날({todayData}) 기준{as_of !== todayData ? ` · 계산일 ${as_of}` : ''}
                </>
              ) : (
                <>오늘 {today} · 작년 같은 시기 자료가 없어 마지막 날({as_of}) 결과</>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button className={pill(!explicit)} onClick={() => set({ as_of: null })}>
              오늘 기준
            </button>
            <DatePicker value={explicit && STATIC_MODE ? explicit : as_of} min={DATA_FROM} max={DATA_TO} marked={STATIC_MODE ? dates : null} today={todayData} onChange={(d) => set({ as_of: d })} />
            <span className="text-xs text-ink-600">달력에서 2025년 7~12월 중 아무 날이나 고를 수 있습니다</span>
          </div>
          <div className="rounded-card bg-brand-600 p-5 shadow-card" data-testid="presets">
            <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="rounded-pill bg-white px-3 py-0.5 text-xs font-black text-brand-700">처음이라면</span>
              <span className="text-lg font-extrabold text-surface">추천 시점 둘러보기</span>
              <span className="text-sm text-brand-100">누르면 그날 춘천 결과로 바뀝니다</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {PRESETS.map((p) => {
                const on = explicit === p.as_of
                return (
                  <button
                    key={p.label}
                    className={`rounded-2xl border-2 bg-white p-4 text-left transition ${on ? 'border-brand-400 ring-4 ring-brand-100/60' : 'border-transparent hover:border-brand-400'}`}
                    onClick={() => set({ as_of: p.as_of, city: p.city })}
                    aria-pressed={on}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-base font-extrabold text-ink-900">{p.title}</span>
                      <span className={`rounded-lg px-2 py-0.5 text-xs font-bold ${on ? 'bg-brand-600 text-white' : 'bg-brand-100 text-brand-700'}`}>{p.label}</span>
                    </div>
                    <p className="mt-1.5 text-xs leading-relaxed text-ink-600">{p.desc}</p>
                  </button>
                )
              })}
            </div>
          </div>
          <LevelGuide />
        </div>
      </div>
      <div className="mx-auto -mt-16 flex w-full max-w-[1240px] flex-col gap-6 px-4 pb-12 sm:px-6">
        {ready ? <Body as_of={as_of} city={city} setCity={(c) => set({ city: c })} events={events} goDate={(d) => set({ as_of: d })} /> : <Loading />}
      </div>
    </>
  )
}

const GUIDE: { level: CandidateLevel; todo: string }[] = [
  { level: 'Level 3', todo: '점검 시기 + 행동 신호 2개 + 공공 자료 확인. 먼저 현장 확인' },
  { level: 'Level 2', todo: '행동 신호 1개. 담당자가 근거를 보고 결정' },
  { level: 'Level 1', todo: '신호가 약하거나 다른 원인. 다음 기준일과 비교' },
  { level: 'Level 0', todo: '점검 시기가 아님. 정기 모니터링' },
  { level: '판단 보류', todo: '자료가 부족함. 더 모아 다시 판단' },
]

/** 맨 위 등급 설명: 요약의 'Level 3 n개 동'을 읽는 법 */
function LevelGuide() {
  return (
    <div className="rounded-card bg-white/75 p-5 backdrop-blur-sm" data-testid="level-guide">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-lg font-extrabold text-ink-900">등급 읽는 법</span>
        <span className="text-sm text-ink-600">위험 점수가 아니라 먼저 확인할 순서입니다</span>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {GUIDE.map((g) => (
          <li key={g.level} className="flex gap-3 rounded-xl bg-white p-3">
            <span className="w-1.5 shrink-0 rounded-full" style={{ background: LEVEL_FILL[g.level] }} />
            <span className="flex flex-col gap-0.5">
              <span className="text-base font-extrabold text-ink-900">
                {g.level}
                {g.level !== '판단 보류' && <span className="ml-1.5 text-sm font-bold text-ink-600">{LEVEL_NAME[g.level]}</span>}
              </span>
              <span className="text-xs leading-relaxed text-ink-600">{g.todo}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 연휴 주의 문장: 첫 줄은 제목, 나머지는 '근거·할 일·관측 한계' 칸 */
function WatchBox({ lines }: { lines: string[] }) {
  const [head, ...rest] = lines
  const i = head.indexOf(': ')
  return (
    <div className="mt-6 rounded-2xl border-l-4 border-brand-500 bg-brand-50 p-5" data-testid="home-watch">
      <div className="text-sm font-bold text-brand-700">{i > 0 ? head.slice(0, i) : head}</div>
      {i > 0 && <div className="mt-1 text-lg font-extrabold text-ink-900">{plainText(head.slice(i + 2))}</div>}
      <dl className="mt-3 grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[5.5rem_1fr]">
        {rest.map((l) => {
          const j = l.indexOf(': ')
          return (
            <div key={l} className="contents">
              <dt className="font-bold text-ink-800">{j > 0 ? l.slice(0, j) : ''}</dt>
              <dd className="text-ink-800">{plainText(j > 0 ? l.slice(j + 2) : l)}</dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}

function Body({ as_of, city, setCity, events, goDate }: { as_of: string; city: City; setCity: (c: City) => void; events: ReturnType<typeof upcomingEvents>; goDate: (d: string) => void }) {
  const h = useHome(as_of)
  const home = h.data[city]
  const [sel, setSel] = useState<string | null>(null)
  useEffect(() => {
    setSel(null)
  }, [as_of, city])
  const picked = useMemo(() => {
    if (!home) return [] as HomeCandidate[]
    const first = home.areas.filter((a) => a.level || a.deviation_first)
      .sort((a, b) => (RANK[b.level ?? ''] ?? -1) - (RANK[a.level ?? ''] ?? -1))[0]?.adm_cd
    const adm = sel ?? first
    if (!adm) return home.candidates.filter((c) => !c.adm_cd) // 도시 단위 후보 (카드 이탈 경보 등)
    return home.candidates.filter((c) => c.adm_cd === adm || c.check_first_adm.includes(adm))
  }, [home, sel])
  if (h.error) return <ErrorBox error={new ApiError(0, 'LOAD_FAILED', h.error)} />
  if (h.loading && !home) return <Loading />
  if (!home) return null
  const ranked = home.areas.filter((a) => a.level || a.deviation_first)
    .sort((a, b) => (RANK[b.level ?? ''] ?? -1) - (RANK[a.level ?? ''] ?? -1))
  const anyL3 = CITIES.some((c) => l3Names(h.data[c]).length > 0)
  const next = events.find((e) => e.analog)
  const placeOf = (c: HomeCandidate | undefined) => c?.adm_nm ?? (c ? `${city} 전체` : city)

  return (
    <>
      {/* ① 요약 */}
      <Section no="1" title="요약" desc="두 도시에서 먼저 볼 동과 다가오는 일정" testId="home-summary-card">
        <div className="grid gap-4 lg:grid-cols-[1fr_1fr_1.1fr]" data-testid="home-summary">
          {CITIES.map((c) => {
            const d = h.data[c]
            const names = l3Names(d)
            const on = c === city
            return (
              <button key={c} className={`rounded-2xl border-2 p-5 text-left transition ${on ? 'border-brand-500 bg-brand-50' : 'border-line hover:border-brand-400'}`} onClick={() => setCity(c)}>
                <div className="flex items-center justify-between">
                  <span className="text-lg font-extrabold text-ink-900">{c}</span>
                  {on && <span className="chip bg-brand-600 text-white">지도에 표시 중</span>}
                </div>
                <div className="mt-3 text-sm font-bold text-ink-600">Level 3 먼저 볼 동</div>
                <div className="text-5xl font-black leading-tight text-ink-900">
                  {names.length}
                  <span className="ml-1 text-xl font-extrabold">개 동</span>
                </div>
                <div className="mt-1 text-sm text-ink-600">
                  Level 2 {d?.summary.counts['Level 2'] ?? 0}건 · 판단 보류 {d?.summary.counts['판단 보류'] ?? 0}건
                </div>
                <div className="mt-3 min-h-[1.5rem] text-sm font-semibold text-ink-900">{names.length ? names.join(' · ') : '먼저 볼 동 없음'}</div>
              </button>
            )
          })}
          <div className="rounded-2xl bg-canvas p-5">
            <div className="text-lg font-extrabold text-ink-900">다가오는 일정</div>
            <ul className="mt-3 flex flex-col gap-3">
              {events.slice(0, 3).map((e) => (
                <li key={e.name} className="flex items-center justify-between gap-2">
                  <span>
                    <span className="mr-2 rounded-lg bg-white px-2 py-0.5 text-sm font-black text-brand-700">D-{e.dDay}</span>
                    <span className="font-bold text-ink-900">{e.name}</span>
                    <span className="ml-1 text-xs text-ink-600">{e.start.slice(5).replace('-', '/')}~{e.end.slice(5).replace('-', '/')}</span>
                  </span>
                  {e.analog ? (
                    <button className="shrink-0 text-xs font-bold text-brand-700 hover:underline" onClick={() => goDate(e.analog!)}>
                      작년 이맘때 →
                    </button>
                  ) : (
                    <span className="shrink-0 text-xs text-ink-400">작년 자료 없음</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </div>
        {!anyL3 && (
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-ink-900 p-5 text-white" data-testid="home-next">
            <span className="text-base font-semibold">
              이 기준일에는 두 도시 모두 먼저 볼 동이 없습니다.{next ? ` ${next.name}(D-${next.dDay})에 대비해 작년 같은 시기를 보세요.` : ''}
            </span>
            {next && (
              <button className="rounded-pill bg-white px-5 py-2 text-sm font-bold text-ink-900 hover:bg-brand-50" onClick={() => goDate(next.analog!)}>
                작년 {next.analog!.slice(5).replace('-', '/')} 결과 보기
              </button>
            )}
          </div>
        )}
        {home.summary.holiday_watch.length > 0 && <WatchBox lines={home.summary.holiday_watch} />}
      </Section>

      {/* ② 먼저 볼 곳 지도 */}
      <Section
        no="2"
        title={`먼저 볼 곳 · ${city}`}
        desc="동을 누르면 아래에 근거가 열립니다"
        aside={
          <div className="flex rounded-pill bg-canvas p-1" role="group" aria-label="도시">
            {CITIES.map((c) => (
              <button key={c} className={`rounded-pill px-5 py-1.5 text-sm font-bold ${c === city ? 'bg-brand-600 text-white' : 'text-ink-800'}`} onClick={() => setCity(c)} aria-pressed={c === city}>
                {c}
              </button>
            ))}
          </div>
        }
      >
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="h-[480px] overflow-hidden rounded-2xl border border-line" data-testid="home-map">
            <DongMap city={city} areas={home.areas} selected={sel} onSelect={setSel} />
          </div>
          <div className="flex flex-col gap-3">
            <div className="text-sm font-bold text-ink-600">확인 순서</div>
            <ol className="flex max-h-[448px] flex-col gap-2 overflow-y-auto pr-1" data-testid="home-rank">
              {ranked.length === 0 && (
                <li className="rounded-xl bg-canvas p-4 text-sm text-ink-600">
                  동을 가리키는 후보가 없습니다.{home.candidates.length > 0 ? ' 아래 근거에 도시 단위 후보를 보여 줍니다.' : ''}
                </li>
              )}
              {ranked.map((a, i) => (
                <li key={a.adm_cd}>
                  <button className={`flex w-full items-center gap-3 rounded-xl border-2 px-3 py-2.5 text-left ${sel === a.adm_cd ? 'border-brand-500 bg-brand-50' : 'border-transparent bg-canvas hover:border-line'}`} onClick={() => setSel(a.adm_cd)}>
                    <span className="w-6 text-center text-sm font-black text-ink-400">{i + 1}</span>
                    <span className="flex-1 text-base font-bold text-ink-900">{a.adm_nm}</span>
                    {a.level ? <LevelBadge level={a.level} /> : <span className="chip bg-deviation-soft text-deviation-ink">이탈 경보</span>}
                  </button>
                </li>
              ))}
            </ol>
          </div>
        </div>
        <div className="mt-4">
          <LevelLegend />
        </div>
      </Section>

      {/* ③ 근거 */}
      <Section no="3" title={`근거 · ${placeOf(picked[0])}`} desc="왜 이 등급인지, 담당자가 할 일, 연결할 수 있는 곳">
        {picked.length === 0 ? (
          <div className="rounded-xl bg-canvas p-5 text-sm text-ink-600">지도나 목록에서 동을 고르면 그 동의 근거가 보입니다.</div>
        ) : (
          <div className="flex flex-col gap-4" data-testid="home-evidence">
            {picked.map((c) => (
              <EvidenceCard key={c.candidate_id} c={c} city={city} as_of={as_of} />
            ))}
          </div>
        )}
      </Section>

      {/* ④ 에이전트에게 묻기 */}
      <AgentChat home={home} picked={picked[0] ?? null} as_of={as_of} city={city} place={placeOf(picked[0])} />
    </>
  )
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-xl bg-canvas px-4 py-3">
      <div className="text-xs font-bold text-ink-600">{label}</div>
      <div className="mt-0.5 text-base font-extrabold text-ink-900">{value}</div>
    </div>
  )
}

function EvidenceCard({ c, city, as_of }: { c: HomeCandidate; city: City; as_of: string }) {
  return (
    <article className="rounded-2xl border border-line p-5">
      <div className="flex flex-wrap items-center gap-2">
        <LevelBadge level={c.candidate_level} withName />
        <span className="text-xl font-extrabold text-ink-900">{c.adm_nm ?? `${city} 전체`}</span>
        <span className="text-base font-semibold text-ink-600">{c.group ?? c.analysis_cohort}</span>
        <span className="chip bg-canvas text-ink-600">{c.candidate_type === 'scheduled_access_review' ? '예정된 점검' : '예상 밖 이탈'}</span>
        {c.selected_for_review && <span className="chip bg-brand-50 text-brand-700">이번 검토 {c.queue_rank}순위</span>}
      </div>
      <p className="mt-3 text-base leading-relaxed text-ink-800">{plainText(c.level_reason ?? '')}</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Fact label="담당자가 할 일" value={c.response_type ?? '—'} />
        <Fact label="근거 신뢰도" value={c.evidence_confidence ?? '—'} />
        <Fact label="빠진 근거" value={c.input_status === '충족' ? '없음' : c.missing_inputs.join(', ')} />
      </div>
      {c.counter_reason && !(c.level_reason ?? '').includes(c.counter_reason) && <p className="mt-3 text-sm text-ink-600">다른 원인 확인: {plainText(c.counter_reason)}</p>}
      {c.resources.length > 0 && (
        <div className="mt-4">
          <div className="text-sm font-bold text-ink-800">먼저 연결할 수 있는 곳</div>
          <ul className="mt-2 grid gap-2 sm:grid-cols-3">
            {c.resources.map((r) => (
              <li key={r.name} className="rounded-xl border border-line px-4 py-3">
                <div className="font-bold text-ink-900">{r.name}</div>
                <div className="text-xs text-ink-600">
                  {r.resource_type}
                  {r.distance_km != null ? ` · ${r.distance_km.toFixed(1)}km` : ''}
                  {r.phone_based ? ' · 전화' : ''}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {!STATIC_MODE && (
        <Link to={`/candidates/${c.candidate_id}?as_of=${as_of}&city=${encodeURIComponent(city)}`} className="mt-4 inline-block text-sm font-bold text-brand-700 hover:underline">
          후보 상세·조사 열기 →
        </Link>
      )}
    </article>
  )
}

// ---- ④ 에이전트에게 묻기: 어떤 근거를 보고 답하는지 보이고, 답을 만드는 단계를 보여 준다
type Msg = { who: 'me' | 'bot'; lines: string[]; note?: string; sources?: string[] }
const STEPS = ['근거 카드 불러오는 중', '등급 이유·자원 확인 중', '답 작성 중']

function AgentAvatar({ small = false }: { small?: boolean }) {
  return (
    <span className={`grid shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-card ${small ? 'h-8 w-8' : 'h-12 w-12'}`} aria-hidden>
      <svg viewBox="0 0 24 24" className={small ? 'h-4 w-4' : 'h-6 w-6'} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4L12 3z" />
        <path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2z" />
      </svg>
    </span>
  )
}

function AgentChat({ home, picked, as_of, city, place }: { home: HomeResponse; picked: HomeCandidate | null; as_of: string; city: City; place: string }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState(0)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    setMsgs([])
  }, [as_of, city])
  // 새 메시지가 오면 질문창 안쪽만 아래로 (페이지는 움직이지 않음. 날짜를 바꿔 대화가 비워질 때도 그대로)
  useEffect(() => {
    if (box.current && (msgs.length || busy)) box.current.scrollTop = box.current.scrollHeight
  }, [msgs, busy])
  useEffect(() => {
    if (!busy) return
    setStep(0)
    const t = window.setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 900)
    return () => window.clearInterval(t)
  }, [busy])
  const limited = STATIC_MODE && geminiLimited()
  const engine = STATIC_MODE ? (limited ? '오늘 무료 한도 소진 · 근거 카드 답' : 'Gemini 2.5 Flash-Lite · 무료 등급') : '에이전트 질의응답 API'
  const sources = [`${as_of} ${city}`, place, ...(picked?.candidate_level ? [picked.candidate_level] : []), ...(picked?.resources.length ? [`자원 ${picked.resources.length}곳`] : [])]

  const ask = async (question: string) => {
    if (!question.trim() || busy) return
    setMsgs((m) => [...m, { who: 'me', lines: [question] }])
    setQ('')
    setBusy(true)
    let bot: Msg
    if (STATIC_MODE) {
      if (geminiLimited()) {
        bot = { who: 'bot', lines: localAnswer(question, home, picked), note: LIMIT_MESSAGE }
      } else {
        const r = await askGemini({ question, as_of: home.as_of, city, adm_cd: picked?.adm_cd ?? picked?.check_first_adm[0] ?? null })
        if (r.status === 'ok') {
          bot = { who: 'bot', lines: r.lines, sources, note: r.dropped ? `근거에 없는 숫자가 든 문장 ${r.dropped}개를 뺐습니다` : undefined }
        } else {
          if (r.status === 'limit') markGeminiLimited()
          bot = { who: 'bot', lines: localAnswer(question, home, picked), sources, note: r.status === 'limit' ? r.message : `근거 카드에서 찾은 답 (${r.message})` }
        }
      }
    } else {
      try {
        const r = await api.qa({ question, as_of, analysis_mode: 'simulated_replay', city })
        const lines = r.status === 'answered' && r.answer ? r.answer.split('\n').filter(Boolean) : [r.abstain_reason ?? '답을 보류했습니다.', ...(r.needed_data.length ? [`필요한 자료: ${r.needed_data.join(', ')}`] : [])]
        bot = { who: 'bot', lines, sources, note: '사실 검사를 통과한 문장만 보여 줍니다' }
      } catch (e) {
        bot = { who: 'bot', lines: localAnswer(question, home, picked), sources, note: `에이전트에 묻지 못해 근거 카드에서 찾은 답 (${e instanceof ApiError ? e.message : '오류'})` }
      }
    }
    setMsgs((m) => [...m, bot])
    setBusy(false)
  }

  return (
    <section id={HOME_SECTIONS[3].id} className="scroll-mt-6 overflow-hidden rounded-card bg-ink-900 text-white shadow-lift" data-testid="home-chat-card">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-white/10 p-6 sm:px-8">
        <div className="flex items-center gap-4">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-brand-600 text-lg font-black">4</span>
          <AgentAvatar />
          <div>
            <h2 className="text-2xl font-extrabold tracking-tight">LINK 에이전트에게 묻기</h2>
            <p className="mt-0.5 flex items-center gap-2 text-sm text-ink-200">
              <span className={`inline-block h-2 w-2 rounded-full ${limited ? 'bg-ink-400' : 'animate-pulse bg-okay-soft'}`} />
              {engine}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs" aria-label="참고하는 근거">
          <span className="text-ink-200">참고하는 근거</span>
          {sources.map((s) => (
            <span key={s} className="rounded-pill bg-white/10 px-2.5 py-1 font-semibold">
              {s}
            </span>
          ))}
        </div>
      </header>

      <div ref={box} className="flex max-h-[420px] min-h-[200px] flex-col gap-4 overflow-y-auto p-6 sm:px-8" data-testid="home-chat">
        {msgs.length === 0 && (
          <div className="flex gap-3">
            <AgentAvatar small />
            <div className="rounded-2xl rounded-tl-sm bg-white/10 px-4 py-3 text-sm leading-relaxed">
              <b>{place}</b>의 근거 카드를 읽고 있습니다. 왜 먼저 봐야 하는지, 어떤 자원을 연결할지 물어보세요.
              <div className="mt-1 text-xs text-ink-200">
                등급은 질문으로 바뀌지 않습니다.{STATIC_MODE ? ' 질문은 Gemini로 보내지니 이름·연락처 같은 개인정보는 적지 마세요.' : ''}
              </div>
            </div>
          </div>
        )}
        {msgs.map((m, i) =>
          m.who === 'me' ? (
            <div key={i} className="max-w-[80%] self-end rounded-2xl rounded-tr-sm bg-brand-600 px-4 py-3 text-sm font-semibold">
              {m.lines[0]}
            </div>
          ) : (
            <div key={i} className="flex max-w-[90%] gap-3">
              <AgentAvatar small />
              <div className="rounded-2xl rounded-tl-sm bg-white px-4 py-3 text-sm leading-relaxed text-ink-900">
                {m.lines.map((l, j) => (
                  <p key={j}>{l}</p>
                ))}
                {(m.sources?.length || m.note) && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-line pt-2 text-xs text-ink-600">
                    {m.sources?.map((s) => (
                      <span key={s} className="rounded-pill bg-canvas px-2 py-0.5 font-semibold">
                        {s}
                      </span>
                    ))}
                    {m.note && <span>{m.note}</span>}
                  </div>
                )}
              </div>
            </div>
          ),
        )}
        {busy && (
          <div className="flex gap-3" role="status">
            <AgentAvatar small />
            <ol className="flex flex-col gap-1 rounded-2xl rounded-tl-sm bg-white/10 px-4 py-3 text-sm">
              {STEPS.map((s, i) => (
                <li key={s} className={`flex items-center gap-2 ${i <= step ? 'text-white' : 'text-ink-400'}`}>
                  <span className={`inline-block h-1.5 w-1.5 rounded-full ${i < step ? 'bg-okay-soft' : i === step ? 'animate-ping bg-brand-400' : 'bg-ink-400'}`} />
                  {s}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>

      <div className="border-t border-white/10 p-6 sm:px-8">
        <div className="mb-3 flex flex-wrap gap-2">
          {EXAMPLE_QUESTIONS.map((x) => (
            <button key={x} className="rounded-pill border border-white/20 px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-white/10 disabled:opacity-50" onClick={() => ask(x)} disabled={busy}>
              {x}
            </button>
          ))}
        </div>
        <form
          className="flex items-center gap-2 rounded-2xl bg-white p-2"
          onSubmit={(e) => {
            e.preventDefault()
            void ask(q)
          }}
        >
          <input className="flex-1 bg-transparent px-3 py-2 text-base text-ink-900 outline-none placeholder:text-ink-400" placeholder={`예: ${place}은 왜 먼저 봐야 해?`} value={q} onChange={(e) => setQ(e.target.value)} aria-label="질문" maxLength={300} />
          <button className="grid h-10 w-10 place-items-center rounded-xl bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-40" disabled={busy || !q.trim()} aria-label="보내기">
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </button>
        </form>
      </div>
    </section>
  )
}
