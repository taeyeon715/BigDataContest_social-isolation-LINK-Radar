import { useEffect, useState } from 'react'
import { api, ApiError, type QAResponse } from '../api'
import { ClaimsBox, EvidenceTable, withRefs } from '../components/EvidenceTable'
import { PageBody, PageHero, useFooterRun } from '../components/Layout'
import { Badge, Card, ErrorBox, HoldNotice, TimeLabelBar , RoleNote, useRole } from '../components/ui'
import { useFilters } from '../lib/filters'
import { ERROR_TEXT, evidenceIndex, reportText } from '../lib/labels'

export const EXAMPLES = ['강남 60대의 밤 활동은 어때?', '강남 1인세대 비율 상위 동은?', '이번 추석 전에 춘천에서 어디부터 확인해야 해?']

export function QAResult({ res }: { res: QAResponse }) {
  const [hl, setHl] = useState<string | null>(null)
  const [showAnswer, setShowAnswer] = useState(false)
  // 근거 표가 먼저 그려진 뒤에 설명 문장을 붙인다 (문장은 이미 사실 검사를 통과한 것만 옴)
  useEffect(() => {
    setShowAnswer(false)
    const t = window.setTimeout(() => setShowAnswer(true), 250)
    return () => window.clearTimeout(t)
  }, [res.run_id])

  if (res.status === 'abstained') {
    const [code, ...rest] = (res.abstain_reason ?? '').split(':')
    return (
      <Card title="답할 수 없는 질문입니다">
        {/* 쉬운 문장을 먼저, 코드는 작은 회색 글자로 */}
        <p className="mb-3 text-base font-semibold text-ink-900" data-testid="abstain-text">
          {ERROR_TEXT[code] ?? (rest.join(':').trim() || '근거가 부족해 답하지 않았습니다.')}
        </p>
        <HoldNotice title="왜 답하지 않나" reasons={rest.length ? [rest.join(':').trim()] : []} needed={res.needed_data} />
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-600">
          <span>
            사유 코드 <span className="font-mono">{code}</span>
          </span>
          <span>· 실행 {res.run_id}</span>
        </div>
        <div className="mt-3 text-xs text-ink-600">근거 없이 추측해서 답하지 않습니다. 위 자료가 있으면 답할 수 있습니다.</div>
      </Card>
    )
  }
  const card = res.evidence_card!
  const idx = evidenceIndex(card.evidence)
  const refName = (id: string) => { const e = idx.get(id); return e ? `근거 ${e.n}` : undefined }
  return (
    <>
      <TimeLabelBar label={card.time_label} />
      <Card title={`근거 · ${card.title}`} right={<span className="text-xs text-ink-600">실행 {res.run_id}</span>}>
        <EvidenceTable card={card} highlight={hl} />
        <div className="mt-4">
          <ClaimsBox card={card} />
        </div>
      </Card>
      <Card title="설명">
        <div className="mb-3 flex flex-wrap gap-1">
          {res.guard.map((g, i) => (
            <span key={i} className={`chip ${g.verdict === 'PASS' ? 'bg-okay-soft text-okay' : 'bg-caution-soft text-caution'}`}>
              {g.check} 검사: {g.verdict}
            </span>
          ))}
        </div>
        {showAnswer ? (
          <p className="text-base leading-relaxed text-ink-900" data-testid="qa-answer">
            {withRefs(reportText(res.answer ?? ''), setHl, refName)}
          </p>
        ) : (
          <div className="h-6 w-2/3 animate-pulse rounded bg-line" />
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          {card.limitations.map((l) => (
            <Badge key={l}>{l}</Badge>
          ))}
        </div>
      </Card>
    </>
  )
}

export default function QA() {
  const { city, as_of } = useFilters()
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState('')
  const [res, setRes] = useState<QAResponse | null>(null)
  const [err, setErr] = useState<ApiError | null>(null)
  useFooterRun(res?.run_id)
  const { can } = useRole()
  const noRole = !can('검토') // 질의응답은 LLM 비용을 써서 검토 권한 이상 (API 명세 §1)

  const ask = async (question: string) => {
    if (!question.trim()) return
    setQ(question)
    setBusy(true)
    setErr(null)
    setRes(null)
    setStage('근거 모으는 중')
    const t = window.setTimeout(() => setStage('사실 검사 중'), 400)
    try {
      const city_ = question.includes('강남') ? '강남' : question.includes('춘천') ? '춘천' : city
      setRes(await api.qa({ question, as_of, analysis_mode: 'simulated_replay', city: city_ }))
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, 'UNKNOWN', '답을 받지 못했습니다.'))
    } finally {
      window.clearTimeout(t)
      setBusy(false)
    }
  }

  return (
    <>
      <PageHero title="질의응답" sources={['qa']} />
      <PageBody>
        <Card>
          <form
            className="flex flex-col gap-3 sm:flex-row"
            onSubmit={(e) => {
              e.preventDefault()
              ask(q)
            }}
          >
            <input className="field flex-1 py-3 text-base" placeholder="예: 강남 1인세대 비율 상위 동은?" value={q} onChange={(e) => setQ(e.target.value)} aria-label="질문" />
            <button className="btn-primary px-6" disabled={busy || !q.trim() || noRole}>
              {busy ? '확인 중…' : '묻기'}
            </button>
          </form>
          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.map((x) => (
              <button key={x} className="rounded-pill border border-line px-3 py-1 text-xs text-ink-800 hover:bg-canvas" onClick={() => ask(x)} disabled={busy || noRole}>
                {x}
              </button>
            ))}
          </div>
          {noRole && (
            <div className="mt-3">
              <RoleNote need="검토" />
            </div>
          )}
          <div className="mt-3 text-xs text-ink-600">
            기준일 {as_of} · 질문에 도시가 없으면 {city}. 숫자·지역·방향이 든 문장은 사실 검사가 끝난 뒤에만 보입니다.
          </div>
        </Card>
        {busy && (
          <Card>
            <div className="flex items-center gap-3 text-ink-600" role="status">
              <span className="h-3 w-3 animate-ping rounded-full bg-brand-500" />
              {stage}… 검사가 끝나기 전에는 답 문장을 보여 주지 않습니다.
            </div>
          </Card>
        )}
        {err && <ErrorBox error={err} onRetry={() => ask(q)} />}
        {res && <QAResult res={res} />}
      </PageBody>
    </>
  )
}
