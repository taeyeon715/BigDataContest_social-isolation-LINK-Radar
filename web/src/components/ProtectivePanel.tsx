// 보호 자원 칸: 먼저 볼 동 옆에 그 동에 이미 있는 연결 자원(경로당·무더위·한파 쉼터·돌봄단·통장·이장).
// 위험 표시가 아니다 (낙인 완화). 등급·순위에 쓰지 않는다. GET /resources/protective
import { useId } from 'react'
import { api, type City, type ProtectiveGroup } from '../api'
import { useAsync } from '../lib/useAsync'
import { Badge, ErrorBox, Loading } from './ui'

const STATUS_TONE: Record<ProtectiveGroup['status'], 'okay' | 'blue' | 'caution'> = {
  수집: 'okay',
  '도시 집계만': 'blue',
  미수집: 'caution',
}

function countText(g: ProtectiveGroup, hasDong: boolean): string {
  if (g.status === '미수집') return '자료 없음'
  if (hasDong && g.in_dong !== null) return `이 동 ${g.in_dong}곳` + (g.city_total !== null ? ` · 도시 ${g.city_total}곳` : '')
  if (g.city_total !== null) return `도시 ${g.city_total}곳 (동별 목록 없음)`
  return '도시 단위 안내'
}

/** 보호 자원 칸. compact 는 지도 옆 패널용 (항목을 2개까지만) */
export function ProtectivePanel({ city, admCd, compact = false }: { city: City; admCd: string | null; compact?: boolean }) {
  const titleId = useId()
  const p = useAsync((signal) => api.protective(city, admCd, { signal }), [city, admCd])
  const max = compact ? 2 : 5
  return (
    <section aria-labelledby={titleId} className="rounded-2xl border border-okay/30 bg-okay-soft/40 px-3 py-3" data-testid="protective-panel">
      <h3 id={titleId} className="text-sm font-bold text-ink-900">
        보호 자원 {p.data?.adm_nm ? `· ${p.data.adm_nm}` : ''}
      </h3>
      <p className="text-xs text-ink-600">이 동에 이미 있는 연결 자원입니다. 위험 표시가 아니며 등급·순위에 쓰지 않습니다.</p>
      {p.error && <ErrorBox error={p.error} onRetry={p.reload} compact />}
      {p.loading && !p.data && <Loading compact label="보호 자원 불러오는 중" />}
      {p.data && (
        <ul className="mt-2 flex flex-col gap-2">
          {p.data.groups.map((g) => (
            <li key={g.group} className="rounded-xl bg-surface px-3 py-2" data-testid="protective-group">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold text-ink-900">{g.label}</span>
                <span className="flex items-center gap-2 text-xs text-ink-800">
                  {countText(g, admCd !== null)}
                  <Badge tone={STATUS_TONE[g.status]}>{g.status}</Badge>
                </span>
              </div>
              {g.items.length > 0 && (
                <ul className="mt-1 list-disc pl-4 text-xs text-ink-800">
                  {g.items.slice(0, max).map((it) => (
                    <li key={it.resource_id}>
                      {it.source_url ? (
                        <a className="text-brand-700 hover:underline" href={it.source_url} target="_blank" rel="noreferrer">
                          {it.name}
                          <span className="sr-only"> (새 창, 출처)</span>
                        </a>
                      ) : (
                        it.name
                      )}
                      {it.scope === '도시' && <span className="text-ink-600"> · 도시 단위</span>}
                      {it.holiday_open === '예' && <span className="text-ink-600"> · 휴일 운영</span>}
                    </li>
                  ))}
                  {g.items.length > max && <li className="list-none text-ink-600">외 {g.items.length - max}곳</li>}
                </ul>
              )}
              {g.note && <div className="mt-1 text-xs text-ink-600">{g.note}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
