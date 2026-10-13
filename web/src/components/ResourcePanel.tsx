import type { Judgement, ResourceRecommendation, RunResource } from '../api'
import { Badge } from './ui'

export type JudgementDraft = Record<string, { judgement: Judgement | null; memo: string }>

const JUDGEMENTS: Judgement[] = ['적합', '보류', '부적합']
// 판정마다 색과 기호를 같이 써서 색만으로 구분하지 않음
const JUDGE_STYLE: Record<Judgement, { on: string; mark: string; border: string }> = {
  적합: { on: 'bg-okay text-white', mark: '✓', border: 'border-okay/60' },
  보류: { on: 'bg-holdstripe text-ink-900 ring-1 ring-hold', mark: '–', border: 'border-hold' },
  부적합: { on: 'bg-ink-800 text-white', mark: '✕', border: 'border-ink-600' },
}

/** 자원 상태 배지: 확인 필요 / 위치 확인 필요 / 권역 외 / 전화형 */
export function resourceFlags(r: ResourceRecommendation): { label: string; tone: 'caution' | 'gray' | 'blue' | 'okay' }[] {
  const out: { label: string; tone: 'caution' | 'gray' | 'blue' | 'okay' }[] = []
  if (r.phone_based) out.push({ label: '전화형', tone: 'blue' })
  if (r.eligibility === '확인 필요') out.push({ label: '자격 확인 필요', tone: 'caution' })
  if (r.eligibility === '부적합') out.push({ label: '자격 부적합', tone: 'gray' })
  if (r.operating_status === '확인 필요') out.push({ label: '운영 확인 필요', tone: 'caution' })
  if (r.operating_status === '종료') out.push({ label: '운영 종료', tone: 'gray' })
  if (r.warnings.some((w) => w.startsWith('위치 확인 필요')) || (!r.phone_based && r.distance_km === null && !r.adm_cd))
    out.push({ label: '위치 확인 필요', tone: 'caution' })
  if (r.warnings.some((w) => w.startsWith('권역 외'))) out.push({ label: '권역 외', tone: 'gray' })
  return out
}

/** 실행 자원이 이 실행의 몇 후보에 추천됐나 (GET /runs/{id}/resources 의 candidate_ids) */
function sharedCount(r: ResourceRecommendation | RunResource): number {
  return 'candidate_ids' in r ? (r.candidate_ids?.length ?? 0) : 0
}

export function ResourcePanel({
  items,
  draft,
  onChange,
  disabled,
}: {
  items: (ResourceRecommendation | RunResource)[]
  draft: JudgementDraft
  onChange: (id: string, v: { judgement: Judgement | null; memo: string }) => void
  disabled?: boolean
}) {
  if (items.length === 0) return <div className="text-ink-600">맞는 자원이 원장에 없습니다. 전화형 자원(외로움안녕120 등)을 먼저 안내하세요.</div>
  return (
    <ul className="flex flex-col gap-3">
      {items.map((r) => {
        const d = draft[r.resource_id] ?? { judgement: null, memo: '' }
        return (
          <li key={r.resource_id} className={`rounded-2xl border p-4 ${d.judgement ? `border-2 ${JUDGE_STYLE[d.judgement].border}` : 'border-line'}`} data-testid="resource-row">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="font-bold">{r.name}</div>
                <div className="text-xs text-ink-600">
                  {r.resource_type} · 대상 {r.target_group ?? '—'} ·{' '}
                  {r.phone_based ? '거리와 무관(전화)' : r.distance_km !== null ? `직선 ${r.distance_km}km` : '거리 모름'}
                </div>
              </div>
              <div className="flex flex-wrap gap-1">
                {resourceFlags(r).map((f) => (
                  <Badge key={f.label} tone={f.tone}>
                    {f.label}
                  </Badge>
                ))}
              </div>
            </div>
            {sharedCount(r) > 1 && (
              <div className="mt-1 text-xs text-ink-600" data-testid="resource-shared">
                이 실행 후보 {sharedCount(r)}곳에 함께 추천 (판정은 한 번)
              </div>
            )}
            {/* 긴 설명(전달 방식·확인 필요 항목·출처)은 접어 둠 */}
            <details className="mt-1 text-xs text-ink-600" data-testid="resource-details">
              <summary className="cursor-pointer select-none">자세히{r.warnings.length > 0 ? ` (확인할 점 ${r.warnings.length}개)` : ''}</summary>
              {r.warnings.length > 0 && (
                <ul className="mt-1 list-disc pl-5">
                  {r.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              )}
              <div className="mt-1">
                출처 확인일 {r.verified_date} · {r.resource_id}
              </div>
            </details>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <div className="flex rounded-pill border border-line p-0.5" role="radiogroup" aria-label={`${r.name} 판정`}>
                {JUDGEMENTS.map((j) => (
                  <button
                    key={j}
                    role="radio"
                    aria-checked={d.judgement === j}
                    disabled={disabled}
                    className={`rounded-pill px-3 py-1 text-xs font-semibold ${d.judgement === j ? JUDGE_STYLE[j].on : 'text-ink-600 hover:bg-canvas'}`}
                    onClick={() => onChange(r.resource_id, { ...d, judgement: j })}
                  >
                    {d.judgement === j && <span aria-hidden>{JUDGE_STYLE[j].mark} </span>}
                    {j}
                  </button>
                ))}
              </div>
              <input
                className="field min-w-[200px] flex-1 py-1.5"
                placeholder="메모 (예: 연휴 운영 여부 확인 필요)"
                value={d.memo}
                disabled={disabled}
                onChange={(e) => onChange(r.resource_id, { ...d, memo: e.target.value })}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}
