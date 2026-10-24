import type { StructuralRiskReference } from '../api'

// 후보 동 × 집단의 성·연령 칸 근거 축 (rules-v2.0, 기준일 계산). 연휴 주의·동절기 판정과 같은 값.
const AXIS: Record<string, string> = { S: '혼자 사는 구조', H: '행동 고립', V: '경제 취약', A: '거점 원거리' }
const num = (x: number | null, d = 2) => (x == null ? '—' : x.toFixed(d))

export function StructuralRisk({ items, candidateId }: { items?: StructuralRiskReference[]; candidateId?: string }) {
  const refs = (items ?? []).filter((r) => !candidateId || r.candidate_id === candidateId)
  if (!refs.length) return null
  return <section className="rounded-lg border border-line bg-paper p-4 text-sm" aria-label="칸 근거 축">
    <h3 className="font-bold">칸 근거 축 (성·연령별)</h3>
    {refs.map((r) => <div key={r.candidate_id} className="mt-3 space-y-2">
      {refs.length > 1 && <p className="font-semibold">{r.city} {r.dong ?? '도시 단위'} · {r.policy_group}</p>}
      {r.status === 'available' && <>
        <p className="font-semibold text-ink-700">{r.period_label}</p>
        <table className="w-full text-left text-xs sm:text-sm">
          <thead><tr className="border-b border-line"><th className="py-1">성·연령</th><th>선 축</th><th>유형</th><th>1인 비율 S</th><th>행동 H</th><th>경제 V</th><th>거점 A</th></tr></thead>
          <tbody>{r.cells.map((c) => <tr key={`${c.sex}-${c.age_band}`} className="border-b border-line">
            <td className="py-2">{c.sex} {c.age_band}{c.small ? ' (작은 칸)' : ''}</td>
            <td className="font-semibold">{c.axes_on.length ? c.axes_on.map((a) => AXIS[a]).join(' + ') : '없음'}</td>
            <td>{c.cell_type}</td><td>{num(c.s, 3)}</td><td>{num(c.h)}</td><td>{num(c.v, 3)}</td><td>{c.a_km == null ? '—' : `${c.a_km.toFixed(1)}km`}</td>
          </tr>)}</tbody>
        </table>
      </>}
      {r.mapping_note && <p className="text-xs text-ink-600">{r.mapping_note}</p>}
      <p className="text-xs text-ink-600">{r.note}</p>
      {r.status === 'available' && <p className="text-xs text-ink-600">{r.comparison_note}</p>}
    </div>)}
  </section>
}
