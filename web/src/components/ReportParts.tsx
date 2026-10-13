// 보고서의 담당자용 출력 (대응안 5칸·내일 아침 체크리스트·확인할 영역), 등급 설명, 판단 기록.
import type { ReactNode } from 'react'
import type { CandidateAction, LevelExplanation, RuleCheck, TraceStep } from '../api'
import { LIST_NOTE } from '../api'
import { NODE_LABEL, TRACE_SOURCE_LABEL } from '../lib/labels'
import { Term } from './Term'

/** 내일 아침 체크리스트: 꺼낼 명단 / 연락할 곳 / 방법 / 기한 / 확인할 영역 */
export function MorningChecklistView({ a }: { a: CandidateAction }) {
  const c = a.checklist
  if (!c) return null
  const rows: [string, ReactNode][] = [
    ['① 꺼낼 명단', <List items={c.lists} />],
    ['② 연락할 곳', <List items={c.contacts} />],
    ['③ 방법', c.method],
    ['④ 기한', <b>{c.due}</b>],
    ['⑤ 확인할 영역', c.check_areas.join(', ')],
  ]
  return (
    <div className="rounded-2xl border border-brand-100 bg-brand-50/50 p-4" data-testid="morning-checklist">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-bold text-ink-900">내일 아침 체크리스트{a.where ? ` · ${a.where}` : ''}</h3>
        {a.group && <span className="text-xs text-ink-600">{a.group}</span>}
      </div>
      <table className="mt-2 w-full text-sm">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} className="border-t border-brand-100 first:border-t-0">
              <th scope="row" className="w-28 py-2 pr-3 text-left align-top text-xs font-semibold text-ink-600">
                {k}
              </th>
              <td className="py-2 align-top text-ink-900">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 rounded-xl bg-surface px-3 py-2 text-xs font-semibold text-ink-800" data-testid="list-note">
        {c.note || LIST_NOTE}
      </div>
    </div>
  )
}

function List({ items }: { items: string[] }) {
  if (items.length === 0) return <span className="text-ink-600">—</span>
  return (
    <ul className="list-disc pl-4">
      {items.map((x) => (
        <li key={x}>{x}</li>
      ))}
    </ul>
  )
}

/** 대응안 5칸: 주체 / 방법 / 기한 / 확인할 영역 / 연계 자원 (+ 할 일, 위급 시 안내는 따로) */
export function ActionPlanView({ a }: { a: CandidateAction }) {
  const p = a.action_plan
  if (!p) return null
  const cells: [string, ReactNode][] = [
    ['주체', p.owner.join(', ')],
    ['방법', p.method],
    ['기한', p.due],
    ['확인할 영역', p.check_areas.join(', ')],
    ['연계 자원', p.linked_resources.length ? <List items={p.linked_resources} /> : (p.resource_note ?? '—')],
  ]
  return (
    <div data-testid="action-plan">
      <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-4">
        {cells.map(([k, v]) => (
          <div key={k} className={`rounded-xl border border-line bg-surface px-3 py-2 ${k === '연계 자원' ? 'sm:col-span-2 md:col-span-4' : ''}`}>
            <div className="text-xs font-semibold text-ink-600">{k}</div>
            <div className="mt-0.5 text-sm text-ink-900">{v}</div>
          </div>
        ))}
      </div>
      {p.tasks.length > 0 && (
        <div className="mt-2 text-sm">
          <span className="text-xs font-semibold text-ink-600">할 일 </span>
          {p.tasks.join(' · ')}
        </div>
      )}
      {p.emergency_contacts.length > 0 && (
        <div className="mt-2 rounded-xl bg-caution-soft px-3 py-2 text-sm text-ink-900" data-testid="emergency-contacts">
          <b className="text-caution">위급 시 안내</b> {p.emergency_contacts.join(' · ')}
          <span className="ml-1 text-xs text-ink-600">(추천 순위와 따로)</span>
        </div>
      )}
    </div>
  )
}

/** 확인할 영역과 고른 이유, 먼저 물어볼 것 */
export function CheckAreasView({ a }: { a: CandidateAction }) {
  if (a.check_areas.length === 0) return null
  return (
    <div>
      <div className="mb-1 text-xs font-semibold text-ink-600">
        <Term>확인할 영역</Term> <span className="font-normal">({a.check_area_note})</span>
      </div>
      <ul className="grid gap-2 md:grid-cols-3">
        {a.check_areas.map((c) => (
          <li key={c.area} className="rounded-xl bg-canvas px-3 py-2">
            <div className="font-semibold text-ink-900">{c.area}</div>
            {c.ask && <div className="text-sm text-ink-800">물어볼 것: {c.ask}</div>}
            {c.reasons.length > 0 && <div className="mt-0.5 text-xs text-ink-600">이유: {c.reasons.join(', ')}</div>}
          </li>
        ))}
      </ul>
    </div>
  )
}

