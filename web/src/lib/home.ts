// 첫 화면 자료: 실제 API(GET /home) 또는 정적 배포 JSON(/data/home). '오늘 기준'은 작년 같은 날의 결과다.
import { api, type City, type HomeCandidate, type HomeResponse, type ReqOpts, type StaticIndex } from '../api'
import { plainText } from './labels'

/** 정적 배포(Vercel): 미리 계산한 집계 JSON 만 읽는다. 원자료·실제 에이전트는 없음 */
export const STATIC_MODE: boolean = (import.meta.env.VITE_STATIC as string | undefined) === '1'

const CITY_CODE: Record<City, string> = { 강남: 'GN', 춘천: 'CC' }

export const DATA_FROM = '2025-07-01'
export const DATA_TO = '2025-12-31'

/** 한국 시간 오늘 (YYYY-MM-DD) */
export function todayKST(now: Date = new Date()): string {
  return new Date(now.getTime() + 9 * 3600_000).toISOString().slice(0, 10)
}

/** 오늘의 월·일을 2025년 자료에 대입. 자료가 없는 1~6월이면 null */
export function sameDayLastData(today: string): string | null {
  const md = today.slice(5)
  const d = `2025-${md}`
  return d >= DATA_FROM && d <= DATA_TO ? d : null
}

/** 정적 기준일 목록에서 d 이전의 가장 가까운 날 (없으면 첫날) */
export function snapDate(d: string, dates: string[]): string {
  const before = dates.filter((x) => x <= d)
  return before.length ? before[before.length - 1] : dates[0]
}

/** 다가오는 연휴 (실제 2026~2027 달력, 사흘 이상). 2025 자료에는 같은 시기 결과만 있다 */
export const UPCOMING: { name: string; start: string; end: string }[] = [
  { name: '성탄절 연휴', start: '2026-12-25', end: '2026-12-27' },
  { name: '신정 연휴', start: '2027-01-01', end: '2027-01-03' },
  { name: '설 연휴', start: '2027-02-06', end: '2027-02-09' },
]

export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000)
}

/** 오늘 이후 연휴 + 작년 같은 시기(연휴 7일 전) 자료 날짜 */
export function upcomingEvents(today: string) {
  return UPCOMING.filter((e) => e.end >= today).map((e) => {
    const d7 = new Date(Date.parse(e.start) - 7 * 86_400_000).toISOString().slice(0, 10)
    return { ...e, dDay: daysBetween(today, e.start), analog: sameDayLastData(d7) }
  })
}

let indexCache: Promise<StaticIndex> | null = null
export function loadIndex(): Promise<StaticIndex> {
  indexCache ??= fetch('/data/home/index.json').then((r) => {
    if (!r.ok) throw new Error('정적 자료 목록을 읽지 못했습니다')
    return r.json() as Promise<StaticIndex>
  })
  return indexCache
}

/** 첫 화면 자료. 정적 모드면 가장 가까운 내보낸 기준일로 맞춘다 */
export async function loadHome(as_of: string, city: City, o?: ReqOpts): Promise<HomeResponse> {
  if (!STATIC_MODE) return api.home(as_of, city, o)
  const idx = await loadIndex()
  const d = snapDate(as_of, idx.dates)
  const r = await fetch(`/data/home/${d}_${CITY_CODE[city]}.json`, { signal: o?.signal })
  if (!r.ok) throw new Error(`${d} ${city} 자료가 없습니다`)
  return (await r.json()) as HomeResponse
}

// ---- 정적 모드 질문: 이 화면의 근거 카드에서만 답을 찾는다 (LLM 아님, 새 숫자를 만들지 않음)
export const EXAMPLE_QUESTIONS = ['왜 이 동을 먼저 봐야 해?', '어떤 자원을 연결하면 돼?', '담당자는 무엇을 하면 돼?', '이 결과의 한계는?']

export function localAnswer(q: string, home: HomeResponse, picked: HomeCandidate | null): string[] {
  const top = picked ?? home.candidates.find((c) => c.candidate_level === 'Level 3') ?? home.candidates[0] ?? null
  const where = top ? `${top.adm_nm ?? home.city} ${top.group ?? top.analysis_cohort}` : home.city
  if (!top) return [`${home.as_of} ${home.city}에는 후보가 없습니다. 다른 기준일을 골라 보세요.`]
  if (/자원|연결|기관|병원|약국/.test(q)) {
    return top.resources.length
      ? [`${where}에 먼저 연결할 수 있는 곳:`, ...top.resources.map((r) => `· ${r.name} (${r.resource_type}${r.distance_km != null ? `, ${r.distance_km.toFixed(1)}km` : ''}${r.phone_based ? ', 전화' : ''}, 자격 ${r.eligibility})`)]
      : [`${where}에 맞춰 찾은 자원이 없습니다. 실제 자원 목록은 로컬 실행 화면에서 확인하세요.`]
  }
  if (/할 일|무엇|대응|담당/.test(q)) {
    const watch = home.summary.holiday_watch.find((l) => l.startsWith('할 일'))
    return [`${where}: ${top.response_type ?? '추세 관찰'}.`, ...(watch ? [watch] : []), '판단은 담당자가 합니다. 기존 돌봄·사례관리 명단에서 해당 칸 대상자를 먼저 확인하세요.']
  }
  if (/한계|정확|믿|확실/.test(q)) {
    const lim = home.summary.holiday_watch.find((l) => l.startsWith('관측 한계'))
    return [
      '2025년 하반기 집계 자료로 계산한 결과이고, 실제 고립 여부를 판정하지 않습니다.',
      ...(lim ? [lim] : []),
      `근거 신뢰도: ${top.evidence_confidence ?? '표시 없음'}${top.input_status !== '충족' ? ` · 빠진 근거: ${top.missing_inputs.join(', ')}` : ''}`,
    ]
  }
  return [`${where} · ${top.candidate_level ?? '등급 전'}`, plainText(top.level_reason ?? '등급 이유 없음')]
}
