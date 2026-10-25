import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CircleMarker, MapContainer, Marker, TileLayer, Tooltip as LTooltip } from 'react-leaflet'
import { CartesianGrid, Legend, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts'
import { api, ApiError, type Candidate, type City, type MapArea, type MapResource } from '../api'
import { PageBody, PageHero } from '../components/Layout'
import { ProtectivePanel } from '../components/ProtectivePanel'
import { Badge, Card, ErrorBox, Loading } from '../components/ui'
import { CENTROIDS, CITY_CENTER } from '../map/centroids'
import { useFilters } from '../lib/filters'
import { HUB_LABEL, HUB_NOTE, HUB_SHORT, candidatePlace, groupOf, isAdminOffice, isHubResource } from '../lib/labels'
import { useAsync } from '../lib/useAsync'

// 구조 취약성 5단계 (한 색상 밝음 → 진함). 행동 신호는 테두리로 따로 표시.
const STEPS = ['#DCE6FC', '#B1C6F5', '#7FA0EC', '#4F78DC', '#2449AE']
const SIGNAL = {
  '예정된 점검': { color: '#008F8A', dash: undefined as string | undefined, weight: 3.5 },
  '이탈 경보(공유 맥락)': { color: '#7A5BC7', dash: '4 4', weight: 3 },
  없음: { color: '#FFFFFF', dash: undefined as string | undefined, weight: 1 },
} as const

function stepOf(v: number | null, sorted: number[]): number {
  if (v === null || sorted.length === 0) return -1
  const idx = sorted.findIndex((x) => x >= v)
  return Math.min(4, Math.floor((idx / sorted.length) * 5))
}

const resourceIcon = L.divIcon({
  className: '',
  html: '<div style="width:10px;height:10px;background:#14171F;border:2px solid #fff;border-radius:2px;box-shadow:0 0 0 1px #14171F"></div>',
  iconSize: [10, 10],
  iconAnchor: [5, 5],
})

function Legend2D() {
  return (
    <div className="flex flex-col gap-3 text-xs" data-testid="map-legend">
      <div className="rounded-xl bg-canvas px-3 py-2 font-semibold text-ink-900">
        위험지도가 아닙니다. 두 축을 합친 점수는 없습니다.
      </div>
      <div>
        <div className="mb-1 font-semibold text-ink-600">채우기 = 동 전체 1인세대 비율 (주민등록, 도시 안 5단계 · 등급 근거 아님)</div>
        <div className="flex items-center gap-1">
          <span className="text-ink-600">낮음</span>
          {STEPS.map((c) => (
            <span key={c} className="inline-block h-3 w-6 rounded-sm" style={{ background: c }} />
          ))}
          <span className="text-ink-600">높음</span>
          <span className="ml-2 inline-block h-3 w-6 rounded-sm border border-dashed border-ink-400 bg-canvas" />
          <span className="text-ink-600">값 없음·품질 의심</span>
        </div>
      </div>
      <div>
        <div className="mb-1 font-semibold text-ink-600">테두리 = 행동 신호</div>
        <div className="flex flex-wrap gap-3">
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-full border-[3px] border-scheduled" /> 예정된 점검
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-full border-2 border-dashed border-deviation" /> 이탈 경보(공유 맥락)
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-full border border-line bg-lv0" /> 없음
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2.5 w-2.5 rounded-sm bg-ink-900" /> 복지 자원
          </span>
        </div>
      </div>
      <div className="text-ink-600">
        공유 맥락: 카드 신호는 도시 단위입니다. 동에 같이 보인 것은 "먼저 확인할 동" 제안이며, 그 동의 결제가 줄었다는 뜻이 아닙니다.
      </div>
      <div className="text-ink-600">행정동 중심점에 원으로 표시합니다. 경계선은 나중에 붙입니다.</div>
    </div>
  )
}

function km(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const r = Math.PI / 180
  const x = (b.lon - a.lon) * r * Math.cos(((a.lat + b.lat) / 2) * r)
  const y = (b.lat - a.lat) * r
  return Math.sqrt(x * x + y * y) * 6371
}

/** 고른 동의 작은 패널: 구조 값·순위·신호·이 동 후보·가까운 자원 */
export function DongPanel({
  area,
  total,
  candidates,
  resources,
  query,
  onClose,
}: {
  area: MapArea
  total: number
  candidates: Candidate[]
  resources: MapResource[]
  query: string
  onClose: () => void
}) {
  const c = CENTROIDS[area.adm_cd]
  const near = c
    ? resources
        .filter((r) => r.lat !== null && r.lon !== null)
        .map((r) => ({ r, d: km(c, { lat: r.lat as number, lon: r.lon as number }) }))
        .sort((a, b) => a.d - b.d)
        .slice(0, 3)
    : []
  const mine = candidates.filter((x) => x.adm_cd === area.adm_cd || x.check_first_adm.includes(area.adm_cd))
  const city: City = area.adm_cd.startsWith('32010') ? '춘천' : '강남'
  return (
    <div className="flex flex-col gap-3" data-testid="dong-panel">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-lg font-bold text-ink-900">{area.adm_nm}</div>
          <div className="text-sm text-ink-600">
            1인세대 비율 {area.structure_value !== null ? `${area.structure_value.toFixed(2)} (${total}개 동 중 ${area.structure_rank}위)` : area.value_state === 'quality_suspect' ? '품질 의심' : '값 없음'}
          </div>
        </div>
        <button className="btn-ghost px-3 py-1 text-xs" onClick={onClose}>
          닫기
        </button>
      </div>
      <div className="text-sm">
        행동 신호: <b>{area.behavior_signal}</b>
      </div>
      {area.nearest_hub_km !== null && (
        <div className="text-sm" data-testid="dong-hub">
          {HUB_LABEL}: <b>직선 {area.nearest_hub_km}km</b>
          <div className="text-xs text-ink-600">{HUB_NOTE}</div>
        </div>
      )}
      <div>
        <div className="mb-1 text-xs font-semibold text-ink-600">이 동의 후보</div>
        {mine.length === 0 ? (
          <div className="text-sm text-ink-600">이 기준일에는 없습니다.</div>
        ) : (
          <ul className="flex flex-col gap-1">
            {mine.map((x) => (
              <li key={x.candidate_id}>
                <Link className="font-semibold text-brand-700 hover:underline" to={`/candidates/${x.candidate_id}${query}`}>
                  {candidatePlace(x)} · {groupOf(x) ?? x.analysis_cohort} · {x.candidate_level ?? '등급 전'}
                </Link>
                {x.adm_cd !== area.adm_cd && <span className="ml-1 text-xs text-ink-600">(먼저 확인할 동으로 포함, 공유 맥락)</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
      <ProtectivePanel city={city} admCd={area.adm_cd} compact />
      {near.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold text-ink-600">가까운 자원 (모든 종류 · 동 중심점에서 직선)</div>
          <ul className="text-sm">
            {near.map(({ r, d }) => (
              <li key={r.resource_id}>
                {r.name} · {r.resource_type} · {d.toFixed(1)}km
                {isAdminOffice(r) ? (
                  <span className="ml-1 text-xs text-ink-600" data-testid="near-admin-note">
                    (행정 창구 · 위 거점 거리에 넣지 않음)
                  </span>
                ) : isHubResource(r) ? (
                  <span className="ml-1 text-xs text-ink-600">(안부·관계 거점)</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function MapPage() {
  const { city, as_of, query } = useFilters()
  const [params] = useSearchParams()
  const m = useAsync((signal) => api.map(as_of, city, { signal }), [as_of, city])
  const cands = useAsync((signal) => api.candidates(as_of, city, undefined, undefined, { signal }), [as_of, city])
  const [sel, setSel] = useState<string | null>(params.get('sel'))
  const [tileFailed, setTileFailed] = useState(false)
  const selArea = m.data?.areas.find((a) => a.adm_cd === sel) ?? null
  const sorted = useMemo(
    () => (m.data ? m.data.areas.map((a) => a.structure_value).filter((v): v is number => v !== null).sort((a, b) => a - b) : []),
    [m.data],
  )
  const areas = useMemo(
    () => (m.data ? [...m.data.areas].sort((a, b) => (a.structure_rank ?? 999) - (b.structure_rank ?? 999)) : []),
    [m.data],
  )
  const phone = m.data?.resources.filter((r) => r.phone_based || r.lat === null) ?? []
  const scatter = (sig: MapArea['behavior_signal']) =>
    areas.filter((a) => a.behavior_signal === sig && a.structure_value !== null).map((a) => ({ x: a.structure_value, y: a.nearest_hub_km, name: a.adm_nm }))

  return (
    <>
      <PageHero title="지도 · 구조 취약성 × 행동 신호" sources={['map', 'candidates']} />
      <PageBody>
        {m.error && <ErrorBox error={m.error} onRetry={m.reload} />}
        {cands.error && (
          <ErrorBox error={new ApiError(cands.error.status, cands.error.code, `동별 후보를 불러오지 못했습니다. ${cands.error.message}`)} onRetry={cands.reload} />
        )}
        <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          {/* 왼쪽: 지도 + 행정동 표 (오른쪽 패널이 길어도 지도 아래가 비지 않게) */}
          <div className="flex min-w-0 flex-col gap-5">
          <Card title={`${city} 행정동 (${m.data?.areas.length ?? '…'}개)`}>
            {m.loading && !m.data ? (
              <Loading onRetry={m.reload} />
            ) : m.data ? (
              <div className="relative h-[460px] overflow-hidden rounded-2xl bg-canvas" data-testid="map">
                {tileFailed && (
                  <div className="absolute left-3 right-3 top-3 z-[1000] rounded-xl bg-surface/95 px-3 py-2 text-xs text-ink-800 shadow-card" role="status">
                    바탕 지도를 불러오지 못했습니다 (인터넷 연결 확인). 동 위치 원과 아래 표는 그대로 볼 수 있습니다.
                  </div>
                )}
                <MapContainer key={city} center={CITY_CENTER[city]} zoom={city === '강남' ? 13 : 10} scrollWheelZoom={false} className="h-full w-full">
                  <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" opacity={0.55} eventHandlers={{ tileerror: () => setTileFailed(true) }} />
                  {m.data.areas.map((a) => {
                    const c = CENTROIDS[a.adm_cd]
                    if (!c) return null
                    const st = stepOf(a.structure_value, sorted)
                    const sig = SIGNAL[a.behavior_signal]
                    return (
                      <CircleMarker
                        key={a.adm_cd}
                        center={[c.lat, c.lon]}
                        radius={sel === a.adm_cd ? 15 : 11}
                        pathOptions={{
                          fillColor: st < 0 ? '#F2F4F8' : STEPS[st],
                          fillOpacity: 0.95,
                          color: st < 0 && a.behavior_signal === '없음' ? '#8A91A3' : sig.color,
                          weight: sig.weight,
                          dashArray: st < 0 && a.behavior_signal === '없음' ? '3 3' : sig.dash,
                        }}
                        eventHandlers={{ click: () => setSel(a.adm_cd) }}
                      >
                        <LTooltip>
                          <b>{a.adm_nm}</b>
                          <br />
                          1인세대 비율 {a.structure_value ?? '값 없음'} {a.structure_rank ? `(${a.structure_rank}위)` : ''}
                          <br />
                          행동 신호: {a.behavior_signal}
                          {a.nearest_hub_km !== null && (
                            <>
                              <br />
                              {HUB_SHORT} 직선 {a.nearest_hub_km}km
                            </>
                          )}
                        </LTooltip>
                      </CircleMarker>
                    )
                  })}
                  {m.data.resources
                    .filter((r) => r.lat !== null && r.lon !== null)
                    .map((r) => (
                      <Marker key={r.resource_id} position={[r.lat as number, r.lon as number]} icon={resourceIcon} title={`${r.name} · ${r.resource_type}`} alt={`${r.name} · ${r.resource_type}`}>
                        <LTooltip>
                          {r.name} · {r.resource_type}
                        </LTooltip>
                      </Marker>
                    ))}
                </MapContainer>
              </div>
            ) : null}
            {phone.length > 0 && (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
                <span className="text-ink-600">지도에 없는 자원:</span>
                {phone.map((r) => (
                  <Badge key={r.resource_id} tone={r.phone_based ? 'blue' : 'caution'}>
                    {r.name} · {r.phone_based ? '전화형' : '위치 확인 필요'}
                  </Badge>
                ))}
              </div>
            )}
          </Card>
          <Card title="행정동 표">
            <div className="scroll-x max-h-[640px] overflow-auto rounded-2xl border border-line">
              <table className="w-full min-w-[520px] text-sm">
                <thead className="sticky top-0 bg-canvas">
                  <tr>
                    <th className="th">동</th>
                    <th className="th">1인세대 비율 (순위)</th>
                    <th className="th">행동 신호</th>
                    <th className="th">안부·관계 거점까지</th>
                  </tr>
                </thead>
                <tbody>
                  {areas.map((a) => (
                    <tr key={a.adm_cd} className={`border-t border-line ${sel === a.adm_cd ? 'bg-brand-50' : ''}`}>
                      <td className="td">
                        <button className="font-semibold text-ink-900 hover:text-brand-700 hover:underline" aria-pressed={sel === a.adm_cd} onClick={() => setSel(a.adm_cd)}>
                          {a.adm_nm}
                        </button>
                      </td>
                      <td className="td">
                        {a.structure_value !== null ? `${a.structure_value.toFixed(2)} (${a.structure_rank}위)` : <Badge tone="caution">{a.value_state === 'quality_suspect' ? '품질 의심' : '값 없음'}</Badge>}
                      </td>
                      <td className="td">
                        {a.behavior_signal === '없음' ? <span className="text-ink-600">없음</span> : <Badge tone={a.behavior_signal === '예정된 점검' ? 'okay' : 'blue'}>{a.behavior_signal}</Badge>}
                      </td>
                      <td className="td">{a.nearest_hub_km !== null ? `직선 ${a.nearest_hub_km}km` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {m.data && <div className="mt-2 text-xs text-ink-600">{m.data.note} · 구조 지표: {m.data.structure_metric}</div>}
          </Card>
          </div>
          <div className="flex min-w-0 flex-col gap-5">
          {selArea && m.data && (
            <Card title="선택한 동">
              <DongPanel area={selArea} total={m.data.areas.filter((a) => a.structure_rank !== null).length} candidates={cands.data?.items ?? []} resources={m.data.resources} query={query} onClose={() => setSel(null)} />
            </Card>
          )}
          <Card title="읽는 법">
            <Legend2D />
            {city === '춘천' && (
              <div className="mt-4 rounded-xl bg-brand-50 px-3 py-2 text-xs text-brand-700">
                춘천 읍·면 거리는 행정동 중심점에서 <b>안부·관계 거점</b>까지의 <b>직선거리</b>입니다. 행정복지센터(주민센터)는
                안부·관계 프로그램을 운영하지 않아 넣지 않습니다. 실제 이동 시간이나 서비스 공급 부족이 아닙니다.
              </div>
            )}
          </Card>
          </div>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
          <Card title="구조 취약성 × 안부·관계 거점까지 직선거리">
            <div className="h-72" role="img" aria-label="행정동별 1인세대 비율(가로)과 안부·관계 거점까지 직선거리(세로) 산점도. 같은 값은 행정동 표에 있습니다.">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 0 }}>
                  <CartesianGrid stroke="#E3E7EF" />
                  <XAxis type="number" dataKey="x" name="1인세대 비율" domain={['auto', 'auto']} tick={{ fontSize: 12, fill: '#4A5163' }}
                    label={{ value: '1인세대 비율 (구조 취약성)', position: 'insideBottom', offset: -12, fontSize: 12, fill: '#4A5163' }} />
                  <YAxis type="number" dataKey="y" name="직선거리" unit="km" tick={{ fontSize: 12, fill: '#4A5163' }} width={56} />
                  <ZAxis range={[90, 90]} />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }}
                    formatter={(v, n) => [n === '직선거리' ? `${v}km` : v, n]}
                    labelFormatter={() => ''}
                    content={({ payload }) => {
                      const p = payload?.[0]?.payload as { name: string; x: number; y: number } | undefined
                      return p ? (
                        <div className="rounded-lg border border-line bg-white px-3 py-2 text-xs shadow-card">
                          <b>{p.name}</b>
                          <div>1인세대 비율 {p.x}</div>
                          <div>{HUB_SHORT} {p.y}km</div>
                        </div>
                      ) : null
                    }} />
                  <Legend verticalAlign="top" height={28} iconSize={10} wrapperStyle={{ fontSize: 12 }} />
                  <Scatter name="예정된 점검" data={scatter('예정된 점검')} fill="#008F8A" stroke="#fff" strokeWidth={2} isAnimationActive={false} />
                  <Scatter name="이탈 경보(공유 맥락)" data={scatter('이탈 경보(공유 맥락)')} fill="#7A5BC7" stroke="#fff" strokeWidth={2} shape="diamond" isAnimationActive={false} />
                  <Scatter name="신호 없음" data={scatter('없음')} fill="#8A91A3" stroke="#fff" strokeWidth={2} isAnimationActive={false} />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
            <div className="text-xs text-ink-600">오른쪽 위로 갈수록 구조 취약성이 높고 안부·관계 거점에서 멉니다. 순위를 매기는 그림이 아닙니다. {HUB_NOTE}</div>
          </Card>
        </div>
      </PageBody>
    </>
  )
}
