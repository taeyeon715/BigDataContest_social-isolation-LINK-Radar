import { useCallback, useContext, useEffect, useRef } from 'react'
import { UNSAFE_DataRouterContext, useBeforeUnload, useBlocker } from 'react-router-dom'

function NavigationGuard({ dirty }: { dirty: boolean }) {
  const handled = useRef<string | null>(null)
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && (
    currentLocation.pathname !== nextLocation.pathname ||
    new URLSearchParams(currentLocation.search).get('city') !== new URLSearchParams(nextLocation.search).get('city') ||
    new URLSearchParams(currentLocation.search).get('as_of') !== new URLSearchParams(nextLocation.search).get('as_of')
  ))
  useEffect(() => {
    if (blocker.state !== 'blocked') { handled.current = null; return }
    if (handled.current === blocker.location.key) return
    handled.current = blocker.location.key
    if (window.confirm('저장하지 않은 검토 내용이 있습니다. 저장하지 않고 이동하시겠습니까?')) blocker.proceed()
    else blocker.reset()
  }, [blocker])
  return null
}

export function UnsavedReviewGuard({ dirty }: { dirty: boolean }) {
  const dataRouter = useContext(UNSAFE_DataRouterContext)
  useBeforeUnload(useCallback((event: BeforeUnloadEvent) => {
    if (!dirty) return
    event.preventDefault()
    event.returnValue = ''
  }, [dirty]))
  return dataRouter ? <NavigationGuard dirty={dirty} /> : null
}
