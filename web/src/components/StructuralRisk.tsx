import type { StructuralRiskReference } from '../api'

export function StructuralRisk({ items, candidateId }: { items?: StructuralRiskReference[]; candidateId?: string }) {
  const refs = (items ?? []).filter((r) => !candidateId || r.candidate_id === candidateId)
  if (!refs.length) return null
  return <section className="rounded-lg border border-line bg-paper p-4 text-sm" aria-label="구조 위험 단계(참고)">
    <h3 className="font-bold">구조 위험 단계(참고)</h3>
    <p className="mt-1 font-semibold text-ink-700">2025.12 기준 사후 계산 · 대응 등급과 순서에 사용하지 않음</p>
    {refs.map((r) => <div key={r.candidate_id} className="mt-3 space-y-2">
      {refs.length > 1 && <p className="font-semibold">{r.city} {r.dong ?? '도시 단위'} · {r.policy_group}</p>}
      {r.status === 'available' && <table className="w-full text-left text-xs sm:text-sm">
        <thead><tr className="border-b border-line"><th className="py-1">성·연령</th><th>참고 단계</th><th>유형</th><th>행동 H</th><th>취약 V</th></tr></thead>
        <tbody>{r.cells.map((c) => <tr key={`${c.sex}-${c.age_band}`} className="border-b border-line">
          <td className="py-2">{c.sex} {c.age_band}</td><td className="font-semibold">{c.stage}</td><td>{c.risk_type}</td><td>{c.behavior.toFixed(2)}</td><td>{c.vulnerability.toFixed(2)}</td>
        </tr>)}</tbody>
      </table>}
      {r.mapping_note && <p className="text-xs text-ink-600">{r.mapping_note}</p>}
      <p className="text-xs text-ink-600">{r.note}</p>
      {r.status === 'available' && <p className="text-xs text-ink-600">{r.comparison_note}</p>}
    </div>)}
  </section>
}
