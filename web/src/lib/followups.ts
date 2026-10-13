// 후속 확인일 모아 보기. GET /followups 한 번으로 읽는다 (후보마다 가장 최근 조사 실행의 마지막 후속 확인일).
import { api, type City, type FollowUpItem } from '../api'
import { useAsync } from './useAsync'
import { daysBetween } from './labels'

export type FollowUp = FollowUpItem

/** 기준일과 비교한 후속 확인 상태: 지남 / 이번 주(7일 안) / 나중 */
export type FollowState = 'overdue' | 'week' | 'later'
const FROM_SERVER: Record<string, FollowState> = { 지남: 'overdue', '이번 주': 'week', 나중: 'later' }

/** 서버가 준 state(as_of 기준)를 먼저 쓰고, 없으면 날짜로 계산 */
export function followState(f: Pick<FollowUp, 'follow_up_on' | 'state'>, as_of: string): FollowState {
  const s = f.state ? FROM_SERVER[f.state] : undefined
  if (s) return s
  const d = daysBetween(as_of, f.follow_up_on)
  if (d < 0) return 'overdue'
  return d <= 7 ? 'week' : 'later'
}

/** 이 도시의 후속 확인 (서버가 도시·기준일로 거르고 state 를 붙여 줌) */
export function useFollowUps(city: City, as_of: string) {
  const res = useAsync(() => api.followups({ city, as_of }), [city, as_of])
  const items = res.data?.items ?? []
  return { ...res, items, byId: new Map(items.map((f) => [f.candidate_id, f])) }
}
