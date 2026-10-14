import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Decision, MoreFocus, RunStatusResponse } from '../api'
import { ApiError, MAX_EXTRA_ROUNDS } from '../api'
import { PHASE_LABEL, errorText, fmtTime } from '../lib/labels'
import { MoreForm, type MoreCandidate } from './MoreForm'
import { PersonName, RoleNote, useRole } from './ui'

type RunLike = Pick<RunStatusResponse, 'status' | 'report_revision' | 'report_stale' | 'abstain_reason'> &
  Partial<Pick<RunStatusResponse, 'run_id' | 'snapshot_id' | 'published_report_revision' | 'extra_rounds'>>

/** 보고서를 다시 만드는 중인가: report_stale 이거나, 요청한 판이 발행된 판보다 앞섬 */
export function regenerating(run: RunLike): boolean {
  if (run.report_stale) return true
  const pub = run.published_report_revision
  return pub != null && run.report_revision != null && run.report_revision > pub
}

/** 남은 추가 조사 횟수 */
export function remainingRounds(run: RunLike): number {
  return Math.max(0, MAX_EXTRA_ROUNDS - (run.extra_rounds ?? 0))
}

/** 화면에 실제로 띄운 보고서 (실행·스냅샷·판). 승인은 이것이 지금 실행 상태와 같을 때만 */
export type ShownReport = { run_id: string; snapshot_id: string; report_revision: number } | null

/** 승인이 막힌 이유. null 이면 승인 가능. shown 을 주면 화면의 보고서가 지금 판과 같은지도 본다 */
export function approvalBlock(run: RunLike, shown?: ShownReport): string | null {
  if (run.status === 'abstained') return '계약 위반으로 보류된 실행이라 승인으로 풀 수 없습니다.'
  if (run.status === 'approved') return '승인으로 확정되었습니다.'
  if (run.status === 'held') return '보류로 기록되었습니다 (확정).'
  if (run.status !== 'awaiting_approval') return `지금 상태(${PHASE_LABEL[run.status]})에서는 승인할 수 없습니다.`
  if (regenerating(run)) {
    const now = shown?.report_revision ?? run.published_report_revision
    if (now && now === run.report_revision) return `보고서 ${now}판을 마무리하는 중입니다. 잠시 뒤 승인할 수 있습니다.`
    return `보고서를 ${run.report_revision}판으로 다시 만드는 중입니다.${now ? ` 지금 화면은 ${now}판입니다.` : ''} 새 판이 나오면 승인할 수 있습니다.`
  }
  if (shown !== undefined) {
    if (shown === null) return '보고서를 불러오는 중입니다. 화면에 보고서가 나온 뒤 승인할 수 있습니다.'
    const sameRun = !run.run_id || shown.run_id === run.run_id
    const sameSnap = !run.snapshot_id || shown.snapshot_id === run.snapshot_id
    if (!sameRun || !sameSnap || shown.report_revision !== run.report_revision)
      return `화면의 보고서(${shown.report_revision}판)가 지금 판(${run.report_revision ?? '—'}판)과 다릅니다. 새 판을 불러온 뒤 승인할 수 있습니다.`
  }
  return null
}

/** 승인 전 확인 항목 하나 (근거 모두 확인 / 자원 판정 / 검토 저장) */
export type PreCheck = { key: string; label: string; ok: boolean; detail?: string }

/** 승인 전 확인 항목을 만든다. 추천 자원이 없으면 자원 판정 항목은 충족으로 본다 */
export function preChecks(p: { evidenceIds: string[]; checked: Set<string>; resourceCount: number; judgedCount: number; reviewSaved: boolean }): PreCheck[] {
  const seen = p.evidenceIds.filter((id) => p.checked.has(id)).length
  return [
    { key: 'evidence', label: '근거를 모두 확인함', ok: seen === p.evidenceIds.length, detail: `${seen} / ${p.evidenceIds.length}` },
    {
      key: 'resource',
      label: '자원을 1개 이상 판정함',
      ok: p.resourceCount === 0 || p.judgedCount > 0,
      detail: p.resourceCount === 0 ? '추천 자원 없음' : `${p.judgedCount}건`,
    },
    { key: 'review', label: '검토를 저장함', ok: p.reviewSaved, detail: p.reviewSaved ? '저장됨' : '저장 안 함' },
  ]
}

/** 승인 기록 (완료 카드용) */
export type ApprovalDone = { report_revision: number; approver: string; created_at: string } | null

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Tab / Shift+Tab 이 container 밖으로 나가지 않게 (처음 ↔ 끝 돌기). 처리했으면 true */
export function trapTab(container: HTMLElement, e: { key: string; shiftKey: boolean; preventDefault: () => void }): boolean {
  if (e.key !== 'Tab') return false
  const items = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE))
  if (items.length === 0) return false
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement as HTMLElement | null
  const inside = !!active && container.contains(active)
  if (e.shiftKey && (active === first || !inside)) {
    e.preventDefault()
    last.focus()
    return true
  }
  if (!e.shiftKey && (active === last || !inside)) {
    e.preventDefault()
    first.focus()
    return true
  }
  return false
}

