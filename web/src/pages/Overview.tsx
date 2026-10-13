import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, type Candidate, type CandidateType, type OverviewResponse, type ReleasedCandidate } from '../api'
import { FollowUpBadge } from '../components/CandidateCard'
import { PageBody, PageHero } from '../components/Layout'
import { OutcomeSummaryCard } from '../components/OutcomeSummary'
import { Badge, Card, Empty, ErrorBox, LevelBadge, Loading, SelectedBadge, TimeLabelBar, TypeChip } from '../components/ui'
import { useFilters } from '../lib/filters'
import { followState, useFollowUps } from '../lib/followups'
import { TYPE_LABEL, candidatePlace, daysBetween, groupOf } from '../lib/labels'
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

export function NoticeBar({ o, query }: { o: OverviewResponse; query: string }) {
  const t = o.triggers[0]
  const sched = o.candidate_counts.scheduled_access_review?.count ?? 0
  const d = t ? daysBetween(o.as_of, t.starts_on) : 0
  const label = t ? (d > 0 && /D-\d+/.test(t.name) ? t.name.replace(/D-\d+/, `D-${d}`) : t.name) : '이번 주 트리거 없음'
  return (
    <div className="flex flex-col gap-3 rounded-pill bg-ink-900 px-5 py-3 text-white sm:flex-row sm:items-center sm:justify-between" data-testid="notice-bar">
      <div className="flex items-center gap-3">
        <span className="chip bg-white/15 text-white">알림</span>
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
  const ov = useAsync(() => api.overview(as_of, city), [as_of, city])
  const snap = useAsync(() => api.activeSnapshot(), [])
  const caps = useAsync(() => api.capabilities(), [])
  const cands = useAsync(() => api.candidates(as_of, city), [as_of, city])
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
        return { kind: TYPE_LABEL[k], key: k, count: r?.count ?? 0, range: `${r?.low ?? 0}~${r?.high ?? 0}` }
      })
    : []

  return (
    <>
      <PageHero title="현황" big sources={['overview', 'candidates']}>
        <div className="grid items-end gap-6 md:grid-cols-[1fr_340px]">
          <div>
            <div className="text-sm text-white/80">
              {city} · 기준일 {as_of}
            </div>
            {ov.loading && !o ? (
              <div className="mt-2 h-12 w-48 animate-pulse rounded-xl bg-white/20" />
            ) : total && o ? (
              <div className="mt-1">
                <div className="text-sm text-white/90">먼저 검토할 후보</div>
                <div className="flex flex-wrap items-baseline gap-3">
                  <span className="text-hero-num font-black" data-testid="hero-count">
                    전체 {total.count}건
                  </span>
                  <span className="rounded-pill bg-white/15 px-3 py-1 text-sm" data-testid="hero-range">
                    임계값에 따라 {total.low}~{total.high}건
                  </span>
                </div>
                <div className="mt-1 text-sm text-white/90">
                  {chart.map((c) => `${c.kind} ${c.count}`).join(' · ')}
                </div>
                <div className="mt-1 text-xs text-white/80">기각된 이탈 경보는 세지 않음. 등급별 수는 후보 목록에서.</div>
              </div>
            ) : null}
          </div>
          <div className="rounded-card bg-white/12 p-5 ring-1 ring-white/25 backdrop-blur-sm" data-testid="trigger-card">
            <div className="mb-2 flex items-center gap-2">
              <span className="chip bg-white text-brand-700">이번 주 트리거</span>
            </div>
            {o && o.triggers.length === 0 && <div className="text-white/90">이 날은 확인할 트리거가 없습니다.</div>}
            <ul className="flex flex-col gap-2">
              {o?.triggers.map((t) => (
                <li key={t.trigger_id}>
                  <div className="font-bold">
                    {t.rule} {t.name}
                  </div>
                  <div className="text-xs text-white/80">
                    시작 {t.starts_on}
                    {t.note ? ` · ${t.note}` : ''}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </PageHero>
      <PageBody>
        {ov.error && <ErrorBox error={ov.error} onRetry={ov.reload} />}
        {cands.loading && !cands.data ? (
          <Card>
            <Loading />
          </Card>
        ) : cands.data ? (
          <TodayRow items={today} budget={cands.data.selection_budget} query={query} />
        ) : null}
        {o && <NoticeBar o={o} query={query} />}
        {o && <TimeLabelBar label={o.time_label} />}
        <div className="grid gap-5 lg:grid-cols-2">
          <Card
            title={`이번 주 후속 확인 ${thisWeek}건`}
            right={
              <Link to={`/candidates${query}&tab=follow`} className="text-xs font-semibold text-brand-700 hover:underline">
                후속 확인 전체 보기 →
              </Link>
            }
          >
            <div data-testid="follow-card">
              {fu.loading && !fu.data ? (
                <Loading />
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
                              {r.reason}
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
          {ov.loading && !o ? (
            <Loading />
          ) : o ? (
            <>
              <div className="h-52" aria-label="후보 종류별 수와 임계값 범위">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chart} layout="vertical" margin={{ left: 8, right: 48, top: 4, bottom: 4 }} barCategoryGap={28}>
                    <CartesianGrid horizontal={false} stroke="#E3E7EF" />
                    <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12, fill: '#4A5163' }} axisLine={false} tickLine={false} />
                    <YAxis type="category" dataKey="kind" width={90} tick={{ fontSize: 12, fill: '#14171F' }} axisLine={false} tickLine={false} />
                    <Tooltip formatter={(v, _n, p) => [`${v}건 (범위 ${(p.payload as { range: string }).range}건)`, '후보 수']} cursor={{ fill: '#F2F4F8' }} />
                    <Bar
                      dataKey="count"
                      radius={[0, 4, 4, 0]}
                      isAnimationActive={false}
                      shape={(props: unknown) => {
                        const p = props as { x: number; y: number; width: number; height: number; payload: { key: CandidateType } }
                        return <rect x={p.x} y={p.y} width={Math.max(p.width, 0)} height={p.height} rx={4} fill={TYPE_COLOR[p.payload.key]} />
                      }}
                    >
                      <LabelList dataKey="range" position="right" formatter={(v: unknown) => `${v}`} style={{ fontSize: 12, fill: '#4A5163' }} />
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="text-xs text-ink-600">막대 = 지금 기준의 수, 오른쪽 숫자 = 임계값을 바꿀 때의 범위. 후보 수는 범위와 함께만 말합니다.</div>
            </>
          ) : null}
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
            {snap.error && <ErrorBox error={snap.error} />}
          </div>
        </details>
      </PageBody>
    </>
  )
}
