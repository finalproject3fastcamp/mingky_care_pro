import { useEffect, useRef, useState } from 'react'

export interface PollingState<T> {
  data: T | null
  error: unknown
  loading: boolean
}

/**
 * 주기 fetcher 를 폴링하는 훅.
 *
 * fetcher 는 `AbortSignal` 을 받는다. 언마운트나 다음 tick 이 오기 전에
 * 이전 요청을 취소해, 늦게 도착한 응답이 새 상태를 덮어쓰는 일과 사라진
 * 컴포넌트를 위해 서버를 두들기는 낭비를 함께 막는다. 취소를 사용하지 않는
 * 호출부는 signal 을 무시해도 된다 — 타입 체크만 통과하면 된다.
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
): PollingState<T> {
  const [state, setState] = useState<PollingState<T>>({
    data: null,
    error: null,
    loading: true,
  })
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    let disposed = false
    let inFlight: AbortController | null = null

    const tick = async () => {
      // 이전 요청이 아직 살아 있으면 끊는다. 폴링 주기보다 응답이 느린 경우
      // 두 응답이 예측할 수 없는 순서로 도착해 늦은 응답이 새 상태를 덮는다.
      inFlight?.abort()
      const controller = new AbortController()
      inFlight = controller

      try {
        const data = await fetcherRef.current(controller.signal)
        if (!disposed) setState({ data, error: null, loading: false })
      } catch (error) {
        // 취소는 우리가 일부러 끊은 것이다. 에러로 반영하면 언마운트 직전
        // 또는 다음 tick 로 갈 때 배너가 순간 뜬다. axios 에 의존하지 않도록
        // signal.aborted 로 판정한다.
        if (disposed || controller.signal.aborted) return
        setState((prev) => ({ ...prev, error, loading: false }))
      }
    }

    tick()
    const id = setInterval(tick, intervalMs)
    return () => {
      disposed = true
      clearInterval(id)
      inFlight?.abort()
    }
  }, [intervalMs])

  return state
}
