import { useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type Candidate, type CandidateLevel } from '../api'
import { CandidateCard, Carousel, FollowUpBadge } from '../components/CandidateCard'
import { PageBody, PageHero } from '../components/Layout'
import { TabList } from '../components/Tabs'
import { Term } from '../components/Term'
import { CounterChip, Empty, ErrorBox, InputStatusBadge, LevelBadge, Loading, TypeChip } from '../components/ui'
import { useFilters } from '../lib/filters'
import { followState, useFollowUps, type FollowUp } from '../lib/followups'
import { candidatePlace, groupOf, tabOf, type CandidateTab } from '../lib/labels'
import { useAsync } from '../lib/useAsync'

type Tab = CandidateTab | 'follow'
const TABS: { key: Tab; label: string; hint: string }[] = [
  { key: 'scheduled', label: '예정된 점검', hint: '연휴·폭염·동절기처럼 공백이 예상되는 때의 점검. 활동 위축이 확인된 후보가 아닙니다.' },
  { key: 'deviation', label: '예상 밖 이탈', hint: '평소보다 낮은 결제가 다른 원인 확인(반증)을 통과했거나 확인이 필요한 도시 × 집단 후보. 동 목록은 공유 맥락입니다.' },
  { key: 'rejected', label: '기각·보류', hint: '다른 원인으로 설명돼 기각된 경보(Level 1로 남김)와 근거가 부족해 판단을 보류한 후보.' },
  { key: 'follow', label: '후속 확인', hint: '검토할 때 후속 확인일을 적은 후보. 기준일이 지난 것은 "기한 지남"으로 표시합니다.' },
]
const LEVELS: CandidateLevel[] = ['Level 3', 'Level 2', 'Level 1', 'Level 0', '판단 보류']
const LEVEL_ORDER: Record<string, number> = { 'Level 3': 0, 'Level 2': 1, 'Level 1': 2, 'Level 0': 3, '판단 보류': 4 }

type SortKey = 'queue' | 'level' | 'place' | 'follow'

function sortItems(items: Candidate[], key: SortKey, fu: Map<string, FollowUp>): Candidate[] {
  const by: Record<SortKey, (c: Candidate) => string | number> = {
    queue: (c) => c.queue_rank ?? 999,
    level: (c) => LEVEL_ORDER[c.candidate_level ?? ''] ?? 9,
    place: (c) => candidatePlace(c),
    follow: (c) => fu.get(c.candidate_id)?.follow_up_on ?? '9999',
  }
  const f = by[key]
  return [...items].sort((a, b) => {
    const x = f(a)
    const y = f(b)
    return typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))
  })
}

