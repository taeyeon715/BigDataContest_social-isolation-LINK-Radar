import { useState } from 'react'
import { COUNTER_CHECKS, EXTRA_TOOLS, MAX_FOCUS_MEMO, type CounterCheck, type ExtraTool, type MoreFocus } from '../api'
import { TOOL_LABEL, TOOL_NOTE } from '../lib/labels'

export type MoreCandidate = { candidate_id: string; label: string }

/** 고른 값으로 focus 를 만든다. 아무것도 안 고르면 null (범위 없이 추가 조사) */
export function buildFocus(p: { ids: string[]; tools: ExtraTool[]; checks: CounterCheck[]; memo: string }): MoreFocus | null {
  const memo = p.memo.trim().slice(0, MAX_FOCUS_MEMO)
  const f: MoreFocus = {}
  if (p.ids.length) f.candidate_ids = p.ids
  if (p.tools.length) f.tools = p.tools
  if (p.checks.length) f.counter_checks = p.checks
  if (memo) f.memo = memo
  return Object.keys(f).length ? f : null
}

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v]
}

/** 추가 조사 범위 고르기: 후보, 추가 도구, 확인할 다른 원인, 메모 */
export function MoreForm({
  candidates,
  defaultIds = [],
  remaining,
  busy,
  onSubmit,
  onCancel,
}: {
  candidates: MoreCandidate[]
  defaultIds?: string[]
  remaining: number
  busy: boolean
  onSubmit: (focus: MoreFocus | null) => void
  onCancel: () => void
}) {
  const [ids, setIds] = useState<string[]>(defaultIds.filter((id) => candidates.some((c) => c.candidate_id === id)))
  const [tools, setTools] = useState<ExtraTool[]>([])
  const [checks, setChecks] = useState<CounterCheck[]>([])
  const [memo, setMemo] = useState('')
  return (
    <form
      className="flex flex-col gap-3 rounded-2xl border border-line p-4"
      data-testid="more-form"
      aria-label="추가 조사 범위"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit(buildFocus({ ids, tools, checks, memo }))
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-bold text-ink-900">추가 조사 범위</div>
        <span className="text-xs text-ink-600" data-testid="more-remaining">
          남은 추가 조사 {remaining}회 (최대 2회)
        </span>
      </div>
      <p className="text-xs text-ink-600">고르지 않으면 에이전트가 범위를 정합니다. 등급은 규칙이 정하므로 추가 조사로 바뀌지 않을 수 있습니다.</p>

      {candidates.length > 0 && (
        <fieldset>
          <legend className="mb-1 text-xs font-semibold text-ink-600">다시 볼 후보</legend>
          <div className="flex flex-col gap-1">
            {candidates.map((c) => (
              <label key={c.candidate_id} className="flex items-center gap-2 text-sm" title={`후보 ID ${c.candidate_id}`}>
                <input type="checkbox" className="h-4 w-4" checked={ids.includes(c.candidate_id)} onChange={() => setIds((l) => toggle(l, c.candidate_id))} />
                <span>{c.label}</span>
              </label>
            ))}
          </div>
        </fieldset>
      )}

      <fieldset>
        <legend className="mb-1 text-xs font-semibold text-ink-600">더 쓸 도구 (선택)</legend>
        <div className="flex flex-col gap-1">
          {EXTRA_TOOLS.map((t) => (
            <label key={t} className="flex items-start gap-2 text-sm" title={`도구 이름 ${t}`} data-tool={t}>
              <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0" checked={tools.includes(t)} onChange={() => setTools((l) => toggle(l, t))} />
              <span className="flex flex-col">
                <span>{TOOL_LABEL[t] ?? t}</span>
                {TOOL_NOTE[t] && <span className="text-xs text-ink-600">{TOOL_NOTE[t]}</span>}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-1 text-xs font-semibold text-ink-600">확인할 다른 원인 (선택)</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {COUNTER_CHECKS.map((c) => (
            <label key={c} className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4" checked={checks.includes(c)} onChange={() => setChecks((l) => toggle(l, c))} />
              {c}
            </label>
          ))}
        </div>
      </fieldset>

      <label className="flex flex-col gap-1 text-xs font-semibold text-ink-600">
        더 보고 싶은 점 (선택)
        <textarea
          className="field min-h-[60px] font-normal text-ink-900"
          maxLength={MAX_FOCUS_MEMO}
          placeholder="예: 연휴 뒤 회복이 늦은지 더 봐 주세요"
          value={memo}
          onChange={(e) => setMemo(e.target.value)}
        />
      </label>
      <div className="flex flex-wrap justify-between gap-2 text-xs">
        <span className="text-caution" data-testid="more-memo-warning">
          개인 정보·이름은 쓰지 마세요. 메모는 도구 계획으로만 바뀌고 원문은 보고서·기록에 남지 않습니다.
        </span>
        <span className="text-ink-600">
          {memo.length} / {MAX_FOCUS_MEMO}자
        </span>
      </div>

      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel} disabled={busy}>
          취소
        </button>
        <button type="submit" className="btn-primary" disabled={busy || remaining <= 0}>
          {busy ? '보내는 중…' : '추가 조사 보내기'}
        </button>
      </div>
    </form>
  )
}
