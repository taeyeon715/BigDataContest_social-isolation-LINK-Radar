import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { api, type Candidate, type CandidateType, type OverviewResponse, type ReleasedCandidate } from '../api'
import { FollowUpBadge } from '../components/CandidateCard'
import { HolidayWatch } from '../components/HolidayWatch'
import { PageBody, PageHero } from '../components/Layout'
import { OutcomeSummaryCard } from '../components/OutcomeSummary'
import { AsyncBlock, Badge, Card, Empty, ErrorBox, LevelBadge, Loading, SelectedBadge, TimeLabelBar, TypeChip } from '../components/ui'
import { useFilters } from '../lib/filters'
import { followState, useFollowUps } from '../lib/followups'
import { TYPE_LABEL, candidatePlace, daysBetween, groupOf, plainText } from '../lib/labels'
import { useAsync } from '../lib/useAsync'

const TYPE_COLOR: Record<CandidateType, string> = { scheduled_access_review: '#008F8A', unexpected_activity_deviation: '#7A5BC7' }

function totalRange(o: OverviewResponse) {
  const r = Object.values(o.candidate_counts)
  return {
    count: r.reduce((s, x) => s + (x?.count ?? 0), 0),
    low: r.reduce((s, x) => s + (x?.low ?? 0), 0),
    high: r.reduce((s, x) => s + (x?.high ?? 0), 0),
  }
}

/** 후보 한 건을 "논현1동 · 중장년 남성 1인가구 · Level 2" 칩으로 (ID 대신) */
export function CandidateChip({ c, query, released = false }: { c: Candidate | ReleasedCandidate; query: string; released?: boolean }) {
  const text = `${candidatePlace(c)} · ${groupOf(c) ?? c.service_eligibility_group ?? c.analysis_cohort} · ${c.candidate_level ?? '등급 전'}`
  if (released)
    return (
      <span className="chip bg-canvas text-ink-600" title={c.candidate_id} data-testid="candidate-chip">
        {text}
      </span>
    )
  return (
    <Link to={`/candidates/${c.candidate_id}${query}`} className="chip bg-brand-50 text-brand-700 hover:bg-brand-100" title={c.candidate_id} data-testid="candidate-chip">
      {text}
    </Link>
  )
}

