import { OUTCOME_FIELDS, OUTCOME_LABEL, api, type City, type OutcomeField, type OutcomeSummaryRow } from '../api'
import { useAsync } from '../lib/useAsync'
import { Card, Empty, ErrorBox, Loading } from './ui'

/** 모아 보기 칸: 서버가 숨긴(10 미만) 칸은 숫자 없이 "10 미만" */
export function cellText(row: OutcomeSummaryRow, f: OutcomeField): string {
  const v = row[f]
  return v === null || row.small_cells.includes(f) ? '10 미만' : v.toLocaleString('ko-KR')
}

function pct(v: number | null): string {
  return v === null ? '—' : `${Math.round(v * 100)}%`
}

const TRIGGER_KO: Record<string, string> = {
  R1: '연휴(R1)',
  R2: '연휴(R2)',
  R3: '연휴(R3)',
  R4: '폭염(R4)',
  R6: '기상특보(R6)',
  W: '동절기(W)',
  DEV: '예상 밖 이탈',
}

/** 현황: 현장 확인 결과 모아 보기 (트리거별). 승인된 조사 실행에 적은 건수만, 10 미만 칸은 숫자 없이 */
export function OutcomeSummaryCard({ city }: { city: City }) {
  const s = useAsync((signal) => api.outcomeSummary({ city, by: 'trigger' }, { signal }), [city])
  const rows = s.data?.rows ?? []
  return (
    <Card title="현장 확인 결과" right={<span className="text-xs text-ink-600">승인된 조사에 적은 건수 · 트리거별</span>}>
      <div data-testid="outcome-summary">
        {!s.data ? (
          s.error ? <ErrorBox error={s.error} onRetry={s.reload} /> : <Loading onRetry={s.reload} />
        ) : rows.length === 0 ? (
          <Empty>아직 적은 현장 확인 결과가 없습니다. 승인한 조사의 완료 카드에서 적을 수 있습니다.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-ink-600">
                  <th className="py-1 pr-2 font-semibold">트리거</th>
                  <th className="py-1 pr-2 font-semibold">동·집단</th>
                  {OUTCOME_FIELDS.map((f) => (
                    <th key={f} className="py-1 pr-2 font-semibold">
                      {OUTCOME_LABEL[f]}
                    </th>
                  ))}
                  <th className="py-1 pr-2 font-semibold">연락 비율</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((r) => (
                  <tr key={`${r.trigger_code}-${r.event_id ?? ''}`} data-testid="outcome-row">
                    <td className="py-1.5 pr-2 font-semibold">{TRIGGER_KO[r.trigger_code] ?? r.trigger_code}</td>
                    <td className="py-1.5 pr-2">{r.places}곳</td>
                    {OUTCOME_FIELDS.map((f) => (
                      <td key={f} className={`py-1.5 pr-2 ${r.small_cells.includes(f) ? 'text-ink-600' : ''}`}>
                        {cellText(r, f)}
                      </td>
                    ))}
                    <td className="py-1.5 pr-2">{pct(r.contact_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-ink-600">{s.data?.note}</p>
          </div>
        )}
      </div>
    </Card>
  )
}
