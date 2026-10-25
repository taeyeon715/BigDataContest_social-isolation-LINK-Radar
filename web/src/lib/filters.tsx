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

// 시연 추천 시점: 어떤 장면을 보여 주는지 한 줄 설명(desc)과 함께. 도시도 같이 바꾼다
export const PRESETS: { label: string; city: City; as_of: string; title: string; desc: string }[] = [
  { label: '추석 D-7 (9/26)', city: '춘천', as_of: '2025-09-26', title: '연휴 직전 점검', desc: '추석 연휴 7일 전. 지난 연휴에 의료 결제가 끊긴 60대 이상 칸이 Level 3로 올라옵니다 (두 도시 각 4개 동).' },
  { label: '추석 뒤 (10/13)', city: '춘천', as_of: '2025-10-13', title: '연휴 뒤 다시 확인', desc: '연휴가 끝난 뒤 춘천 5개 동을 다시 먼저 봅니다. 확인 결과가 아직 없는 칸은 판단 보류로 남습니다.' },
  { label: '동절기 (12/20)', city: '춘천', as_of: '2025-12-20', title: '겨울 점검', desc: '연휴가 아니어도 겨울에 체류가 줄어든 춘천 읍·면(동산면·남산면) 60대 이상을 먼저 봅니다.' },
]
