// 조사 실행 상태 폴링 (FE-02).
// - 진행 중이거나 보고서를 다시 만드는 중이면 1초마다 다시 부른다.
// - 잠깐 끊긴 오류(연결 실패·시간 초과·5xx)는 2·4·8·16초 간격으로 다시 시도하고, 그래도 안 되면 오류를 보인다.
// - 페이지를 떠나거나 다른 실행으로 바뀌면 진행 중 요청을 끊고(AbortController) 다시 걸지 않는다.
// - 진행 중 요청이 있을 때 refresh() 를 부르면 새 줄을 만들지 않고, 그 요청이 끝난 뒤 한 번만 더 부른다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, isAborted, isTransient, type RunStatusResponse } from '../api'
import { regenerating } from '../components/ApprovalPanel'

export const POLL_MS = 1000
export const MAX_RETRIES = 4
const BUSY = new Set(['queued', 'running'])

/** n번째 다시 시도까지 기다릴 시간: 2, 4, 8, 16초 */
export function backoffMs(n: number): number {
  return 1000 * 2 ** Math.min(n, 4)
}

function toApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError(0, 'UNKNOWN', '알 수 없는 오류가 생겼습니다.')
}

export type RunPoll = {
  run: RunStatusResponse | null
  error: ApiError | null
  /** 잠깐 끊겨 다시 시도하는 중이면 몇 번째인지 (0 = 아님) */
  retrying: number
  /** 지금 상태를 다시 부름. 진행 중 요청이 있으면 그 뒤 한 번만 */
  refresh: () => Promise<void>
}

export function useRunPoll(runId: string | null): RunPoll {
  const [run, setRun] = useState<RunStatusResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [retrying, setRetrying] = useState(0)
  const refreshRef = useRef<() => Promise<void>>(() => Promise.resolve())

  useEffect(() => {
    setRun(null)
    setError(null)
    setRetrying(0)
    if (!runId) {
      refreshRef.current = () => Promise.resolve()
      return
    }
    const ac = new AbortController()
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let inflight: Promise<void> | null = null
    let queued: Promise<void> | null = null
    let fails = 0

    const schedule = (ms: number) => {
      clearTimeout(timer)
      if (alive) timer = setTimeout(tick, ms)
    }

    function tick(): Promise<void> {
      if (!alive) return Promise.resolve()
      if (inflight) {
        // 이미 부르는 중: 줄을 하나 더 만들지 않고, 끝난 뒤 한 번만 더
        queued ??= inflight.then(() => {
          queued = null
          return tick()
        })
        return queued
      }
      clearTimeout(timer)
      const p = (async () => {
        try {
          const r = await api.run(runId!, { signal: ac.signal })
          if (!alive || r.run_id !== runId) return
          fails = 0
          setRun(r)
          setError(null)
          setRetrying(0)
          if (BUSY.has(r.status) || regenerating(r)) schedule(POLL_MS)
        } catch (e) {
          if (!alive || isAborted(e)) return
          const err = toApiError(e)
          if (isTransient(err) && fails < MAX_RETRIES) {
            fails += 1
            setRetrying(fails)
            schedule(backoffMs(fails))
          } else {
            setRetrying(0)
            setError(err)
          }
        }
      })().finally(() => {
        inflight = null
      })
      inflight = p
      return p
    }

    refreshRef.current = () => {
      fails = 0
      return tick()
    }
    tick()
    return () => {
      alive = false
      clearTimeout(timer)
      ac.abort()
    }
  }, [runId])

  const refresh = useCallback(() => refreshRef.current(), [])
  return { run, error, retrying, refresh }
}
