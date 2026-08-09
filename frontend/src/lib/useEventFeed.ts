/**
 * 이벤트 타임라인 전용 폴링 훅.
 *
 * lib/usePolling.ts 를 안 쓰는 이유
 *   - 그쪽은 useEffect deps 가 [intervalMs] 뿐이라 필터가 바뀌어도 다음 tick
 *     (최대 3 초) 까지 화면이 안 바뀐다. 필터 UI 에는 못 쓴다.
 *   - 폴링을 끌 수 없다. history 모드(과거 조회)에서는 멈춰야 한다.
 *   - 의료진 화면과 공유되는 파일이라 고치면 남의 작업에 영향이 간다.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'

import { listEvents } from './eventsApi'
import type { EventPage, EventQuery } from '../types/events'

export interface EventFeedState {
  /** 첫 응답 전에는 null. 필터를 바꾸는 동안에는 이전 결과를 유지한다. */
  page: EventPage | null
  error: unknown
  /** 새 조건의 첫 조회 중에만 true. 폴링 tick 에서는 올리지 않는다. */
  loading: boolean
  /** 폴링이 꺼진 history 모드에서 수동 재조회. */
  refresh: () => void
}

/**
 * query 객체를 useEffect deps 에 넣을 수 있는 문자열로 만든다.
 *
 * 객체를 그대로 deps 에 넣으면 매 렌더마다 새 참조라 effect 가 무한히 재실행된다.
 * JSON.stringify 만으로는 부족하다 — 키 순서가 다르면 내용이 같아도 다른
 * 문자열이 나와서, 호출부가 객체를 어떻게 조립했느냐에 따라 재조회가 터진다.
 * 그래서 키를 정렬한 뒤 직렬화한다.
 */
function stableKey(query: EventQuery): string {
  const entries = Object.entries(query)
    .filter(([, value]) => value !== undefined && value !== '')
    .sort(([a], [b]) => a.localeCompare(b))
  return JSON.stringify(entries)
}

/**
 * @param query    조회 조건. 바뀌면 즉시 재조회한다.
 * @param intervalMs 폴링 주기. null 이면 폴링하지 않는다(history 모드).
 */
export function useEventFeed(query: EventQuery, intervalMs: number | null): EventFeedState {
  const [state, setState] = useState<Omit<EventFeedState, 'refresh'>>({
    page: null,
    error: null,
    loading: true,
  })
  const [reloadToken, setReloadToken] = useState(0)

  // effect 는 key 로만 재실행하고, 실제 조건은 항상 최신 것을 읽는다.
  const queryRef = useRef(query)
  queryRef.current = query

  const key = stableKey(query)

  useEffect(() => {
    let disposed = false
    let inFlight: AbortController | null = null

    const run = async (isFirst: boolean) => {
      // 이전 요청이 아직 살아 있으면 끊는다. 안 끊으면 늦게 도착한 옛 응답이
      // 새 조건의 결과를 덮어쓴다.
      inFlight?.abort()
      const controller = new AbortController()
      inFlight = controller

      // 폴링 tick 마다 loading 을 올리면 3 초마다 목록이 사라졌다 나타난다.
      if (isFirst) setState((prev) => ({ ...prev, loading: true, error: null }))

      try {
        const page = await listEvents(queryRef.current, { signal: controller.signal })
        if (!disposed) setState({ page, error: null, loading: false })
      } catch (error) {
        // 취소는 우리가 일부러 끊은 것이다. 에러로 표시하면 필터를 건드릴
        // 때마다 에러 배너가 뜬다.
        if (disposed || axios.isCancel(error)) return
        setState((prev) => ({ ...prev, error, loading: false }))
      }
    }

    run(true)

    if (intervalMs === null) {
      return () => {
        disposed = true
        inFlight?.abort()
      }
    }

    const timer = setInterval(() => run(false), intervalMs)
    return () => {
      disposed = true
      clearInterval(timer)
      inFlight?.abort()
    }
    // query 대신 key 를 쓴다. 위 stableKey 주석 참고.
  }, [key, intervalMs, reloadToken])

  const refresh = useCallback(() => setReloadToken((n) => n + 1), [])

  return { ...state, refresh }
}
