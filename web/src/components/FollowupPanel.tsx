import { useState } from 'react'
import { api, ApiError, MAX_QUESTION, type Candidate, type FollowupIntent, type FollowupResponse } from '../api'
import { ERROR_TEXT, admName, candidatePlace, errorText, groupOf, reportText, splitAnswer, splitReason } from '../lib/labels'
import { withRefs } from './EvidenceTable'
import { RoleNote, useRole } from './ui'

export const FOLLOWUP_NOTE = '새 조사를 하지 않고 이 보고서의 근거만으로 답합니다.'
const EXAMPLES = ['이 후보는 왜 이 등급이야?', '어떤 근거가 가장 중요해?', '연결할 자원은?', '자료 한계는?']

/** 질문 종류 (서버 intent). 등급 이유 / 자원·대응 / 비교 / 자료 한계 / 기타 */
export type { FollowupIntent }
export const INTENT_LABEL: Record<FollowupIntent, string> = {
  why_level: '등급 이유',
  resources: '자원·대응',
  compare: '비교',
  data_limits: '자료 한계',
  other: '기타',
}
type FollowupRes = FollowupResponse

type Item = { q: string; res: FollowupRes }

/** 질문 종류 칩 */
export function IntentChip({ intent }: { intent?: FollowupIntent | null }) {
  if (!intent) return null
  return (
    <span className="chip bg-canvas text-ink-600" data-testid="followup-intent" data-intent={intent}>
      질문 종류 · {INTENT_LABEL[intent] ?? intent}
    </span>
  )
}

/** 이어 묻기 답 하나. 답은 근거 ID 버튼과 함께, 보류는 쉬운 문장 + 작은 회색 코드 */
export function FollowupAnswer({ item, onCite, refName }: { item: Item; onCite: (id: string) => void; refName?: (id: string) => string | undefined }) {
  const { q, res } = item
  if (res.status === 'abstained') {
    const { code, text } = splitReason(res.abstain_reason)
    const plain = (code && ERROR_TEXT[code]) || text || '근거가 부족해 답하지 않았습니다.'
    return (
      <li className="rounded-2xl border border-line p-3" data-testid="followup-item" data-status="abstained">
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-600">
          <span>질문 · {q}</span>
          <IntentChip intent={res.intent} />
        </div>
        <p className="mt-1 text-ink-900" data-testid="followup-abstain">
          {plain}
        </p>
        {text && text !== plain && <p className="mt-0.5 text-sm text-ink-800">{text}</p>}
        {res.needed_data.length > 0 && (
          <div className="mt-1 text-sm text-ink-800">
            필요한 자료: {res.needed_data.join(' · ')}
          </div>
        )}
        {code === 'NO_DATA' && (
          <div className="mt-1 rounded-xl bg-canvas px-3 py-2 text-sm text-ink-800" data-testid="followup-new-run">
            이 보고서에는 비교할 근거가 없습니다. 기준일을 바꾼 새 조사를 시작해 두 보고서를 비교하세요.
          </div>
        )}
        {code && <div className="mt-1 font-mono text-xs text-ink-600">{code}</div>}
      </li>
    )
  }
  const { facts, explanation, disclaimer } = splitAnswer(res.answer)
  const show = (l: string) => withRefs(reportText(l), onCite, refName)
  return (
    <li className="rounded-2xl border border-line p-3" data-testid="followup-item" data-status="answered">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-600">
        <span>질문 · {q}</span>
        <IntentChip intent={res.intent} />
      </div>
      <div className="mt-1 flex flex-col gap-2 text-ink-900" data-testid="followup-answer">
        {facts.length > 0 && (
          <ul className="list-disc pl-5" data-testid="followup-facts">
            {facts.map((l, i) => (
              <li key={i}>{show(l)}</li>
            ))}
          </ul>
        )}
        {explanation.length > 0 && (
          <div className="rounded-xl bg-canvas px-3 py-2" data-testid="followup-explanation">
            <div className="mb-0.5 text-xs font-semibold text-ink-600">설명</div>
            {explanation.map((l, i) => (
              <p key={i}>{show(l)}</p>
            ))}
          </div>
        )}
        {disclaimer && <div className="text-xs text-ink-600">개인 판정 아님 · 최종 판단은 담당자</div>}
      </div>
      {res.cited_evidence_ids.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1 text-xs" data-testid="followup-cited">
          <span className="text-ink-600">인용한 근거</span>
          {res.cited_evidence_ids.map((id) => (
            <button key={id} title={id} className={`rounded bg-brand-50 px-1.5 py-0.5 text-brand-700 hover:bg-brand-100 ${refName?.(id) ? '' : 'font-mono'}`} onClick={() => onCite(id)}>
              {refName?.(id) ?? id}
            </button>
          ))}
        </div>
      )}
      <div className="mt-1 text-xs text-ink-600">
        {res.source === 'llm' ? '설명 문장 포함 (사실 검사 통과)' : '템플릿 문장만'}
        {res.report_revision != null && ` · 보고서 ${res.report_revision}판 근거`}
      </div>
    </li>
  )
}