/** 표 보기: 한눈에 훑고 정렬. 행의 지역 이름이 상세로 가는 링크 */
export function CandidateTable({ items, query, fu, asOf }: { items: Candidate[]; query: string; fu: Map<string, FollowUp>; asOf: string }) {
  const [sort, setSort] = useState<SortKey>('queue')
  const rows = useMemo(() => sortItems(items, sort, fu), [items, sort, fu])
  const th = (children: ReactNode, k?: SortKey) => (
    <th className="th" aria-sort={k && sort === k ? 'ascending' : undefined}>
      {k ? (
        <button className="inline-flex items-center gap-1 font-semibold hover:text-ink-900" onClick={() => setSort(k)}>
          {children}
          <span aria-hidden className={sort === k ? 'text-brand-600' : 'text-ink-200'}>
            ▲
          </span>
        </button>
      ) : (
        children
      )}
    </th>
  )
  return (
    <div className="scroll-x overflow-x-auto rounded-2xl border border-line">
      <table className="w-full min-w-[760px] text-sm" data-testid="candidate-table">
        <thead className="bg-canvas">
          <tr>
            {th('지역', 'place')}
            {th('집단')}
            {th('등급', 'level')}
            {th('배정', 'queue')}
            {th('종류 · 트리거')}
            {th('상태')}
            {th('후속 확인', 'follow')}
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.candidate_id} className="border-t border-line hover:bg-canvas/60">
              <td className="td">
                <Link to={`/candidates/${c.candidate_id}${query}`} className="font-bold text-ink-900 hover:text-brand-700 hover:underline">
                  {candidatePlace(c)}
                </Link>
              </td>
              <td className="td">{groupOf(c) ?? c.service_eligibility_group ?? '—'}</td>
              <td className="td">
                <LevelBadge level={c.candidate_level} withName />
              </td>
              <td className="td whitespace-nowrap">
                {c.selected_for_review ? (
                  <span className="font-semibold text-brand-700">
                    {c.queue_rank} / {c.selection_budget}건
                  </span>
                ) : c.queue_rank ? (
                  <span className="text-ink-600">대기 {c.queue_rank}</span>
                ) : (
                  '—'
                )}
              </td>
              <td className="td">
                <div className="flex flex-wrap items-center gap-1">
                  <TypeChip type={c.candidate_type} />
                  {c.trigger && <span className="text-xs text-ink-600">{c.trigger}</span>}
                </div>
              </td>
              <td className="td">
                <div className="flex flex-wrap gap-1">
                  {c.candidate_type === 'unexpected_activity_deviation' && <CounterChip c={c} />}
                  <InputStatusBadge status={c.input_status} />
                </div>
              </td>
              <td className="td">
                <FollowUpBadge f={fu.get(c.candidate_id)} asOf={asOf} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Candidates() {
  const { city, as_of, query } = useFilters()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab) || 'scheduled'
  const level = params.get('level') ?? ''
  const onlySelected = params.get('selected') === '1'
  const view = params.get('view') === 'cards' ? 'cards' : 'table'
  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params)
    if (v) p.set(k, v)
    else p.delete(k)
    setParams(p, { replace: true })
  }
  const res = useAsync((signal) => api.candidates(as_of, city, undefined, undefined, { signal }), [as_of, city])
  const all = useMemo(() => res.data?.items ?? [], [res.data])
  const fu = useFollowUps(city, as_of)
  const inTab = (c: Candidate, t: Tab) => (t === 'follow' ? fu.byId.has(c.candidate_id) : tabOf(c) === t)
  const counts = Object.fromEntries(TABS.map((t) => [t.key, all.filter((c) => inTab(c, t.key)).length])) as Record<Tab, number>
  const items = all
    .filter((c) => inTab(c, tab))
    .filter((c) => !level || c.candidate_level === level)
    .filter((c) => !onlySelected || c.selected_for_review)
  const overdue = all.filter((c) => {
    const f = fu.byId.get(c.candidate_id)
    return f && followState(f, as_of) === 'overdue'
  }).length

  return (
    <>
      <PageHero title="후보 목록" sources={['candidates']} />
      <PageBody>
        <section className="card p-5 sm:p-8">
          <div className="flex flex-wrap items-end justify-between gap-4 border-b border-line">
            <TabList
              label="후보 종류"
              idPrefix="cand-tab"
              value={tab}
              onChange={(k) => set('tab', k)}
              tabs={TABS.map((t) => ({
                key: t.key,
                label: (
                  <>
                    {t.label} <span className="ml-1 text-xs">{counts[t.key] ?? 0}</span>
                    {t.key === 'follow' && overdue > 0 && <span className="chip ml-1 bg-caution text-white">지남 {overdue}</span>}
                  </>
                ),
              }))}
            />
            <div className="flex flex-wrap items-center gap-3 pb-3">
              <label className="flex items-center gap-2 text-xs text-ink-600">
                등급
                <select className="field py-1" value={level} onChange={(e) => set('level', e.target.value)} aria-label="등급 필터">
                  <option value="">전체</option>
                  {LEVELS.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs text-ink-600">
                <input type="checkbox" checked={onlySelected} onChange={(e) => set('selected', e.target.checked ? '1' : '')} />
                검토 배정된 것만
              </label>
              <div className="flex rounded-pill border border-line p-0.5" role="group" aria-label="보기 방식">
                {(['table', 'cards'] as const).map((v) => (
                  <button
                    key={v}
                    aria-pressed={view === v}
                    className={`rounded-pill px-3 py-1 text-xs font-semibold ${view === v ? 'bg-ink-900 text-white' : 'text-ink-600 hover:bg-canvas'}`}
                    onClick={() => set('view', v === 'table' ? '' : 'cards')}
                  >
                    {v === 'table' ? '표' : '카드'}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div id="cand-tab-panel" role="tabpanel" aria-labelledby={`cand-tab-${tab}`}>
            <div className="mt-6 grid gap-2 lg:grid-cols-[1fr_auto] lg:items-end">
              <div>
                <h2 className="text-xl font-bold leading-snug [text-wrap:balance]">
                  {city} · <span className="whitespace-nowrap">{as_of}</span> 기준 <span className="text-brand-600">{TABS.find((t) => t.key === tab)?.label}</span>
                </h2>
                <p className="mt-1 text-ink-600">{TABS.find((t) => t.key === tab)?.hint}</p>
              </div>
              {res.data && (
                <div className="rounded-xl bg-canvas px-3 py-2 text-xs text-ink-600">
                  <Term>검토 배정</Term>: 이번에 볼 수 있는 수 {res.data.selection_budget}건. 등급과는 따로 정합니다.
                </div>
              )}
            </div>
            <div className="mt-6">
              {res.error && !res.data ? (
                <ErrorBox error={res.error} onRetry={res.reload} />
              ) : !res.data ? (
                <Loading onRetry={res.reload} />
              ) : tab === 'follow' && fu.error && !fu.data ? (
                <ErrorBox error={fu.error} onRetry={fu.reload} />
              ) : tab === 'follow' && !fu.data ? (
                <Loading label="후속 확인일 불러오는 중" onRetry={fu.reload} />
              ) : items.length === 0 ? (
                <Empty>{tab === 'follow' ? '후속 확인일을 적은 후보가 없습니다. 후보 상세의 검토 저장에서 적을 수 있습니다.' : '이 기준일에는 해당 후보가 없습니다.'}</Empty>
              ) : view === 'table' ? (
                <CandidateTable items={items} query={query} fu={fu.byId} asOf={as_of} />
              ) : (
                <Carousel label="후보 카드">
                  {items.map((c) => (
                    <CandidateCard key={c.candidate_id} c={c} query={query} followUp={fu.byId.get(c.candidate_id)} asOf={as_of} />
                  ))}
                </Carousel>
              )}
            </div>
          </div>
        </section>
      </PageBody>
    </>
  )
}