type CheckRow = { condition: string; met: boolean | null; value?: unknown; threshold?: unknown; distance?: number | null; unit?: string | null }

function fmt(v: unknown, unit?: string | null): string {
  if (v === null || v === undefined || v === '') return '—'
  return `${v}${unit && typeof v === 'number' ? unit : ''}`
}

/** 등급 설명: 조건마다 충족 / 미충족 / 자료 없음 + 값과 기준. 보고서의 level_explanations, 없으면 후보의 rule_checks */
export function LevelExplain({ exp, checks }: { exp?: LevelExplanation | null; checks?: RuleCheck[] }) {
  const units = Object.fromEntries((checks ?? []).map((c) => [c.condition, c.unit ?? null]))
  const rows: CheckRow[] = exp ? exp.checks.map((c) => ({ ...c, unit: units[c.condition] })) : (checks ?? [])
  if (rows.length === 0) return null
  return (
    <div data-testid="level-explain">
      <div className="mb-1 flex flex-wrap items-baseline gap-2 text-xs font-semibold text-ink-600">
        <span>등급을 정한 조건</span>
        {exp?.rule_id && (
          <span className="font-normal">
            규칙 {exp.rule_id}
            {exp.rule_version ? ` (${exp.rule_version})` : ''}
          </span>
        )}
      </div>
      <ul className="flex flex-col gap-1">
        {rows.map((c) => {
          const mark = c.met === true ? '✓ 충족' : c.met === false ? '✕ 미충족' : '? 자료 없음'
          const tone = c.met === true ? 'bg-okay-soft text-okay' : c.met === false ? 'bg-canvas text-ink-800' : 'bg-holdstripe text-ink-800'
          const hasThreshold = c.threshold !== null && c.threshold !== undefined
          return (
            <li key={c.condition} className="flex flex-wrap items-center gap-2 text-sm" data-testid="rule-check">
              <span className={`chip ${tone}`}>{mark}</span>
              <span className="font-semibold text-ink-900">{c.condition}</span>
              <span className="text-ink-600">
                값 {fmt(c.value, c.unit)}
                {hasThreshold && <> · 기준 {fmt(c.threshold, c.unit)}</>}
                {typeof c.distance === 'number' && hasThreshold && (
                  <> · {c.met ? '여유' : '기준까지'} {Math.round(c.distance * 100) / 100}{c.unit ?? ''}</>
                )}
              </span>
            </li>
          )
        })}
      </ul>
      {exp && exp.what_if.length > 0 && (
        <div className="mt-2 rounded-xl bg-canvas px-3 py-2 text-xs text-ink-800">
          <b>무엇이 바뀌면 등급이 바뀌나</b> · {exp.what_if.join(' · ')}
        </div>
      )}
    </div>
  )
}

/** 판단 기록: 노드마다 무엇을 골랐고 누가 정했나 (규칙·LLM·검사·템플릿) */
export function DecisionTrace({ steps }: { steps: TraceStep[] }) {
  if (steps.length === 0) return <div className="text-xs text-ink-600">판단 기록이 없습니다.</div>
  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="w-full text-xs" data-testid="decision-trace">
        <thead className="bg-canvas">
          <tr>
            <th className="th">단계</th>
            <th className="th">고른 것</th>
            <th className="th">누가</th>
            <th className="th">규칙 · 이유</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((s, i) => (
            <tr key={`${s.node}-${i}`} className="border-t border-line">
              <td className="td whitespace-nowrap">
                {i + 1}. {NODE_LABEL[s.node] ?? s.node}
                {s.next && <span className="text-ink-600"> → {NODE_LABEL[s.next] ?? s.next}</span>}
              </td>
              <td className="td">{s.choice ?? '—'}</td>
              <td className="td whitespace-nowrap">{s.source ? (TRACE_SOURCE_LABEL[s.source] ?? s.source) : '—'}</td>
              <td className="td text-ink-600">{[s.rule_ids.join(', '), s.reason].filter(Boolean).join(' · ') || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
