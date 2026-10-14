import { Fragment, useState, type ReactNode } from 'react'
import type { EvidenceCard, ToolResult } from '../api'
import { HUB_NOTE, INTERP_LABEL, MODE_LABEL, fmtValue, mentionsHub, plainText, replaceInputCodes, splitRefs } from '../lib/labels'
import { Term } from './Term'
import { Badge } from './ui'

const VALUE_STATE: Record<string, string> = {
  observed: '관측',
  zero: '원본 0',
  suppressed: '비공개',
  missing: '자료 없음',
  quality_suspect: '품질 의심',
}

const INTERP_TONE = { '직접 확인': 'okay', '계산 재현': 'blue', 가설: 'caution', 미확인: 'gray' } as const

/** 근거 한 줄 해석: "논현1동 1인세대 비율 0.61" + 할 수 있는 말 첫 줄 (순위 같은 비교값) */
export function evidenceLine(e: ToolResult): { head: string; sub: string | null } {
  const place = e.applied_scope ? e.applied_scope.replace(/\s*\(.*\)$/, '').replace(/\s*탐색적$/, '') : ''
  const scope = place && !e.metric.includes(place) ? `${place} ` : ''
  const value = e.status === 'ERROR' || e.value === null ? '자료 없음' : fmtValue(e.value, e.unit)
  const dir = e.direction && e.direction !== '변화 없음' ? ` (${e.direction})` : ''
  return { head: plainText(`${scope}${e.metric} ${value}${dir}`), sub: e.valid_claims[0] ? plainText(e.valid_claims[0]) : null }
}