/** 오늘 먼저 볼 후보 (검토 배정 순서대로, 최대 3건) */
export function TodayRow({ items, budget, query }: { items: Candidate[]; budget: number | null; query: string }) {
  return (
    <section className="card p-5 sm:p-6" data-testid="today-row">
      <header className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-bold text-ink-900">오늘 먼저 볼 {items.length}건</h2>
        {budget !== null && <span className="text-xs text-ink-600">검토 배정 순서 · 이번에 볼 수 있는 수 {budget}건 (등급과 따로)</span>}
      </header>
      {items.length === 0 ? (
        <Empty>이 기준일에 검토 배정된 후보가 없습니다.</Empty>
      ) : (
        <ol className="grid gap-3 md:grid-cols-3">
          {items.map((c) => (
            <li key={c.candidate_id}>
              <Link
                to={`/candidates/${c.candidate_id}${query}`}
                className="flex h-full flex-col gap-2 rounded-2xl border border-line p-4 transition hover:border-brand-400 hover:shadow-card"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <LevelBadge level={c.candidate_level} withName />
                  <SelectedBadge c={c} />
                </div>
                <div className="font-bold text-ink-900">{candidatePlace(c)}</div>
                <div className="text-ink-600">{groupOf(c) ?? c.service_eligibility_group}</div>
                <div className="mt-auto flex flex-wrap items-center gap-1.5 text-xs text-ink-600">
                  <TypeChip type={c.candidate_type} />
                  {c.trigger}
                </div>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

/** 해제된 후보: released_candidates(사유 포함)를 쓰고, 옛 응답이면 ID만 */
export function releasedOf(o: OverviewResponse): { id: string; c: ReleasedCandidate | null; reason: string | null }[] {
  if (o.released_candidates && o.released_candidates.length > 0)
    return o.released_candidates.map((c) => ({ id: c.candidate_id, c, reason: c.release_reason }))
  return o.released_candidate_ids.map((id) => ({ id, c: null, reason: null }))
}

type CountRow = { kind: string; key: CandidateType; count: number; low: number; high: number }

/** 후보 수 막대 + 범위 선 (CSS만, 차트 라이브러리 없이). 막대 = 지금 수, 가는 선 = 임계값을 바꿀 때 범위 */
export function CountBars({ rows }: { rows: CountRow[] }) {
  const max = Math.max(1, ...rows.map((r) => Math.max(r.count, r.high)))
  const pct = (v: number) => `${(v / max) * 100}%`
  const summary = rows.map((r) => `${r.kind} ${r.count}건 (범위 ${r.low}~${r.high}건)`).join(', ')
  return (
    <div role="img" aria-label={`후보 종류별 수와 임계값 범위: ${summary}`} data-testid="count-bars" className="flex flex-col gap-4">
      {rows.map((r) => (
        <div key={r.key} className="grid grid-cols-[88px_minmax(0,1fr)_72px] items-center gap-3" aria-hidden>
          <div className="text-sm font-semibold text-ink-900">{r.kind}</div>
          <div className="relative h-7 rounded-md bg-canvas">
            <div className="absolute inset-y-0 left-0 rounded-md" style={{ width: pct(r.count), background: TYPE_COLOR[r.key] }} />
            {/* 범위 선: low~high */}
            <div className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-ink-900" style={{ left: pct(r.low), width: `calc(${pct(r.high)} - ${pct(r.low)})` }} />
            <div className="absolute top-1 bottom-1 w-0.5 bg-ink-900" style={{ left: pct(r.low) }} />
            <div className="absolute top-1 bottom-1 w-0.5 bg-ink-900" style={{ left: `calc(${pct(r.high)} - 2px)` }} />
          </div>
          <div className="text-xs text-ink-800">
            <b className="text-sm">{r.count}건</b>
            <div className="text-ink-600">
              범위 {r.low}~{r.high}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

export function NoticeBar({ o, query }: { o: OverviewResponse; query: string }) {
  const t = o.triggers[0]
  const sched = o.candidate_counts.scheduled_access_review?.count ?? 0
  const d = t ? daysBetween(o.as_of, t.starts_on) : 0
  const label = t ? (d > 0 && /D-\d+/.test(t.name) ? t.name.replace(/D-\d+/, `D-${d}`) : t.name) : '이번 주 트리거 없음'
  return (
    <div className="flex flex-col gap-3 rounded-pill bg-ink-900 px-5 py-3 text-white sm:flex-row sm:items-center sm:justify-between" data-testid="notice-bar">
      <div className="flex items-center gap-3">
        <span className="chip bg-ink-900/25 text-white">알림</span>
        <span className="font-semibold">
          {label} · 예정된 점검 {sched}건
        </span>
      </div>
      <Link to={`/candidates${query}&tab=scheduled`} className="btn bg-brand-500 text-white hover:bg-brand-600">
        후보 검토하기 →
      </Link>
    </div>
  )
}

export default function Overview() {
  const { city, as_of, query } = useFilters()
  const ov = useAsync((signal) => api.overview(as_of, city, { signal }), [as_of, city])
  const snap = useAsync((signal) => api.activeSnapshot({ signal }), [])
  const caps = useAsync((signal) => api.capabilities({ signal }), [])
  const cands = useAsync((signal) => api.candidates(as_of, city, undefined, undefined, { signal }), [as_of, city])
  const o = ov.data
  const total = o ? totalRange(o) : null
  const nowList = useMemo(() => cands.data?.items ?? [], [cands.data])
  const byId = useMemo(() => new Map(nowList.map((c) => [c.candidate_id, c])), [nowList])
  const fu = useFollowUps(city, as_of)
  const released = o ? releasedOf(o) : []
  const today = nowList
    .filter((c) => c.selected_for_review)
    .sort((a, b) => (a.queue_rank ?? 99) - (b.queue_rank ?? 99))
    .slice(0, 3)
  const soon = fu.items.filter((f) => followState(f, as_of) !== 'later')
  const thisWeek = soon.filter((f) => followState(f, as_of) === 'week').length
  const chart = o
    ? (Object.keys(TYPE_LABEL) as CandidateType[]).map((k) => {
        const r = o.candidate_counts[k]
        return { kind: TYPE_LABEL[k], key: k, count: r?.count ?? 0, low: r?.low ?? 0, high: r?.high ?? 0 }
      })
    : []

  return (
    <>
      <PageHero title="현황" big sources={['overview', 'candidates']}>
        <div className="grid items-end gap-6 md:grid-cols-[1fr_340px]">
          <div>
            <div className="text-sm text-white">
              {city} · 기준일 {as_of}
            </div>
            {ov.loading && !o ? (
              <div className="mt-2 h-12 w-48 animate-pulse rounded-xl bg-ink-900/30" />
            ) : ov.error && !o ? (
              <div className="mt-2 rounded-xl bg-ink-900/30 px-4 py-3 text-white" data-testid="hero-error">
                <div className="font-semibold">후보 수를 불러오지 못했습니다</div>
                <div className="text-sm">아래 오류 안내를 보고 다시 시도하세요. 0건이라는 뜻이 아닙니다.</div>
              </div>
            ) : total && o ? (
              <div className="mt-1">
                <div className="text-sm text-white">먼저 검토할 후보</div>
                <div className="flex flex-wrap items-baseline gap-3">
                  <span className="text-hero-num font-black" data-testid="hero-count">
                    전체 {total.count}건
                  </span>
                  <span className="rounded-pill bg-ink-900/25 px-3 py-1 text-sm" data-testid="hero-range">
                    임계값에 따라 {total.low}~{total.high}건
                  </span>
                </div>
                <div className="mt-1 text-sm text-white">
                  {chart.map((c) => `${c.kind} ${c.count}`).join(' · ')}
                </div>
                <div className="mt-1 text-xs text-white">기각된 이탈 경보는 세지 않음. 등급별 수는 후보 목록에서.</div>
              </div>
            ) : null}
          </div>
          <div className="rounded-card bg-ink-900/25 p-5 ring-1 ring-white/40 backdrop-blur-sm" data-testid="trigger-card">
            <div className="mb-2 flex items-center gap-2">
              <span className="chip bg-white text-brand-700">이번 주 트리거</span>
            </div>
            {!o && ov.loading && <div className="h-5 w-40 animate-pulse rounded bg-ink-900/30" />}
            {!o && ov.error && <div className="text-white">트리거를 불러오지 못했습니다.</div>}
            {o && o.triggers.length === 0 && <div className="text-white">이 날은 확인할 트리거가 없습니다.</div>}
            <ul className="flex flex-col gap-2">
              {o?.triggers.map((t) => (
                <li key={t.trigger_id}>
                  <div className="font-bold">
                    {t.rule} {t.name}
                  </div>
                  <div className="text-xs text-white">
                    시작 {t.starts_on}
                    {t.note ? ` · ${plainText(t.note)}` : ''}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </PageHero>
      <PageBody>
        {cands.data ? (
          <TodayRow items={today} budget={cands.data.selection_budget} query={query} />
        ) : (
          <Card title="오늘 먼저 볼 후보">
            <div data-testid="today-row">
              {cands.error ? <ErrorBox error={cands.error} onRetry={cands.reload} /> : <Loading label="후보 목록 불러오는 중" onRetry={cands.reload} />}
            </div>
          </Card>
        )}
        {o?.holiday_watch?.length ? <HolidayWatch lines={o.holiday_watch} /> : null}
        {o && <NoticeBar o={o} query={query} />}
        {o && <TimeLabelBar label={o.time_label} />}
        <div className="grid gap-5 lg:grid-cols-2">
          <Card
            title={fu.data ? `이번 주 후속 확인 ${thisWeek}건` : '이번 주 후속 확인'}
            right={
              <Link to={`/candidates${query}&tab=follow`} className="text-xs font-semibold text-brand-700 hover:underline">
                후속 확인 전체 보기 →
              </Link>
            }
          >
            <div data-testid="follow-card">
              {!fu.data ? (
                fu.error ? <ErrorBox error={fu.error} onRetry={fu.reload} /> : <Loading onRetry={fu.reload} />
              ) : soon.length === 0 ? (
                <Empty>기준일부터 7일 안에 후속 확인할 후보가 없습니다.</Empty>
              ) : (
                <ul className="flex flex-col divide-y divide-line">
                  {soon.map((f) => {
                    const c = byId.get(f.candidate_id)
                    return (
                      <li key={f.candidate_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                        <Link to={`/candidates/${f.candidate_id}${query}`} className="font-semibold text-ink-900 hover:text-brand-700 hover:underline">
                          {c ? `${candidatePlace(c)} · ${groupOf(c) ?? c.analysis_cohort}` : f.candidate_id}
                        </Link>
                        <span className="flex items-center gap-2">
                          {f.memo && <span className="text-xs text-ink-600">{f.memo}</span>}
                          <FollowUpBadge f={f} asOf={as_of} />
                        </span>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </Card>
          <OutcomeSummaryCard city={city} />
          <Card title="새로 생긴 / 해제된 후보" right={<span className="text-xs text-ink-600">{o?.compared_as_of ? `${o.compared_as_of} 목록과 비교` : '지난 7일과 비교'}</span>}>
            {!o && (ov.error ? <ErrorBox error={ov.error} onRetry={ov.reload} /> : <Loading onRetry={ov.reload} />)}
            {o && o.new_candidate_ids.length === 0 && released.length === 0 && <Empty>바뀐 후보가 없습니다.</Empty>}
            {o && (
              <div className="flex flex-col gap-4">
                {o.new_candidate_ids.length > 0 && (
                  <div>
                    <div className="mb-2 text-xs font-semibold text-ink-600">새로 생김 {o.new_candidate_ids.length}건</div>
                    <div className="flex flex-wrap gap-2">
                      {o.new_candidate_ids.map((id) => {
                        const c = byId.get(id)
                        return c ? (
                          <CandidateChip key={id} c={c} query={query} />
                        ) : (
                          <Link key={id} to={`/candidates/${id}${query}`} className="chip bg-brand-50 text-brand-700 hover:bg-brand-100">
                            {id}
                          </Link>
                        )
                      })}
                    </div>
                  </div>
                )}
                {released.length > 0 && (
                  <div>
                    <div className="mb-2 text-xs font-semibold text-ink-600">해제됨 {released.length}건 (등급은 지난 기준일 것)</div>
                    <ul className="flex flex-col gap-2" data-testid="released-list">
                      {released.map((r) => (
                        <li key={r.id} className="flex flex-wrap items-center gap-2">
                          {r.c ? (
                            <CandidateChip c={r.c} query={query} released />
                          ) : (
                            <span className="chip bg-canvas text-ink-600">{r.id}</span>
                          )}
                          {r.reason && (
                            <span className="text-xs text-ink-600" data-testid="release-reason">
                              {plainText(r.reason)}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </Card>
        </div>
        <Card title="후보 수 (범위와 함께)">
          <AsyncBlock state={ov}>
            {() => (
              <>
                <CountBars rows={chart} />
                <div className="mt-3 text-xs text-ink-600">막대 = 지금 기준의 수, 가는 선 = 임계값을 바꿀 때의 범위. 후보 수는 범위와 함께만 말합니다.</div>
              </>
            )}
          </AsyncBlock>
        </Card>
        {/* 출처·버전은 접어서 맨 아래. 스냅샷 하나만 늘 보임 */}
        <details className="card group p-5 sm:p-6">
          <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2">
            <span className="text-base font-bold text-ink-900">데이터 출처 · 버전</span>
            <span className="flex items-center gap-2">
              {snap.data && <Badge tone="dark">스냅샷 {snap.data.snapshot_id}</Badge>}
              <span className="text-xs font-semibold text-brand-700 group-open:hidden">펼치기</span>
              <span className="hidden text-xs font-semibold text-brand-700 group-open:inline">접기</span>
            </span>
          </summary>
          <div className="mt-4 flex flex-wrap gap-2" data-testid="source-badges">
            {snap.data && (
              <>
                <Badge>정제 규칙 {snap.data.cleaning_rule_version}</Badge>
                <Badge>등급 규칙 {snap.data.rule_version ?? '—'}</Badge>
                <Badge>자원 원장 {snap.data.ledger_version ?? '—'}</Badge>
                <Badge>
                  데이터 {snap.data.data_from} ~ {snap.data.data_to}
                </Badge>
              </>
            )}
            {caps.data && <Badge tone="blue">단위 계약 {caps.data.grain_version}</Badge>}
            {caps.data?.sources.map((s) => (
              <Badge key={s.source} tone="gray">
                {s.name}
              </Badge>
            ))}
            {snap.error && <ErrorBox error={snap.error} onRetry={snap.reload} />}
            {caps.error && <ErrorBox error={caps.error} onRetry={caps.reload} />}
          </div>
        </details>
      </PageBody>
    </>
  )
}
