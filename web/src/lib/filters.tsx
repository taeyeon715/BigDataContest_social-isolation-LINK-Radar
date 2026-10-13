import { useSearchParams } from 'react-router-dom'
import type { City } from '../api'
import { CITIES } from '../api'

export const DEFAULT_AS_OF = '2025-09-26'
export const DEFAULT_CITY: City = '춘천'

/** 기준일·도시는 주소의 ?as_of=&city= 로 유지해 모든 페이지가 같은 값을 쓴다. */
export function useFilters() {
  const [params, setParams] = useSearchParams()
  const rawCity = params.get('city')
  const city: City = CITIES.includes(rawCity as City) ? (rawCity as City) : DEFAULT_CITY
  const as_of = params.get('as_of') ?? DEFAULT_AS_OF
  const set = (next: Partial<{ city: City; as_of: string }>) => {
    const p = new URLSearchParams(params)
    if (next.city) p.set('city', next.city)
    if (next.as_of) p.set('as_of', next.as_of)
    setParams(p, { replace: true })
  }
  const query = `?as_of=${as_of}&city=${encodeURIComponent(city)}`
  return { city, as_of, set, query }
}

/** 시연용 바로가기 (docs/화면_설계.md §5) */
export const PRESETS: { label: string; city: City; as_of: string }[] = [
  { label: '추석 D-7 (9/26)', city: '춘천', as_of: '2025-09-26' },
  { label: '추석 뒤 (10/13)', city: '강남', as_of: '2025-10-13' },
  { label: '동절기 (11/14)', city: '춘천', as_of: '2025-11-14' },
]
