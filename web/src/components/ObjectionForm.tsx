// 이의·정정 기록 (공공부문 AI 윤리기준의 사후관리·피해구제). 후보 상세 화면에서 담당자가 "이 후보는 현장 사정과 다르다"를
// 사유와 함께 남긴다. 남기기는 검토 이상, 반영·기각은 승인 역할. 자유 글은 서버가 저장 전에 개인정보를 가린다.
import { useEffect, useId, useState, type FormEvent } from 'react'
import { ApiError, OBJECTION_REASONS, api, type City, type ObjectionReason, type ObjectionRecord } from '../api'
import { errorText, fmtTime } from '../lib/labels'
import { Badge, PersonName, RoleNote, useRole } from './ui'

const MAX_TEXT = 1000
const STATUS_TONE: Record<ObjectionRecord['status'], 'gray' | 'blue' | 'okay' | 'caution'> = {
  접수: 'gray',
  '검토 중': 'blue',
  반영: 'okay',
  기각: 'caution',
}

function newKey(candidateId: string): string {
  return `obj:${candidateId}:${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`.slice(0, 100)
}

export function ObjectionForm({ city, candidateId, admCd, runId }: { city: City; candidateId: string; admCd: string | null; runId?: string | null }) {
  const { can } = useRole()
  const ids = { title: useId(), reason: useId(), text: useId(), hint: useId() }
  const [reason, setReason] = useState<ObjectionReason>(OBJECTION_REASONS[0])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [items, setItems] = useState<ObjectionRecord[]>([])
  const [key, setKey] = useState(() => newKey(candidateId))

  useEffect(() => {
    let alive = true
    api
      .objections({ candidate_id: candidateId })
      .then((r) => alive && setItems(r.items))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [candidateId])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.createObjection({
        target_type: 'candidate',
        candidate_id: candidateId,
        run_id: runId ?? null,
        city,
        adm_cd: admCd,
        reason_category: reason,
        text: text.trim() || null,
        idempotency_key: key,
      })
      setItems((xs) => [r.objection, ...xs.filter((x) => x.objection_id !== r.objection.objection_id)])
      setText('')
      setKey(newKey(candidateId))
      setMsg({ ok: true, text: '이의·정정을 접수했습니다. 승인 담당자가 검토합니다.' })
    } catch (err) {
      const t = err instanceof ApiError ? errorText(err.code, err.message) : { title: '접수하지 못했습니다.', detail: null }
      setMsg({ ok: false, text: t.title })
    } finally {
      setBusy(false)
    }
  }

  const decide = async (o: ObjectionRecord, status: '검토 중' | '반영' | '기각') => {
    try {
      const r = await api.objectionStatus(o.objection_id, { status })
      setItems((xs) => xs.map((x) => (x.objection_id === o.objection_id ? r.objection : x)))
    } catch (err) {
      const t = err instanceof ApiError ? errorText(err.code, err.message) : { title: '바꾸지 못했습니다.', detail: null }
      setMsg({ ok: false, text: t.title })
    }
  }

  return (
    <section aria-labelledby={ids.title} className="rounded-2xl border border-line bg-surface px-4 py-3" data-testid="objection-form">
      <h3 id={ids.title} className="font-semibold text-ink-900">
        이의·정정 남기기
      </h3>
      <p className="text-xs text-ink-600">
        이 후보가 현장 사정과 다르거나 근거·자원 정보가 틀렸으면 사유를 남겨 주세요. 등급을 바로 바꾸지 않고, 승인 담당자가 검토해 다음 규칙 평가에 반영합니다.
      </p>
      <form className="mt-2 flex flex-col gap-2" onSubmit={submit}>
        <label htmlFor={ids.reason} className="text-xs font-semibold text-ink-600">
          사유
        </label>
        <select id={ids.reason} className="field py-1.5" value={reason} onChange={(e) => setReason(e.target.value as ObjectionReason)} disabled={!can('검토')}>
          {OBJECTION_REASONS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <label htmlFor={ids.text} className="text-xs font-semibold text-ink-600">
          내용 (선택)
        </label>
        <textarea
          id={ids.text}
          className="field min-h-[72px]"
          maxLength={MAX_TEXT}
          aria-describedby={ids.hint}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={!can('검토')}
        />
        <div id={ids.hint} className="text-xs text-ink-600">
          이름·전화번호·주소 같은 개인 정보는 적지 마세요. 적더라도 저장 전에 가려집니다. ({text.length}/{MAX_TEXT})
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" className="btn-primary" disabled={busy || !can('검토')}>
            {busy ? '접수 중…' : '이의·정정 접수'}
          </button>
          {msg && (
            <span role={msg.ok ? 'status' : 'alert'} className={`text-xs ${msg.ok ? 'text-okay' : 'text-caution'}`}>
              {msg.text}
            </span>
          )}
        </div>
        {!can('검토') && <RoleNote need="검토" />}
      </form>
      {items.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs font-semibold text-ink-600">이 후보에 남긴 이의·정정 ({items.length})</div>
          <ul className="flex flex-col gap-2" data-testid="objection-list">
            {items.map((o) => (
              <li key={o.objection_id} className="rounded-xl bg-canvas px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={STATUS_TONE[o.status]}>{o.status}</Badge>
                  <b>{o.reason_category}</b>
                  <span className="text-xs text-ink-600">
                    {fmtTime(o.created_at)} · <PersonName name={o.submitted_by} />
                  </span>
                </div>
                {o.text && <div className="mt-1 text-ink-800">{o.text}</div>}
                {o.resolution && <div className="mt-1 text-xs text-ink-600">처리: {o.resolution}</div>}
                {can('승인') && (o.status === '접수' || o.status === '검토 중') && (
                  <div className="mt-1 flex flex-wrap gap-2" aria-label="이의·정정 처리">
                    {o.status === '접수' && (
                      <button type="button" className="btn-ghost px-3 py-1 text-xs" onClick={() => decide(o, '검토 중')}>
                        검토 중으로
                      </button>
                    )}
                    <button type="button" className="btn-ghost px-3 py-1 text-xs" onClick={() => decide(o, '반영')}>
                      반영
                    </button>
                    <button type="button" className="btn-ghost px-3 py-1 text-xs" onClick={() => decide(o, '기각')}>
                      기각
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
