import { useEffect, useRef, useState } from 'react'

export interface PollingState<T> {
  data: T | null
  error: unknown
  loading: boolean
}

/**
 * 주기 fetcher 를 폴링하는 훅.
 *
 * 조회 대상이 바뀔 수 있는 화면(로봇을 골라 보는 등)은 `key` 를 넘겨야
 * 한다. 안 넘기면 대상이 바뀌어도 다음 tick 까지 이전 대상의 데이터가
 * 화면에 남고, 주기가 길수록 그 시간 동안 다른 로봇을 보면서 판단하게
 * 된다. key 가 바뀌면 즉시 재조회하고 이전 데이터를 지운다.
 *
 * fetcher 는 `AbortSignal` 을 받는다. 언마운트나 다음 tick 이 오기 전에
 * 이전 요청을 취소해, 늦게 도착한 응답이 새 상태를 덮어쓰는 일과 사라진
 * 컴포넌트를 위해 서버를 두들기는 낭비를 함께 막는다. 취소를 사용하지 않는
 * 호출부는 signal 을 무시해도 된다 — 타입 체크만 통과하면 된다.
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  key?: string | number | null,
): PollingState<T> {
  const [state, setState] = useState<PollingState<T>>({
    data: null,
    error: null,
    loading: true,
  })
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  // 렌더가 아니라 effect 안에서 비교해야 한다. StrictMode 가 렌더를 두 번
  // 부르면 렌더 중 갱신한 값은 두 번째 렌더에서 이미 같아져 초기화를 놓친다.
  const keyRef = useRef(key)

  useEffect(() => {
    let disposed = false
    let inFlight: AbortController | null = null

    // 대상이 바뀌었으면 이전 대상의 데이터를 즉시 지운다.
    //
    // 남겨두면 다음 tick 까지 다른 대상의 값이 화면에 그대로 있다. 주기가
    // 30초면 그동안 "이 로봇은 문제 없음" 으로 읽히고, 실제로는 다른 로봇을
    // 보고 있는 것이다. 값이 없는 화면은 사람이 기다리지만, 틀린 값이 있는
    // 화면은 사람이 판단해버린다.
    //
    // key 를 안 넘기는 호출부(대상이 하나뿐인 화면)는 undefined 로 고정돼
    // 이 블록이 한 번도 실행되지 않는다.
    if (keyRef.current !== key) {
      keyRef.current = key
      setState({ data: null, error: null, loading: true })
    }

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
    // key 를 deps 에 넣어야 대상이 바뀔 때 즉시 재조회가 걸린다. fetcherRef
    // 만 갱신하면 다음 tick 까지 이전 대상의 응답을 계속 받는다.
  }, [intervalMs, key])

  return state
}
