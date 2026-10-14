import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, isAborted } from '../api'

export type AsyncState<T> = {
  data: T | null
  error: ApiError | null
  loading: boolean
  reload: () => void
}

function toApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError(0, 'UNKNOWN', '알 수 없는 오류가 생겼습니다.')
}

/** 간단한 불러오기 훅. deps가 바뀌면 다시 부른다.
 * - 이전 요청은 AbortSignal 로 끊고, 늦게 온 옛 응답은 버린다.
 * - data 는 deps 키와 함께 둔다. 도시·기준일이 바뀌면 새 응답이 올 때까지 옛 값을 보이지 않는다 (FE-03).
 *   같은 키로 다시 부를 때(reload)는 옛 값을 둔 채 새로 부른다.
 * - 페이지를 떠나면(언마운트) 진행 중 요청을 끊는다. */
export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[]): AsyncState<T> {
  const key = JSON.stringify(deps)
  const [state, setState] = useState<{ key: string; data: T | null; error: ApiError | null; loading: boolean }>({
    key,
    data: null,
    error: null,
    loading: true,
  })
  const [tick, setTick] = useState(0)
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    const ac = new AbortController()
    setState((s) => (s.key === key ? { ...s, error: null, loading: true } : { key, data: null, error: null, loading: true }))
    fnRef
      .current(ac.signal)
      .then((d) => {
        if (!ac.signal.aborted) setState({ key, data: d, error: null, loading: false })
      })
      .catch((e: unknown) => {
        if (ac.signal.aborted || isAborted(e)) return
        setState((s) => ({ key, data: s.key === key ? s.data : null, error: toApiError(e), loading: false }))
      })
    return () => ac.abort()
  }, [key, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  // 키가 바뀐 직후(효과가 돌기 전 첫 그림)에도 옛 키의 값은 보이지 않게
  if (state.key !== key) return { data: null, error: null, loading: true, reload }
  return { data: state.data, error: state.error, loading: state.loading, reload }
}
