import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api'

export type AsyncState<T> = { data: T | null; error: ApiError | null; loading: boolean; reload: () => void }

/** 간단한 불러오기 훅. deps가 바뀌면 다시 부른다. 늦게 온 옛 응답은 버린다. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const seq = useRef(0)

  useEffect(() => {
    const my = ++seq.current
    setLoading(true)
    setError(null)
    fn()
      .then((d) => {
        if (my === seq.current) setData(d)
      })
      .catch((e: unknown) => {
        if (my === seq.current) setError(e instanceof ApiError ? e : new ApiError(0, 'UNKNOWN', '알 수 없는 오류가 생겼습니다.'))
      })
      .finally(() => {
        if (my === seq.current) setLoading(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, error, loading, reload }
}