/** 미충족 항목이 있을 때 "그래도 승인" 확인창. 브라우저 기본 <dialog>(showModal)로 뒤 화면을 막고,
 * Tab 은 창 안에서만 돌며, 닫으면 연 버튼으로 포커스를 돌려줌 (FE-05) */
export function ConfirmDialog({ unmet, onOk, onCancel }: { unmet: PreCheck[]; onOk: () => void; onCancel: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const d = ref.current
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (d) {
      if (typeof d.showModal === 'function') {
        if (!d.open) d.showModal()
      } else d.setAttribute('open', '') // jsdom 등 showModal 이 없는 환경
    }
    cancelRef.current?.focus()
    return () => {
      if (d?.open && typeof d.close === 'function') d.close()
      if (opener && opener.isConnected) opener.focus()
    }
  }, [])
  return (
    <dialog
      ref={ref}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
      aria-describedby="confirm-body"
      className="w-[calc(100%-2rem)] max-w-md rounded-card bg-surface p-6 text-ink-900 shadow-lift backdrop:bg-ink-900/40"
      onCancel={(e) => {
        // Esc: 브라우저가 닫기 전에 React 상태로 닫음
        e.preventDefault()
        onCancel()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault()
          onCancel()
          return
        }
        if (ref.current) trapTab(ref.current, e)
      }}
    >
      <h2 id="confirm-title" className="text-base font-bold text-ink-900">
        아직 끝나지 않은 확인이 있습니다
      </h2>
      <div id="confirm-body" className="mt-2 text-ink-800">
        <ul className="list-disc pl-5">
          {unmet.map((c) => (
            <li key={c.key}>
              {c.label}
              {c.detail ? ` (${c.detail})` : ''}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-sm text-ink-600">최종 판단은 담당자가 합니다. 이대로 승인하면 지금 상태 그대로 승인 기록이 남습니다.</p>
      </div>
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <button ref={cancelRef} type="button" className="btn-ghost" onClick={onCancel}>
          돌아가서 확인하기
        </button>
        <button type="button" className="btn-primary" onClick={onOk}>
          그래도 승인
        </button>
      </div>
    </dialog>
  )
}

/** 승인 뒤 초록 완료 카드: 몇 판으로 누가 언제, 다음 할 일 */
function DoneCard({
  done,
  fallbackRevision,
  onPrint,
  nextHref,
  children,
}: {
  done: ApprovalDone
  fallbackRevision: number | null
  onPrint?: () => void
  nextHref?: string | null
  children?: ReactNode
}) {
  return (
    <div className="rounded-2xl border border-okay/30 bg-okay-soft px-4 py-4" data-testid="approval-done">
      <div className="flex items-center gap-2 font-bold text-okay">
        <span aria-hidden className="flex h-6 w-6 items-center justify-center rounded-full bg-okay text-white">
          ✓
        </span>
        {done?.report_revision ?? fallbackRevision ?? '—'}판으로 승인됨
      </div>
      {done && (
        <div className="mt-1 text-sm text-ink-800" data-testid="approval-done-by">
          승인자 <PersonName name={done.approver} /> · {fmtTime(done.created_at)}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {onPrint && (
          <button className="btn-ghost" onClick={onPrint}>
            인쇄용 보기
          </button>
        )}
        {nextHref && (
          <Link className="btn-primary" to={nextHref}>
            다음 배정 후보 →
          </Link>
        )}
      </div>
      {children}
    </div>
  )
}

/** 승인 요청 오류: 쉬운 문장 먼저, 서버 문장은 보조, 코드는 작게 */
function DecideError({ error }: { error: Error }) {
  const code = error instanceof ApiError ? error.code : null
  const { title, detail } = errorText(code, error.message, '승인 요청에 실패했습니다.', error instanceof ApiError ? error.retryAfter : null)
  return (
    <div className="rounded-xl bg-caution-soft px-3 py-2 text-ink-900" role="alert">
      <div className="font-semibold">{title}</div>
      {detail && <div className="text-sm text-ink-800">{detail}</div>}
      {code && <div className="text-xs text-ink-600">오류 코드 {code}</div>}
    </div>
  )
}

export function ApprovalPanel({
  run,
  shown,
  onDecide,
  checks,
  done = null,
  onPrint,
  nextHref,
  moreCandidates,
  moreDefaultIds,
  outcome,
}: {
  run: RunLike
  shown?: ShownReport
  onDecide: (d: Decision, memo: string | null, focus?: MoreFocus | null) => Promise<void>
  checks?: PreCheck[]
  done?: ApprovalDone
  onPrint?: () => void
  nextHref?: string | null
  /** 추가 조사에서 고를 후보 (이 실행의 후보)와 처음에 골라 둘 후보 */
  moreCandidates?: MoreCandidate[]
  moreDefaultIds?: string[]
  /** 승인 뒤 완료 카드 안에 넣을 현장 확인 결과 입력 */
  outcome?: ReactNode
}) {
  const [busy, setBusy] = useState<Decision | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [memo, setMemo] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const [exhausted, setExhausted] = useState(false) // 서버가 EXTRA_ROUNDS_EXCEEDED 로 답함
  const block = approvalBlock(run, shown)
  const { can } = useRole()
  const noRole = !can('승인') // 승인·보류·추가 조사는 승인 역할 (API 명세 §1)
  const approveBlocked = block !== null || !!run.abstain_reason || noRole
  const otherBlocked = block !== null || noRole
  const remaining = exhausted ? 0 : remainingRounds(run)
  const unmet = (checks ?? []).filter((c) => !c.ok)

  const decide = async (d: Decision, focus?: MoreFocus | null) => {
    setBusy(d)
    setError(null)
    try {
      if (d === 'more') await onDecide(d, memo || null, focus ?? null)
      else await onDecide(d, memo || null)
      if (d === 'more') setMoreOpen(false)
    } catch (e) {
      if (e instanceof ApiError && e.code === 'EXTRA_ROUNDS_EXCEEDED') {
        setExhausted(true)
        setMoreOpen(false)
      }
      setError(e instanceof Error ? e : new Error('승인 요청에 실패했습니다.'))
    } finally {
      setBusy(null)
    }
  }
  // 승인 전 확인이 덜 됐으면 막지는 않고 "그래도 승인" 확인창을 띄운다
  const approve = () => {
    if (unmet.length > 0) setConfirming(true)
    else decide('approved')
  }

  if (run.status === 'approved')
    return (
      <DoneCard done={done} fallbackRevision={run.report_revision} onPrint={onPrint} nextHref={nextHref}>
        {outcome}
      </DoneCard>
    )

  return (
    <div className="flex flex-col gap-3" data-testid="approval-panel">
      <div className="flex flex-wrap items-center gap-2 text-ink-600">
        <span>보고서</span>
        <span className="chip bg-ink-900 text-white">
          {shown !== undefined ? (shown?.report_revision ?? '—') : (run.published_report_revision ?? run.report_revision ?? '—')}판
        </span>
        {regenerating(run) && (
          <span className="chip bg-caution-soft text-caution" data-testid="regenerating">
            보고서 다시 만드는 중 ({run.report_revision}판)
          </span>
        )}
      </div>
      {checks && checks.length > 0 && !block && (
        <div data-testid="pre-checks">
          <div className="mb-1 text-xs font-semibold text-ink-600">승인 전 확인</div>
          <ul className="flex flex-col gap-1">
            {checks.map((c) => (
              <li key={c.key} className="flex flex-wrap items-center gap-2 text-sm" data-ok={c.ok}>
                <span
                  aria-hidden
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                    c.ok ? 'bg-okay text-white' : 'border border-caution bg-caution-soft text-caution'
                  }`}
                >
                  {c.ok ? '✓' : '!'}
                </span>
                <span className={c.ok ? 'text-ink-800' : 'font-semibold text-ink-900'}>{c.label}</span>
                {c.detail && <span className="text-xs text-ink-600">{c.detail}</span>}
                <span className="sr-only">{c.ok ? '충족' : '아직 안 됨'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {noRole && !block && <RoleNote need="승인" />}
      {block && (
        <div className="rounded-xl bg-canvas px-3 py-2 text-ink-800" data-testid="approval-block">
          {block}
        </div>
      )}
      <textarea
        className="field min-h-[60px]"
        placeholder="승인 메모 (선택)"
        aria-label="승인 메모"
        value={memo}
        onChange={(e) => setMemo(e.target.value)}
        disabled={otherBlocked}
      />
      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" disabled={approveBlocked || busy !== null} onClick={approve}>
          {busy === 'approved' ? '보내는 중…' : '승인'}
        </button>
        <button className="btn-ghost" disabled={otherBlocked || busy !== null} onClick={() => decide('held')}>
          보류
        </button>
        <button
          className="btn-ghost"
          disabled={otherBlocked || busy !== null || remaining <= 0 || moreOpen}
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen(true)}
        >
          추가 조사 (남은 {remaining}회)
        </button>
      </div>
      {remaining <= 0 && !otherBlocked && (
        <div className="text-xs text-ink-600" data-testid="more-exhausted">
          추가 조사 {MAX_EXTRA_ROUNDS}번을 모두 썼습니다. 승인하거나 보류하세요.
        </div>
      )}
      {moreOpen && !otherBlocked && (
        <MoreForm
          candidates={moreCandidates ?? []}
          defaultIds={moreDefaultIds}
          remaining={remaining}
          busy={busy !== null}
          onCancel={() => setMoreOpen(false)}
          onSubmit={(focus) => decide('more', focus)}
        />
      )}
      {error && <DecideError error={error} />}
      <div className="text-xs text-ink-600">승인은 화면에 보이는 보고서 판으로만 보냅니다. 같은 요청을 두 번 보내도 기록은 한 건입니다.</div>
      {confirming && (
        <ConfirmDialog
          unmet={unmet}
          onCancel={() => setConfirming(false)}
          onOk={() => {
            setConfirming(false)
            decide('approved')
          }}
        />
      )}
    </div>
  )
}
