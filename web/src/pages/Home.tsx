import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError, CITIES, type City, type HomeCandidate, type HomeResponse } from '../api'
import { DongMap, LevelLegend } from '../components/DongMap'
import { Card, ErrorBox, LevelBadge, Loading, TypeChip } from '../components/ui'
import { PRESETS } from '../lib/filters'
import {
  DATA_FROM,
  DATA_TO,
  EXAMPLE_QUESTIONS,
  STATIC_MODE,
  loadHome,
  loadIndex,
  localAnswer,
  sameDayLastData,
  snapDate,
  todayKST,
  upcomingEvents,
} from '../lib/home'
import { LEVEL_LEGEND, plainText } from '../lib/labels'

// 첫 화면: ① 요약 → ② 먼저 볼 곳 지도 → ③ 근거 → ④ 질문. '오늘 기준'은 작년 같은 날의 결과다 (자료는 2025년 7~12월).
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

  return (
    <>
      <div className="bg-hero px-4 pb-24 pt-8 text-white sm:px-6">
        <div className="mx-auto flex max-w-[1240px] flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h1 className="text-2xl font-bold">한눈에 보기</h1>
            <div className="text-sm" data-testid="basis-line">
              {explicit ? (
                <>기준일 {as_of} 결과</>
              ) : todayData ? (
                <>
                  오늘 {today} · 작년 같은 날({todayData}) 기준 결과{as_of !== todayData ? ` (가장 가까운 계산일 ${as_of})` : ''}
                </>
              ) : (
                <>오늘 {today} · 작년 같은 시기 자료가 없어 마지막 날({as_of}) 결과</>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button className={`rounded-pill px-3 py-1 text-sm font-semibold ${!explicit ? 'bg-white text-brand-700' : 'border border-white/70'}`} onClick={() => set({ as_of: null })}>
              오늘 기준
            </button>
            {PRESETS.map((p) => (
              <button key={p.label} className={`rounded-pill px-3 py-1 text-sm ${explicit === p.as_of ? 'bg-white text-brand-700' : 'border border-white/70 hover:bg-ink-900/30'}`} onClick={() => set({ as_of: p.as_of })}>
                {p.label}
              </button>
            ))}
            {STATIC_MODE ? (
              <select aria-label="기준일" className="rounded-md px-2 py-1 text-sm text-ink-900" value={as_of} onChange={(e) => set({ as_of: e.target.value })}>
                {(dates ?? []).map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            ) : (
              <input type="date" aria-label="기준일" className="rounded-md px-2 py-1 text-sm text-ink-900" min={DATA_FROM} max={DATA_TO} value={as_of} onChange={(e) => e.target.value && set({ as_of: e.target.value })} />
            )}
          </div>
        </div>
      </div>
      <div className="mx-auto -mt-16 flex w-full max-w-[1240px] flex-col gap-5 px-4 pb-10 sm:px-6">
        {ready ? <Body as_of={as_of} city={city} setCity={(c) => set({ city: c })} events={events} goDate={(d) => set({ as_of: d })} /> : <Loading />}
      </div>
    </>
  )
}

function Body({ as_of, city, setCity, events, goDate }: { as_of: string; city: City; setCity: (c: City) => void; events: ReturnType<typeof upcomingEvents>; goDate: (d: string) => void }) {
  const h = useHome(as_of)
  const home = h.data[city]
  const [sel, setSel] = useState<string | null>(null)
  useEffect(() => setSel(null), [as_of, city])
  const picked = useMemo(() => {
    if (!home) return [] as HomeCandidate[]
    const first = home.areas.filter((a) => a.level || a.deviation_first)
      .sort((a, b) => (RANK[b.level ?? ''] ?? -1) - (RANK[a.level ?? ''] ?? -1))[0]?.adm_cd
    const adm = sel ?? first
    if (!adm) return home.candidates.filter((c) => !c.adm_cd)  // 도시 단위 후보 (카드 이탈 경보 등)
    return home.candidates.filter((c) => c.adm_cd === adm || c.check_first_adm.includes(adm))
  }, [home, sel])
  if (h.error) return <ErrorBox error={new ApiError(0, 'LOAD_FAILED', h.error)} />
  if (h.loading && !home) return <Loading />
  if (!home) return null
  const ranked = home.areas.filter((a) => a.level || a.deviation_first)
    .sort((a, b) => (RANK[b.level ?? ''] ?? -1) - (RANK[a.level ?? ''] ?? -1))
  const anyL3 = CITIES.some((c) => l3Names(h.data[c]).length > 0)
  const next = events.find((e) => e.analog)

  return (
    <>
      {/* ① 요약 */}
      <Card title="① 요약">
        <div className="grid gap-4 md:grid-cols-3" data-testid="home-summary">
          {CITIES.map((c) => {
            const d = h.data[c]
            const names = l3Names(d)
            return (
              <button key={c} className={`rounded-2xl border p-4 text-left transition ${c === city ? 'border-brand-500 bg-brand-50' : 'border-line hover:border-brand-400'}`} onClick={() => setCity(c)}>
                <div className="text-sm font-bold text-ink-900">{c}</div>
                <div className="mt-1 text-2xl font-black text-ink-900">Level 3 {names.length}개 동</div>
                <div className="text-xs text-ink-600">Level 2 {d?.summary.counts['Level 2'] ?? 0} · 판단 보류 {d?.summary.counts['판단 보류'] ?? 0}</div>
                <div className="mt-2 text-sm text-ink-800">{names.length ? names.join(' · ') : '먼저 볼 동(Level 3) 없음'}</div>
              </button>
            )
          })}
          <div className="rounded-2xl border border-line p-4">
            <div className="text-sm font-bold text-ink-900">다가오는 일정</div>
            <ul className="mt-2 flex flex-col gap-2 text-sm">
              {events.slice(0, 3).map((e) => (
                <li key={e.name}>
                  <span className="font-semibold">{e.name}</span> D-{e.dDay} ({e.start.slice(5).replace('-', '/')}~{e.end.slice(5).replace('-', '/')})
                  {e.analog ? (
                    <button className="ml-2 text-xs text-brand-700 underline" onClick={() => goDate(e.analog!)}>
                      작년 이맘때 보기
                    </button>
                  ) : (
                    <span className="ml-2 text-xs text-ink-600">작년 같은 시기 자료 없음</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </div>
        {!anyL3 && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-brand-50 p-4 text-sm text-ink-900" data-testid="home-next">
            <span>이 기준일에는 두 도시 모두 먼저 볼 동(Level 3)이 없습니다.{next ? ` 다가오는 ${next.name}(D-${next.dDay})에 대비해 작년 같은 시기 결과를 보세요.` : ''}</span>
            {next && (
              <button className="btn-primary px-4" onClick={() => goDate(next.analog!)}>
                작년 {next.analog!.slice(5).replace('-', '/')} 결과 보기
              </button>
            )}
          </div>
        )}
        {home.summary.holiday_watch.length > 0 && (
          <ul className="mt-4 flex flex-col gap-1 rounded-xl bg-canvas p-4 text-sm text-ink-800" data-testid="home-watch">
            {home.summary.holiday_watch.map((l) => (
              <li key={l}>{plainText(l)}</li>
            ))}
          </ul>
        )}
        {home.summary.triggers.length > 0 && <div className="mt-3 text-xs text-ink-600">이 기준일의 점검 시점: {home.summary.triggers.map((t) => t.name).join(' · ')}</div>}
      </Card>

      {/* ② 먼저 볼 곳 지도 */}
      <Card title={`② 먼저 볼 곳 · ${city}`}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex rounded-pill bg-canvas p-1" role="group" aria-label="도시">
            {CITIES.map((c) => (
              <button key={c} className={`rounded-pill px-4 py-1 text-sm font-semibold ${c === city ? 'bg-brand-600 text-white' : 'text-ink-800'}`} onClick={() => setCity(c)} aria-pressed={c === city}>
                {c}
              </button>
            ))}
          </div>
          <LevelLegend />
        </div>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="h-[460px] overflow-hidden rounded-2xl border border-line" data-testid="home-map">
            <DongMap city={city} areas={home.areas} selected={sel} onSelect={setSel} />
          </div>
          <ol className="flex max-h-[460px] flex-col gap-2 overflow-y-auto" data-testid="home-rank">
            {ranked.length === 0 && (
              <li className="text-sm text-ink-600">
                이 기준일에는 동을 가리키는 후보가 없습니다.{home.candidates.length > 0 ? ' 아래 근거에 도시 단위 후보를 보여 줍니다.' : ''}
              </li>
            )}
            {ranked.map((a, i) => (
              <li key={a.adm_cd}>
                <button className={`flex w-full items-center justify-between rounded-xl border px-3 py-2 text-left text-sm ${sel === a.adm_cd ? 'border-brand-500 bg-brand-50' : 'border-line hover:bg-canvas'}`} onClick={() => setSel(a.adm_cd)}>
                  <span>
                    {i + 1}. {a.adm_nm}
                  </span>
                  {a.level ? <LevelBadge level={a.level} /> : <span className="chip bg-deviation-soft text-deviation-ink">이탈 경보 먼저 볼 동</span>}
                </button>
              </li>
            ))}
          </ol>
        </div>
        <div className="mt-3 text-xs text-ink-600">{LEVEL_LEGEND} 개인이나 동네가 위험하다는 뜻이 아닙니다.</div>
      </Card>

      {/* ③ 근거 */}
      <Card title="③ 근거">
        {picked.length === 0 ? (
          <div className="text-sm text-ink-600">지도나 목록에서 동을 고르면 그 동의 근거가 보입니다.</div>
        ) : (
          <div className="flex flex-col gap-4" data-testid="home-evidence">
            {picked.map((c) => (
              <EvidenceCard key={c.candidate_id} c={c} city={city} as_of={as_of} />
            ))}
          </div>
        )}
      </Card>

      {/* ④ 질문 */}
      <Chat home={home} picked={picked[0] ?? null} as_of={as_of} city={city} />
    </>
  )
}

function EvidenceCard({ c, city, as_of }: { c: HomeCandidate; city: City; as_of: string }) {
  return (
    <div className="rounded-2xl border border-line p-4">
      <div className="flex flex-wrap items-center gap-2">
        <LevelBadge level={c.candidate_level} withName />
        <TypeChip type={c.candidate_type} />
        <span className="font-bold text-ink-900">{c.adm_nm ?? `${city} 전체`}</span>
        <span className="text-sm text-ink-600">{c.group ?? c.analysis_cohort}</span>
        {c.selected_for_review && <span className="chip bg-brand-50 text-brand-700">이번 검토 배정 {c.queue_rank}순위</span>}
      </div>
      <p className="mt-2 text-sm text-ink-800">{plainText(c.level_reason ?? '')}</p>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-600">
        <span>할 일: {c.response_type ?? '—'}</span>
        <span>근거 신뢰도: {c.evidence_confidence ?? '—'}</span>
        {c.input_status !== '충족' && <span>빠진 근거: {c.missing_inputs.join(', ')}</span>}
        {c.counter_reason && !(c.level_reason ?? '').includes(c.counter_reason) && <span>다른 원인 확인: {plainText(c.counter_reason)}</span>}
      </div>
      {c.resources.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-semibold text-ink-600">먼저 연결할 수 있는 곳</div>
          <ul className="mt-1 flex flex-wrap gap-2 text-sm">
            {c.resources.map((r) => (
              <li key={r.name} className="rounded-xl bg-canvas px-3 py-1">
                {r.name} <span className="text-xs text-ink-600">{r.resource_type}{r.distance_km != null ? ` · ${r.distance_km.toFixed(1)}km` : ''}{r.phone_based ? ' · 전화' : ''}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {!STATIC_MODE && (
        <Link to={`/candidates/${c.candidate_id}?as_of=${as_of}&city=${encodeURIComponent(city)}`} className="mt-3 inline-block text-sm font-semibold text-brand-700 underline">
          후보 상세·조사 열기 →
        </Link>
      )}
    </div>
  )
}

type Msg = { who: 'me' | 'bot'; lines: string[]; note?: string }

function Chat({ home, picked, as_of, city }: { home: HomeResponse; picked: HomeCandidate | null; as_of: string; city: City }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [msgs, setMsgs] = useState<Msg[]>([])
  useEffect(() => setMsgs([]), [as_of, city])
  const ask = async (question: string) => {
    if (!question.trim() || busy) return
    setMsgs((m) => [...m, { who: 'me', lines: [question] }])
    setQ('')
    if (STATIC_MODE) {
      setMsgs((m) => [...m, { who: 'bot', lines: localAnswer(question, home, picked), note: '이 화면의 근거 카드에서 찾은 답 (LLM 아님)' }])
      return
    }
    setBusy(true)
    try {
      const r = await api.qa({ question, as_of, analysis_mode: 'simulated_replay', city })
      const lines = r.status === 'answered' && r.answer ? r.answer.split('\n').filter(Boolean) : [r.abstain_reason ?? '답을 보류했습니다.', ...(r.needed_data.length ? [`필요한 자료: ${r.needed_data.join(', ')}`] : [])]
      setMsgs((m) => [...m, { who: 'bot', lines, note: '에이전트 답 (사실 검사 통과 문장만)' }])
    } catch (e) {
      setMsgs((m) => [...m, { who: 'bot', lines: localAnswer(question, home, picked), note: `에이전트에 묻지 못해 근거 카드에서 찾은 답 (${e instanceof ApiError ? e.message : '오류'})` }])
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card title="④ 질문하기">
      <div className="flex max-h-[360px] flex-col gap-3 overflow-y-auto" data-testid="home-chat">
        {msgs.length === 0 && <div className="text-sm text-ink-600">고른 동과 이 기준일 근거에 대해 물어보세요. 등급은 질문으로 바뀌지 않습니다.</div>}
        {msgs.map((m, i) => (
          <div key={i} className={`max-w-[85%] rounded-2xl px-4 py-2 text-sm ${m.who === 'me' ? 'self-end bg-brand-600 text-white' : 'self-start bg-canvas text-ink-900'}`}>
            {m.lines.map((l, j) => (
              <div key={j}>{l}</div>
            ))}
            {m.note && <div className="mt-1 text-xs text-ink-600">{m.note}</div>}
          </div>
        ))}
        {busy && <div className="self-start text-sm text-ink-600">근거를 확인하는 중…</div>}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {EXAMPLE_QUESTIONS.map((x) => (
          <button key={x} className="rounded-pill border border-line px-3 py-1 text-xs text-ink-800 hover:bg-canvas" onClick={() => ask(x)} disabled={busy}>
            {x}
          </button>
        ))}
      </div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void ask(q)
        }}
      >
        <input className="field flex-1" placeholder="예: 효자2동은 왜 먼저 봐야 해?" value={q} onChange={(e) => setQ(e.target.value)} aria-label="질문" />
        <button className="btn-primary px-5" disabled={busy || !q.trim()}>
          보내기
        </button>
      </form>
    </Card>
  )
}
