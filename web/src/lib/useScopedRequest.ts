import { useCallback, useEffect, useRef } from 'react'

/** 조회 범위가 바뀌거나 화면을 떠나면 요청을 끊고, 이미 도착 중인 응답도 버린다. */
export function useScopedRequest(scope: string) {
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  const active = useRef<AbortController | null>(null)
  useEffect(() => () => active.current?.abort(), [scope])
  return useCallback(() => {
    active.current?.abort()
    const controller = new AbortController()
    const startedScope = scopeRef.current
    active.current = controller
    return {
      signal: controller.signal,
      current: () => !controller.signal.aborted && active.current === controller && scopeRef.current === startedScope,
    }
  }, [])
}