/** 기술 정보 (분모·측정 단위·기간·방식·근거 ID). "자세히"를 눌러야 보임 */
function Details({ e }: { e: ToolResult }) {
  const rows: [string, ReactNode][] = [
    ['근거 ID', <span className="font-mono">{e.evidence_id}</span>],
    ['분모', e.denominator ?? '—'],
    ['단위', `${e.unit ?? '—'}${e.n_obs ? ` · 관측 ${e.n_obs}개` : ''}`],
    ['적용 범위', e.applied_scope ?? '—'],
    ['측정 단위', e.measured_grain ?? '—'],
    ['관측 기간', e.observed_from ? `${e.observed_from} ~ ${e.observed_to}` : '—'],
    [
      '기준일 · 방식',
      <>
        {e.as_of} · <Term name="과거 재생">{MODE_LABEL[e.analysis_mode]}</Term>
      </>,
    ],
  ]
  if (e.merchant_region) rows.push(['결제 자료 범위', `가맹점 ${e.merchant_region} · ${e.customer_residence} 거주 고객`])
  if (e.availability_basis === 'assumed' && e.simulated_available_at)
    rows.push(['가정한 공개 시점', <Term name="가정한 공개 시점">{e.simulated_available_at}</Term>])
  if (e.is_shared_context) rows.push(['공유 맥락', <Term name="공유 맥락">도시 신호를 동에 같이 보임</Term>])
  if (e.error_code) rows.push(['자료 없음 사유', e.error_code])
  if (e.missing_inputs.length) rows.push(['빠진 근거', replaceInputCodes(e.missing_inputs.join(', '))])
  return (
    <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2" data-testid="evidence-details">
      {rows.map(([k, v]) => (
        <div key={k} className="flex gap-2">
          <dt className="w-24 shrink-0 text-ink-600">{k}</dt>
          <dd className="min-w-0 break-words text-ink-800">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

/** 근거 카드 표. 행마다 한 줄 해석을 먼저 굵게, 기술 열은 "자세히"로. checked/onToggle 을 주면 "확인함" 체크 */
export function EvidenceTable({
  card,
  checked,
  onToggle,
  highlight,
}: {
  card: EvidenceCard
  checked?: Set<string>
  onToggle?: (id: string) => void
  highlight?: string | null
}) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  const check = !!onToggle
  // 좁은 화면(sm 미만)에서는 표 대신 행마다 카드처럼 쌓음 (FE-07)
  const cell2 = check ? 'max-sm:col-start-2' : ''
  const flip = (id: string) =>
    setOpen((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  return (
    <div className="rounded-2xl border border-line">
      <table className="w-full table-fixed border-collapse text-sm max-sm:block" data-testid="evidence-table">
        <thead className="bg-canvas max-sm:hidden">
          <tr>
            {onToggle && <th className="th w-14 rounded-tl-2xl">확인</th>}
            <th className={`th ${onToggle ? '' : 'rounded-tl-2xl'}`}>근거와 뜻</th>
            <th className="th w-32">
              <Term>해석 상태</Term>
            </th>
            <th className="th w-20 rounded-tr-2xl">
              <span className="sr-only">자세히</span>
            </th>
          </tr>
        </thead>
        <tbody className="max-sm:block">
          {card.evidence.map((e) => {
            const line = evidenceLine(e)
            const isOpen = open.has(e.evidence_id) || highlight === e.evidence_id
            return (
              <Fragment key={e.evidence_id}>
                <tr
                  id={e.evidence_id}
                  className={`border-t border-line first:border-t-0 max-sm:grid max-sm:py-1 ${check ? 'max-sm:grid-cols-[36px_minmax(0,1fr)]' : 'max-sm:grid-cols-1'} ${highlight === e.evidence_id ? 'bg-brand-50' : ''}`}
                >
                  {onToggle && (
                    <td className="td max-sm:row-span-3 max-sm:pr-0">
                      <input
                        type="checkbox"
                        className="h-4 w-4"
                        aria-label={`${line.head} 확인함`}
                        checked={checked?.has(e.evidence_id) ?? false}
                        onChange={() => onToggle(e.evidence_id)}
                      />
                    </td>
                  )}
                  <td className="td">
                    <div className="font-bold text-ink-900" data-testid="evidence-line">
                      {line.head}
                    </div>
                    {line.sub && <div className="mt-0.5 text-ink-800">{line.sub}</div>}
                    {mentionsHub(e.metric) && (
                      <div className="mt-0.5 text-xs text-ink-600" data-testid="hub-note-evidence">
                        {HUB_NOTE}
                      </div>
                    )}
                    {((e.value_state && e.value_state !== 'observed') || e.is_shared_context) && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {e.value_state && e.value_state !== 'observed' && <Badge tone="caution">{VALUE_STATE[e.value_state]}</Badge>}
                        {e.is_shared_context && (
                          <Badge tone="blue">
                            <Term>공유 맥락</Term>
                          </Badge>
                        )}
                      </div>
                    )}
                  </td>
                  <td className={`td max-sm:py-0 ${cell2}`}>
                    <Badge tone={INTERP_TONE[e.interpretation_status]}>{INTERP_LABEL[e.interpretation_status] ?? e.interpretation_status}</Badge>
                  </td>
                  <td className={`td text-right max-sm:py-1 max-sm:text-left ${cell2}`}>
                    <button
                      type="button"
                      className="whitespace-nowrap rounded-pill px-2 py-1 text-xs font-semibold text-brand-700 hover:bg-brand-50"
                      aria-expanded={isOpen}
                      aria-controls={`${e.evidence_id}-more`}
                      aria-label={`${plainText(e.metric)} ${isOpen ? '접기' : '자세히'}`}
                      onClick={() => flip(e.evidence_id)}
                    >
                      {isOpen ? '접기' : '자세히'}
                    </button>
                  </td>
                </tr>
                {isOpen && (
                  <tr id={`${e.evidence_id}-more`} className="bg-canvas/60 max-sm:block">
                    <td className="td max-sm:block" colSpan={onToggle ? 4 : 3}>
                      <Details e={e} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** 할 수 있는 말 / 하면 안 되는 말 */
export function ClaimsBox({ card, fold = true }: { card: EvidenceCard; fold?: boolean }) {
  const valid = Array.from(new Set(card.evidence.flatMap((e) => e.valid_claims)))
  const forbidden = Array.from(new Set(card.evidence.flatMap((e) => e.forbidden_claims)))
  return (
    <div className="grid gap-3 md:grid-cols-2" data-testid="claims">
      <div className="rounded-2xl bg-okay-soft/60 px-4 py-3">
        <div className="text-xs font-semibold text-okay">할 수 있는 말</div>
        <ul className="mt-1 list-disc pl-5">
          {valid.map((v, i) => (
            <li key={i}>{plainText(v)}</li>
          ))}
        </ul>
      </div>
      {fold ? (
        <details className="rounded-2xl bg-canvas px-4 py-3" data-testid="forbidden-claims">
          <summary className="cursor-pointer select-none text-xs font-semibold text-ink-600">하면 안 되는 말 {forbidden.length}개 (펼쳐 보기)</summary>
          <ul className="mt-1 list-disc pl-5 text-ink-600">
            {forbidden.map((v) => (
              <li key={v}>{plainText(v)}</li>
            ))}
          </ul>
        </details>
      ) : (
        <div className="rounded-2xl bg-canvas px-4 py-3">
          <div className="text-xs font-semibold text-ink-600">하면 안 되는 말</div>
          <ul className="mt-1 list-disc pl-5 text-ink-600">
            {forbidden.map((v) => (
              <li key={v}>{plainText(v)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/** 답 문장 안의 [EV-…] 를 근거 표 행으로 가는 버튼으로 */
export function withRefs(text: string, onRef: (id: string) => void, refName?: (id: string) => string | undefined): ReactNode[] {
  // 이름이 있으면 "근거 [EV-…]" 앞의 "근거"는 splitRefs 가 뺌 (버튼이 이미 "근거 n")
  const parts = refName ? splitRefs(text) : text.split(/(\[EV-[0-9A-Za-z-]+\])/g).map((p) => (/^\[EV-[0-9A-Za-z-]+\]$/.test(p) ? { ref: p.slice(1, -1) } : { text: p }))
  return parts.map((p, i) => {
    if ('text' in p) return <span key={i}>{p.text}</span>
    const id = p.ref
    return (
      <button key={i} title={id} className={`mx-0.5 rounded bg-brand-50 px-1 text-xs text-brand-700 hover:bg-brand-100 ${refName?.(id) ? '' : 'font-mono'}`} onClick={() => onRef(id)}>
        {refName?.(id) ?? id}
      </button>
    )
  })
}
