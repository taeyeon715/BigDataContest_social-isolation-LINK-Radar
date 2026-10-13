import { useEffect, useState } from 'react'
import { ApiError, OUTCOME_FIELDS, OUTCOME_LABEL, api, type OutcomeCounts, type OutcomeRecord } from '../api'
import { errorText, fmtTime } from '../lib/labels'

const EMPTY: OutcomeCounts = { checked_households: 0, contacted: 0, absent: 0, new_links: 0, crises_found: 0 }

/** 건수 사이 관계 검사 (서버와 같은 규칙). 문제가 없으면 null */
export function outcomeProblem(c: OutcomeCounts): string | null {
  if (OUTCOME_FIELDS.some((f) => !Number.isInteger(c[f]) || c[f] < 0)) return '0 이상의 정수만 적을 수 있습니다.'
  if (c.contacted + c.absent > c.checked_households) return '연락됨 + 부재가 확인 가구 수보다 많습니다.'
  if (c.new_links > c.checked_households || c.crises_found > c.checked_households)
    return '신규 연계·위기 발견 수는 확인 가구 수보다 많을 수 없습니다.'
  return null
}

function newKey(runId: string, candidateId: string): string {
  return `${runId}:${candidateId}:${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`.slice(0, 100)
}

/** 승인 뒤 현장 확인 결과. 이 동·집단의 건수만 적음. 이 화면에서는 10 미만도 그대로 보임 */
export function OutcomeForm({ runId, candidateId }: { runId: string; candidateId: string }) {
  const [saved, setSaved] = useState<OutcomeRecord | null>(null)
  const [draft, setDraft] = useState<OutcomeCounts>(EMPTY)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [key, setKey] = useState(() => newKey(runId, candidateId))

  useEffect(() => {
    let alive = true
    api
      .runOutcomes(runId)
      .then((r) => {
        if (!alive) return
        const mine = r.items.find((x) => x.candidate_id === candidateId) ?? null
        setSaved(mine)
        if (mine) setDraft(Object.fromEntries(OUTCOME_FIELDS.map((f) => [f, mine[f]])) as OutcomeCounts)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [runId, candidateId])

  const problem = outcomeProblem(draft)
  const save = async () => {
    if (problem) return
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.recordOutcome(runId, { candidate_id: candidateId, ...draft, idempotency_key: key })
      setSaved(r.outcome)
      setKey(newKey(runId, candidateId)) // 다음에 고쳐 적으면 새 기록
      setMsg({ ok: true, text: '현장 확인 결과를 저장했습니다.' })
    } catch (e) {
      const t = e instanceof ApiError ? errorText(e.code, e.message) : { title: '저장하지 못했습니다.', detail: null }
      setMsg({ ok: false, text: t.title })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 rounded-2xl border border-line bg-surface px-4 py-3" data-testid="outcome-form">
      <div className="font-semibold text-ink-900">현장 확인 결과</div>
      <p className="text-xs text-ink-600">
        이 동·집단의 건수만 적습니다. 이름·주소·전화번호는 적지 않습니다. 모아 보기에서는 10 미만 칸을 숫자 없이 "10 미만"으로 보입니다.
      </p>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {OUTCOME_FIELDS.map((f) => (
          <label key={f} className="flex flex-col text-xs text-ink-600">
            {OUTCOME_LABEL[f]}
            <input
              type="number"
              min={0}
              step={1}
              inputMode="numeric"
              className="field mt-1 py-1.5"
              aria-label={OUTCOME_LABEL[f]}
              value={draft[f]}
              onChange={(e) => setDraft({ ...draft, [f]: e.target.value === '' ? 0 : Number(e.target.value) })}
            />
          </label>
        ))}
      </div>
      {problem && (
        <div className="mt-1 text-xs text-caution" role="alert">
          {problem}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button className="btn-primary" disabled={busy || !!problem} onClick={save}>
          {busy ? '저장 중…' : saved ? '고쳐 저장' : '결과 저장'}
        </button>
        {saved && (
          <span className="text-xs text-ink-600" data-testid="outcome-saved">
            마지막 저장 {fmtTime(saved.created_at)} · {saved.recorded_by}
          </span>
        )}
        {msg && <span className={`text-xs ${msg.ok ? 'text-okay' : 'text-caution'}`}>{msg.text}</span>}
      </div>
    </div>
  )
}
