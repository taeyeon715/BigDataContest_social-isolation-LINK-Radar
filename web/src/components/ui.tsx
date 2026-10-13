import type { ReactNode } from 'react'
import type { Candidate, CandidateLevel, CandidateType } from '../api'
import { ApiError } from '../api'
import { Term } from './Term'
import { errorText, LEVEL_LEGEND, LEVEL_NAME, TYPE_LABEL, counterView } from '../lib/labels'

const LEVEL_STYLE: Record<CandidateLevel, string> = {
  'Level 0': 'bg-lv0 text-ink-800',
  'Level 1': 'bg-lv1 text-ink-900',
  'Level 2': 'bg-lv2 text-ink-900', // 흰 글자는 대비 2.9:1 이라 진한 글자
  'Level 3': 'bg-lv3 text-white',
  '판단 보류': 'bg-holdstripe text-ink-800 border border-hold',
}

export function LevelBadge({ level, withName = false }: { level: CandidateLevel | null; withName?: boolean }) {
  if (!level) return <span className="chip bg-lv0 text-ink-600">등급 전</span>
  return (
    <span className={`chip ${LEVEL_STYLE[level]}`} data-testid="level-badge" title={`${LEVEL_NAME[level]} · ${LEVEL_LEGEND}`}>
      {level}
      {withName && level !== '판단 보류' ? ` · ${LEVEL_NAME[level]}` : ''}
    </span>
  )
}

export function TypeChip({ type }: { type: CandidateType }) {
  const cls = type === 'scheduled_access_review' ? 'bg-scheduled-soft text-scheduled-ink' : 'bg-deviation-soft text-deviation-ink'
  return <span className={`chip ${cls}`}>{TYPE_LABEL[type]}</span>
}

/** 필수 근거가 일부 빠진 후보 (부분 판정) / 모두 빠진 후보 (보류) */
export function InputStatusBadge({ status }: { status: Candidate['input_status'] }) {
  if (status === '충족') return null
  return (
    <span
      className="chip bg-caution-soft text-caution"
      data-testid="input-status"
      title={status === '부분 판정' ? '부분 판정: 필수 근거 중 일부가 없어 있는 근거만으로 판단했습니다' : '필수 근거가 없어 판단을 미뤘습니다'}
    >
      {status === '부분 판정' ? '근거 일부 빠짐' : '근거 보류'}
    </span>
  )
}

export function SelectedBadge({ c }: { c: Candidate }) {
  if (c.selected_for_review)
    return (
      <span className="chip bg-brand-50 text-brand-700" title={`이번에 볼 수 있는 수(K=${c.selection_budget}) 안에 들어 먼저 볼 후보`}>
        검토 배정 {c.queue_rank} / {c.selection_budget}건
      </span>
    )
  if (c.queue_rank) return <span className="chip bg-canvas text-ink-600">대기 순번 {c.queue_rank}</span>
  return null
}

const COUNTER_STYLE = {
  pass: 'bg-okay-soft text-okay',
  reject: 'bg-lv0 text-ink-800',
  hold: 'bg-holdstripe text-ink-800',
  check: 'bg-caution-soft text-caution',
  review: 'bg-brand-50 text-brand-700',
  none: 'bg-canvas text-ink-600',
} as const

export function CounterChip({ c }: { c: Pick<Candidate, 'counter_result' | 'counter_reason'> }) {
  const v = counterView(c)
  return (
    <span className={`chip ${COUNTER_STYLE[v.tone]}`} data-testid="counter-chip">
      {v.label}
    </span>
  )
}

export function Card({ title, right, children, className = '' }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card p-5 sm:p-6 ${className}`}>
      {(title || right) && (
        <header className="mb-4 flex flex-wrap items-center justify-between gap-2">
          {title && <h2 className="text-base font-bold text-ink-900">{title}</h2>}
          {right}
        </header>
      )}
      {children}
    </section>
  )
}

export function Loading({ label = '불러오는 중' }: { label?: string }) {
  return (
    <div className="flex flex-col gap-3" role="status" aria-label={label}>
      <div className="h-4 w-1/3 animate-pulse rounded bg-line" />
      <div className="h-4 w-2/3 animate-pulse rounded bg-line" />
      <div className="h-4 w-1/2 animate-pulse rounded bg-line" />
      <span className="text-xs text-ink-600">{label}…</span>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-2xl border border-dashed border-line bg-canvas/60 px-4 py-8 text-center text-ink-600">{children}</div>
}

/** 오류: 쉬운 문장이 제목, 코드는 작은 회색 글자 */
export function ErrorBox({ error, onRetry }: { error: ApiError | Error; onRetry?: () => void }) {
  const code = error instanceof ApiError ? error.code : 'ERROR'
  const { title, detail } = errorText(code, error.message)
  return (
    <div className="rounded-2xl border border-caution/40 bg-caution-soft px-4 py-3 text-ink-900" role="alert">
      <div className="font-semibold">{title}</div>
      {detail && <div className="mt-0.5 text-sm text-ink-800">{detail}</div>}
      <div className="mt-1 text-xs text-ink-600">오류 코드 {code}</div>
      {onRetry && (
        <button className="btn-ghost mt-3" onClick={onRetry}>
          다시 시도
        </button>
      )}
    </div>
  )
}

/** simulated_replay 결과에 붙는 "가정한 공개 시점" 띠 */
export function TimeLabelBar({ label }: { label: string | null | undefined }) {
  if (!label) return null
  return (
    <div className="rounded-2xl border border-brand-100 bg-brand-50 px-4 py-2 text-xs text-brand-700" data-testid="time-label">
      <b className="mr-1">
        <Term>가정한 공개 시점</Term>
      </b>
      {label.replace(/^가정한 공개 시점:\s*/, '')}
    </div>
  )
}

/** 판단 보류 안내 (회색 줄무늬) */
export function HoldNotice({ title, reasons, needed }: { title: string; reasons: string[]; needed?: string[] }) {
  return (
    <div className="rounded-2xl border border-hold bg-holdstripe px-4 py-3" data-testid="hold-notice">
      <div className="font-bold text-ink-900">{title}</div>
      {reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-ink-800">
          {reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      {needed && needed.length > 0 && (
        <div className="mt-2">
          <div className="text-xs font-semibold text-ink-600">더 확보하면 되는 자료</div>
          <ul className="mt-1 list-disc pl-5 text-ink-800">
            {needed.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function Badge({ children, tone = 'gray' }: { children: ReactNode; tone?: 'gray' | 'blue' | 'caution' | 'okay' | 'dark' }) {
  const cls = {
    gray: 'bg-canvas text-ink-600 border border-line',
    blue: 'bg-brand-50 text-brand-700',
    caution: 'bg-caution-soft text-caution',
    okay: 'bg-okay-soft text-okay',
    dark: 'bg-ink-900 text-white',
  }[tone]
  return <span className={`chip ${cls}`}>{children}</span>
}
