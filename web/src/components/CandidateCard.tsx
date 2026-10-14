import { useRef, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Candidate } from '../api'
import { followState, type FollowUp } from '../lib/followups'
import { admName, candidatePlace, counterView, groupOf, missingLabel } from '../lib/labels'
import { CounterChip, InputStatusBadge, LevelBadge, SelectedBadge, TypeChip } from './ui'

/** 후속 확인일 배지. 기준일이 지났으면 황토 "기한 지남" */
export function FollowUpBadge({ f, asOf }: { f: FollowUp | undefined; asOf: string }) {
  if (!f) return null
  const st = followState(f, asOf)
  return (
    <span className={`chip ${st === 'overdue' ? 'bg-caution text-white' : 'bg-brand-50 text-brand-700'}`} data-testid="follow-up-badge">
      {st === 'overdue' ? '후속 확인 기한 지남' : '후속 확인'} {f.follow_up_on.slice(5)}
    </span>
  )
}

export function CandidateCard({ c, query, followUp, asOf }: { c: Candidate; query: string; followUp?: FollowUp; asOf?: string }) {
  const cv = counterView(c)
  return (
    <Link
      to={`/candidates/${c.candidate_id}${query}`}
      className="card flex w-[280px] shrink-0 snap-start flex-col gap-3 p-5 transition hover:-translate-y-0.5 hover:shadow-lift sm:w-[300px]"
      data-testid="candidate-card"
    >
      <div className="flex items-center justify-between gap-2">
        <LevelBadge level={c.candidate_level} withName />
        <SelectedBadge c={c} />
      </div>
      <div>
        <div className="text-base font-bold leading-snug text-ink-900">{candidatePlace(c)}</div>
        <div className="text-ink-600">{groupOf(c) ?? c.service_eligibility_group}</div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <TypeChip type={c.candidate_type} />
        <InputStatusBadge status={c.input_status} />
        {c.candidate_type === 'unexpected_activity_deviation' && <CounterChip c={c} />}
      </div>
      <div className="text-xs text-ink-600">
        {c.trigger ? <>트리거: {c.trigger}</> : <>분석 코호트: {c.analysis_cohort}</>}
      </div>
      {c.is_shared_context && c.check_first_adm.length > 0 && (
        <div className="text-xs text-ink-600">
          먼저 확인할 동(공유 맥락): {c.check_first_adm.map(admName).join(', ')}
        </div>
      )}
      {(cv.tone === 'reject' || cv.tone === 'check' || cv.tone === 'review' || cv.tone === 'hold') && cv.reason && (
        <div className="rounded-xl bg-canvas px-3 py-2 text-xs text-ink-800">{cv.reason}</div>
      )}
      {c.missing_inputs.length > 0 && (
        <div className="break-words text-xs text-caution [overflow-wrap:anywhere]">빠진 근거: {c.missing_inputs.map(missingLabel).join(', ')}</div>
      )}
      {followUp && asOf && (
        <div>
          <FollowUpBadge f={followUp} asOf={asOf} />
        </div>
      )}
      <div className="mt-auto text-xs font-semibold text-brand-700" title={`후보 ID ${c.candidate_id}`} data-testid="candidate-card-more">
        자세히 보기 →
      </div>
    </Link>
  )
}

/** 가로로 넘기는 카드 줄 + 좌우 버튼 */
export function Carousel({ children, label }: { children: ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const move = (dir: number) => ref.current?.scrollBy({ left: dir * 320, behavior: 'smooth' })
  return (
    <div className="relative">
      <div className="mb-2 flex justify-end gap-2">
        <button className="h-9 w-9 rounded-full border border-line bg-surface text-ink-800 hover:bg-canvas" aria-label={`${label} 왼쪽으로`} onClick={() => move(-1)}>
          ‹
        </button>
        <button className="h-9 w-9 rounded-full border border-line bg-surface text-ink-800 hover:bg-canvas" aria-label={`${label} 오른쪽으로`} onClick={() => move(1)}>
          ›
        </button>
      </div>
      <div ref={ref} className="scroll-x -mx-1 flex snap-x gap-5 overflow-x-auto px-1 pb-4 pt-1" aria-label={label} role="region" tabIndex={0}>
        {children}
      </div>
    </div>
  )
}
