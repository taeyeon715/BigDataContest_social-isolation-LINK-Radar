import 'leaflet/dist/leaflet.css'
import type { Feature, FeatureCollection, Geometry } from 'geojson'
import L, { type Layer, type PathOptions } from 'leaflet'
import { useEffect, useMemo, useState } from 'react'
import { GeoJSON, MapContainer, TileLayer, useMap } from 'react-leaflet'
import type { CandidateLevel, City, HomeArea } from '../api'
import { CITY_CENTER } from '../map/centroids'

// 먼저 볼 곳 지도: 행정동 경계를 그 동 후보의 가장 높은 등급 색으로 칠한다 (척도는 Level 하나, 위험 점수 아님).
// 경계: vuski/admdongkor ver20250701 (CC BY 4.0, 원천 통계청 SGIS), public/data/dong_boundaries.json
export const LEVEL_FILL: Record<CandidateLevel, string> = {
  'Level 3': '#1F5FA8',
  'Level 2': '#5E9BD9',
  'Level 1': '#B9D3F0',
  'Level 0': '#DCE3EE',
  '판단 보류': '#9AA1AE',
}
const NONE_FILL = '#F4F6FA'
const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> · 행정동 경계 <a href="https://github.com/vuski/admdongkor" target="_blank" rel="noreferrer">vuski/admdongkor</a> (통계청 SGIS, CC BY 4.0)'

type Props = { city: City; areas: HomeArea[]; selected: string | null; onSelect: (adm: string) => void }
type DongProps = { adm_cd: string; adm_nm: string; city: City }

let boundaries: Promise<FeatureCollection<Geometry, DongProps>> | null = null
function loadBoundaries() {
  boundaries ??= fetch('/data/dong_boundaries.json').then((r) => r.json())
  return boundaries
}

/** 후보가 있는 동(없으면 도시 전체)이 보이게 확대. 고른 동이 있으면 그 동으로 */
function FitTo({ data, focus }: { data: FeatureCollection<Geometry, DongProps>; focus: string[] }) {
  const map = useMap()
  const key = focus.join(',')
  useEffect(() => {
    const feats = data.features.filter((f) => focus.includes(f.properties.adm_cd))
    const b = L.geoJSON({ ...data, features: feats.length ? feats : data.features } as FeatureCollection).getBounds()
    if (b.isValid()) map.fitBounds(b, { padding: [24, 24], maxZoom: 14 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, data, key])
  return null
}

/** 먼저 확대할 동: Level 2 이상, 없으면 이탈 경보의 먼저 볼 동 */
function focusOf(areas: HomeArea[]): string[] {
  const hi = areas.filter((a) => a.level === 'Level 3' || a.level === 'Level 2')
  return (hi.length ? hi : areas.filter((a) => a.deviation_first)).map((a) => a.adm_cd)
}

export function DongMap({ city, areas, selected, onSelect }: Props) {
  const [geo, setGeo] = useState<FeatureCollection<Geometry, DongProps> | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    loadBoundaries().then(setGeo).catch(() => setFailed(true))
  }, [])
  const byAdm = useMemo(() => new Map(areas.map((a) => [a.adm_cd, a])), [areas])
  const data = useMemo(
    () => (geo ? { ...geo, features: geo.features.filter((f) => f.properties.city === city) } : null),
    [geo, city],
  )
  const style = (f?: Feature<Geometry, DongProps>): PathOptions => {
    const a = f ? byAdm.get(f.properties.adm_cd) : undefined
    const sel = f?.properties.adm_cd === selected
    return {
      fillColor: a?.level ? LEVEL_FILL[a.level] : NONE_FILL,
      fillOpacity: a?.level ? 0.8 : 0.5,
      color: sel ? '#14171F' : a?.deviation_first ? '#7A5BC7' : '#FFFFFF',
      weight: sel ? 3 : a?.deviation_first ? 2.5 : 1,
      dashArray: a?.deviation_first && !sel ? '4 3' : undefined,
    }
  }
  const onEach = (f: Feature<Geometry, DongProps>, layer: Layer) => {
    const a = byAdm.get(f.properties.adm_cd)
    layer.bindTooltip(`${f.properties.adm_nm} · ${a?.level ?? '후보 없음'}`, { sticky: true })
    layer.on('click', () => onSelect(f.properties.adm_cd))
  }
  if (failed) return <div className="grid h-full place-items-center text-sm text-ink-600">행정동 경계를 불러오지 못했습니다.</div>
  return (
    <MapContainer key={city} center={CITY_CENTER[city]} zoom={city === '강남' ? 13 : 10} scrollWheelZoom={false} className="h-full w-full">
      <TileLayer attribution={ATTRIBUTION} url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" opacity={0.5} />
      {data && (
        // 등급·선택이 바뀌면 다시 그림 (react-leaflet GeoJSON 은 data 가 같으면 스타일을 갱신하지 않음)
        <>
          <GeoJSON key={`${city}-${selected}-${areas.map((a) => a.level ?? '-').join('')}`} data={data} style={style} onEachFeature={onEach} />
          <FitTo data={data} focus={selected ? [selected] : focusOf(areas)} />
        </>
      )}
    </MapContainer>
  )
}

export function LevelLegend() {
  const items: [string, string][] = [
    ['Level 3 우선 현장 검토', LEVEL_FILL['Level 3']],
    ['Level 2 검토 후보', LEVEL_FILL['Level 2']],
    ['Level 1 변화 관찰', LEVEL_FILL['Level 1']],
    ['판단 보류', LEVEL_FILL['판단 보류']],
    ['후보 없음', NONE_FILL],
  ]
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-800" data-testid="level-legend">
      {items.map(([t, c]) => (
        <span key={t} className="flex items-center gap-1">
          <span className="inline-block h-3 w-5 rounded-sm border border-line" style={{ background: c }} />
          {t}
        </span>
      ))}
      <span className="flex items-center gap-1">
        <span className="inline-block h-3 w-5 rounded-sm border-2 border-dashed" style={{ borderColor: '#7A5BC7' }} />
        카드 이탈 경보의 먼저 볼 동
      </span>
    </div>
  )
}
