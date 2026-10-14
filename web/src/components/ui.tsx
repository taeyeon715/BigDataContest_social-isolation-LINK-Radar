import { useEffect, useState, type ReactNode } from 'react'
import type { Candidate, CandidateLevel, CandidateType } from '../api'
import { ApiError, ROLE_RANK, currentRole, onRoleChange, type Role } from '../api'
import { Term } from './Term'
import { errorText, isSharedName, LEVEL_LEGEND, LEVEL_NAME, TYPE_LABEL, counterView } from '../lib/labels'

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

/** 오래 걸릴 때 안내 (FE-04). 실제 모드 첫 계산은 10초 넘게 걸릴 수 있다 */
export const SLOW_HINT_MS = 3_000
export const STUCK_HINT_MS = 20_000
export const SLOW_TEXT = '처음 계산 중이라 10초 정도 걸릴 수 있습니다. 멈춘 것이 아닙니다.'
export const STUCK_TEXT = '20초 넘게 답이 없습니다. API가 켜져 있는지 확인하거나 다시 시도하세요.'

/** 불러오는 중: 회색 뼈대 + 3초 뒤 "처음 계산 중" 안내, 20초 뒤 [다시 시도] */
export function Loading({ label = '불러오는 중', onRetry, compact = false }: { label?: string; onRetry?: () => void; compact?: boolean }) {
  const [stage, setStage] = useState<0 | 1 | 2>(0)
  useEffect(() => {
    const a = setTimeout(() => setStage(1), SLOW_HINT_MS)
    const b = setTimeout(() => setStage(2), STUCK_HINT_MS)
    return () => {
      clearTimeout(a)
      clearTimeout(b)
    }
  }, [])
  return (
    <div className="flex flex-col gap-3" role="status" aria-label={label} data-testid="loading">
      {!compact && (
        <>
          <div className="h-4 w-1/3 animate-pulse rounded bg-line" />
          <div className="h-4 w-2/3 animate-pulse rounded bg-line" />
          <div className="h-4 w-1/2 animate-pulse rounded bg-line" />
        </>
      )}
      <span className="text-xs text-ink-600">{label}…</span>
      {stage >= 1 && (
        <span className="text-xs text-ink-800" data-testid="slow-hint">
          {stage === 1 ? SLOW_TEXT : STUCK_TEXT}
        </span>
      )}
      {stage === 2 && onRetry && (
        <button type="button" className="btn-ghost self-start" onClick={onRetry}>
          다시 시도
        </button>
      )}
    </div>
  )
}

/** 불러오기 결과 하나를 네 상태로: 불러오는 중 / 오류(다시 시도) / 값. 오류를 "없음"으로 보이지 않게 (FE-01) */
export function AsyncBlock<T>({
  state,
  label,
  children,
}: {
  state: { data: T | null; error: ApiError | null; loading: boolean; reload: () => void }
  label?: string
  children: (data: T) => ReactNode
}) {
  if (state.error && !state.data) return <ErrorBox error={state.error} onRetry={state.reload} />
  if (!state.data) return state.loading ? <Loading label={label} onRetry={state.reload} /> : null
  return (
    <>
      {state.error && <ErrorBox error={state.error} onRetry={state.reload} compact />}
      {children(state.data)}
    </>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-2xl border border-dashed border-line bg-canvas/60 px-4 py-8 text-center text-ink-600">{children}</div>
}

/** 오류: 쉬운 문장이 제목, 코드는 작은 회색 글자. compact 는 이미 보이는 값 위에 얹는 한 줄 */
export function ErrorBox({ error, onRetry, compact = false }: { error: ApiError | Error; onRetry?: () => void; compact?: boolean }) {
  const code = error instanceof ApiError ? error.code : 'ERROR'
  const { title, detail } = errorText(code, error.message, undefined, error instanceof ApiError ? error.retryAfter : null)
  if (compact)
    return (
      <div className="mb-2 flex flex-wrap items-center gap-2 rounded-xl bg-caution-soft px-3 py-1.5 text-xs text-ink-900" role="alert">
        <span className="font-semibold">새로 불러오지 못해 이전 값을 보이는 중입니다.</span>
        <span className="text-ink-600">{title} · 오류 코드 {code}</span>
        {onRetry && (
          <button type="button" className="font-semibold text-brand-700 underline" onClick={onRetry}>
            다시 시도
          </button>
        )}
      </div>
    )
  return (
    <div className="rounded-2xl border border-caution/40 bg-caution-soft px-4 py-3 text-ink-900" role="alert" data-testid="error-box">
      <div className="font-semibold">{title}</div>
      {detail && <div className="mt-0.5 text-sm text-ink-800">{detail}</div>}
      <div className="mt-1 text-xs text-ink-600">오류 코드 {code}</div>
      {onRetry && (
        <button type="button" className="btn-ghost mt-3" onClick={onRetry}>
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

/** 지금 역할(짐작)과 need 이상인지. 403 을 받으면 내려가고 화면이 다시 그려짐 */
export function useRole(): { role: Role; can: (need: Role) => boolean } {
  const [r, setR] = useState<Role>(currentRole())
  useEffect(() => onRoleChange(() => setR(currentRole())), [])
  return { role: r, can: (need: Role) => ROLE_RANK[r] >= ROLE_RANK[need] }
}

/** 권한이 모자라 버튼을 잠갔을 때 한 줄 안내 */
export function RoleNote({ need }: { need: Role }) {
  return (
    <div className="text-xs text-ink-600" data-testid="role-note">
      {need} 권한이 필요합니다. 지금 계정은 {currentRole()} 권한이라 볼 수만 있습니다. 담당자 계정(토큰)으로 들어오세요.
    </div>
  )
}

/** 검토자·승인자 이름. 공용 토큰 기록("공용:<이름>")이면 그대로 보이고 "공용 계정" 표시를 붙임 */
export function PersonName({ name }: { name: string }) {
  if (!isSharedName(name)) return <>{name}</>
  return (
    <>
      {name}{' '}
      <span className="chip bg-canvas text-ink-600" title="여럿이 같이 쓰는 공용 계정으로 기록됐습니다. 실제로 누가 했는지는 따로 적어 두세요." data-testid="shared-account">
        공용 계정
      </span>
    </>
  )
}