/** 직전 대상 이름: 후보 ID 는 "춘천 남면 · 고령층", 동 코드만 있으면 동 이름 */
export function targetLabel(target: FollowupResponse['target'], candidates: Candidate[] = []): string | null {
  if (!target) return null
  const known = (target.candidate_ids ?? [])
    .map((id) => candidates.find((x) => x.candidate_id === id))
    .filter((c): c is Candidate => !!c)
    .map((c) => [candidatePlace(c), groupOf(c) ?? c.service_eligibility_group].filter(Boolean).join(' · '))
  if (known.length > 0) return known.join(', ')
  const dongs = (target.adm_cds ?? []).map(admName).filter(Boolean)
  return dongs.length > 0 ? dongs.join(', ') : null
}

/** 이어 묻기: 이 실행의 근거 카드 안에서만 답함 */
export function FollowupPanel({
  runId,
  disabled: off,
  onCite,
  refName,
  candidates = [],
}: {
  runId: string
  disabled?: boolean
  onCite: (id: string) => void
  refName?: (id: string) => string | undefined
  /** 직전 대상 이름을 만들 이 실행의 후보 */
  candidates?: Candidate[]
}) {
  const { can } = useRole()
  const noRole = !can('검토') // 이어 묻기는 LLM 비용을 써서 검토 권한 이상
  const disabled = off || noRole
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [items, setItems] = useState<Item[]>([])
  const [error, setError] = useState<ApiError | null>(null)

  const ask = async (question: string) => {
    const text = question.trim()
    if (!text) return
    setBusy(true)
    setError(null)
    try {
      const res = await api.followup(runId, { question: text })
      if (res.run_id !== runId) return
      setItems((l) => [{ q: text, res }, ...l])
      setQ('')
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, 'ERROR', '이어 묻기에 실패했습니다.'))
    } finally {
      setBusy(false)
    }
  }
  const err = error ? errorText(error.code, error.message) : null
  // 서버가 기억하는 직전 대상 = 대상을 읽은 가장 최근 답의 target ("그 동네·거기"를 이것으로 풂)
  const lastTarget = items.find((it) => (it.res.target?.candidate_ids?.length ?? 0) > 0 || (it.res.target?.adm_cds?.length ?? 0) > 0)?.res.target ?? null
  const remembered = targetLabel(lastTarget, candidates)

  return (
    <div className="flex flex-col gap-3" data-testid="followup-panel">
      <p className="text-sm text-ink-600">{FOLLOWUP_NOTE} 특정 개인에 대한 질문에는 답하지 않습니다.</p>
      <form
        className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault()
          ask(q)
        }}
      >
        <input
          className="field flex-1"
          aria-label="이어 묻기 질문"
          placeholder="예: 이 후보는 왜 이 등급이야?"
          maxLength={MAX_QUESTION}
          value={q}
          disabled={disabled || busy}
          onChange={(e) => setQ(e.target.value)}
        />
        <button type="submit" className="btn-primary" disabled={disabled || busy || !q.trim()}>
          {busy ? '묻는 중…' : '묻기'}
        </button>
      </form>
      {noRole && <RoleNote need="검토" />}
      {remembered && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="chip bg-brand-50 text-brand-700" data-testid="followup-target" title="'그 동네', '거기'라고 물으면 이 대상으로 답합니다">
            직전 대상: {remembered}
          </span>
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {EXAMPLES.map((x) => (
          <button key={x} className="chip bg-canvas text-ink-600 hover:bg-brand-50" disabled={disabled || busy} onClick={() => ask(x)}>
            {x}
          </button>
        ))}
      </div>
      {err && (
        <div className="rounded-xl bg-caution-soft px-3 py-2 text-ink-900" role="alert">
          <div className="font-semibold">{err.title}</div>
          {err.detail && <div className="text-sm text-ink-800">{err.detail}</div>}
          <div className="text-xs text-ink-600">오류 코드 {error!.code}</div>
        </div>
      )}
      {items.length > 0 && (
        <ul className="flex flex-col gap-2">
          {items.map((it) => (
            <FollowupAnswer key={it.res.followup_id} item={it} onCite={onCite} refName={refName} />
          ))}
        </ul>
      )}
    </div>
  )
}
