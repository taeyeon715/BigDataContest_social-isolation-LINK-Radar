import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type RunHistoryResponse } from '../api'
import { PageBody, PageHero } from '../components/Layout'
import { Badge, Card, Empty, ErrorBox, Loading } from '../components/ui'
import { useFilters } from '../lib/filters'
import { DECISION_LABEL as DECISION, MODE_LABEL, PHASE_LABEL, REVISION_REASON_LABEL as REASON, fmtTime } from '../lib/labels'
import { useAsync } from '../lib/useAsync'


function HistoryRow({ runId }: { runId: string }) {
  const h = useAsync<RunHistoryResponse>((signal) => api.history(runId, { signal }), [runId])
  if (h.error) return <ErrorBox error={h.error} onRetry={h.reload} />
  if (!h.data) return <Loading onRetry={h.reload} />
  const d = h.data!
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <div>
        <div className="mb-1 text-xs font-semibold text-ink-600">보고서 판</div>
        {d.report_revisions.length === 0 ? <span className="text-ink-600">없음</span> : (
          <ul className="text-xs">
            {d.report_revisions.map((r) => (
              <li key={r.report_revision}>
                {r.report_revision}판 · {REASON[r.reason] ?? r.reason} · 근거 {r.evidence_revision}판 · {fmtTime(r.created_at)}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <div className="mb-1 text-xs font-semibold text-ink-600">검토</div>
        {d.reviews.length === 0 ? <span className="text-ink-600">없음</span> : (
          <ul className="flex flex-col gap-1 text-xs">
            {d.reviews.map((r) => (
              <li key={r.review_id}>
                {r.review_id} · {r.reviewer} · 판 {r.report_revision_before}→{r.report_revision_after}
                {r.follow_up_on && ` · 후속 ${r.follow_up_on}`}
                <div className="text-ink-600">{r.resource_judgements.map((j) => `${j.resource_id} ${j.judgement}`).join(', ')}</div>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <div className="mb-1 text-xs font-semibold text-ink-600">승인</div>
        {d.approvals.length === 0 ? <span className="text-ink-600">없음</span> : (
          <ul className="text-xs">
            {d.approvals.map((a) => (
              <li key={a.approval_id}>
                {a.approval_id} · {DECISION[a.decision]} · {a.report_revision}판 · {a.approver}
                <div className="break-all font-mono text-ink-600">{a.idempotency_key}</div>
              </li>
            ))}
          </ul>
        )}
        {d.finalized.length > 0 && <div className="mt-1 text-xs text-okay">확정 기록 {d.finalized.length}건</div>}
      </div>
    </div>
  )
}

export default function Runs() {
  const { query } = useFilters()
  const runs = useAsync((signal) => api.runs(undefined, 100, { signal }), [])
  const [open, setOpen] = useState<string | null>(null)
  return (
    <>
      <PageHero title="실행 기록" sources={['runs']} />
      <PageBody>
        <Card title="실행" right={<button className="btn-ghost" onClick={runs.reload}>새로 고침</button>}>
          {runs.loading && !runs.data ? (
            <Loading />
          ) : runs.error ? (
            <ErrorBox error={runs.error} onRetry={runs.reload} />
          ) : runs.data!.items.length === 0 ? (
            <Empty>아직 실행이 없습니다. 후보 상세에서 조사를 시작하거나 질의응답에서 질문해 보세요.</Empty>
          ) : (
            <div className="scroll-x overflow-x-auto rounded-2xl border border-line">
              <table className="w-full min-w-[960px] text-sm">
                <thead className="bg-canvas">
                  <tr>
                    <th className="th">실행 ID</th>
                    <th className="th">내용</th>
                    <th className="th">상태</th>
                    <th className="th">기준일 · 모드</th>
                    <th className="th">스냅샷</th>
                    <th className="th">정제 · 등급 규칙</th>
                    <th className="th">자원 원장</th>
                    <th className="th">보고서</th>
                    <th className="th">만든 시각</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.data!.items.map((r) => (
                    <Fragment key={r.run_id}>
                      <tr className="cursor-pointer border-t border-line hover:bg-canvas/60" onClick={() => setOpen(open === r.run_id ? null : r.run_id)}>
                        <td className="td whitespace-nowrap font-mono text-xs">{open === r.run_id ? '▾' : '▸'} {r.run_id}</td>
                        <td className="td min-w-[180px]">
                          {r.candidate_id ? (
                            <Link className="text-brand-600 hover:underline" to={`/candidates/${r.candidate_id}${query}`} onClick={(e) => e.stopPropagation()}>
                              {r.candidate_id}
                            </Link>
                          ) : (
                            r.question ?? `정기 감시${r.city ? ` · ${r.city}` : ''}`
                          )}
                        </td>
                        <td className="td"><Badge tone={r.status === 'approved' ? 'okay' : r.status === 'abstained' ? 'caution' : 'gray'}>{PHASE_LABEL[r.status]}</Badge></td>
                        <td className="td whitespace-nowrap text-xs">{r.as_of} · {MODE_LABEL[r.analysis_mode]}</td>
                        <td className="td whitespace-nowrap font-mono text-xs">{r.snapshot_id}</td>
                        <td className="td text-xs">{r.cleaning_rule_version} · {r.rule_version}</td>
                        <td className="td whitespace-nowrap font-mono text-xs">{r.ledger_version}</td>
                        <td className="td whitespace-nowrap text-xs">{r.report_revision ? `${r.report_revision}판${r.report_stale ? ' (다시 만드는 중)' : ''}` : '—'}</td>
                        <td className="td whitespace-nowrap text-xs">{fmtTime(r.created_at)}</td>
                      </tr>
                      {open === r.run_id && (
                        <tr className="bg-canvas/40">
                          <td className="td" colSpan={9}>
                            <div className="sticky left-3 w-[min(1100px,calc(100vw-160px))]">
                              <HistoryRow runId={r.run_id} />
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </PageBody>
    </>
  )
}
