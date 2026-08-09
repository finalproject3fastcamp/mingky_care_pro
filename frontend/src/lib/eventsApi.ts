/**
 * GET /events 호출부. (backend/app/routers/events.py)
 *
 * api.ts 의 axios 인스턴스를 그대로 쓴다. 새로 만들면 baseURL 이 두 군데가 되고,
 * dev 프록시(/api)를 한쪽만 타는 사고가 난다.
 */

import { api } from './api'
import type { EventPage, EventQuery } from '../types/events'

/**
 * <input type="datetime-local"> 값을 오프셋이 붙은 ISO 문자열로 바꾼다.
 *
 * 그 input 은 '2026-08-06T11:00' 처럼 타임존이 없는 문자열을 준다.
 * 그대로 보내면 asyncpg 가 **백엔드 프로세스의 로컬 타임존**으로 해석한다.
 * 개발 PC(KST)에서는 맞게 동작하다가 UTC 컨테이너에 배포하는 순간 9시간
 * 어긋나고, 에러가 안 나고 결과만 조용히 틀린다.
 *
 * 필터 입력은 반드시 이 함수를 거쳐서 EventQuery 에 넣는다.
 */
export function toIsoOrUndefined(localValue: string | undefined): string | undefined {
  if (!localValue) return undefined
  const parsed = new Date(localValue)
  // 사용자가 입력 중인 반쪽짜리 값이면 Invalid Date 가 된다.
  // 그대로 보내면 백엔드가 422 를 내므로 여기서 없던 조건으로 취급한다.
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString()
}

/**
 * 미선택 필터를 쿼리스트링에서 뺀다.
 *
 * axios 는 undefined 값을 알아서 빼주지만 **빈 문자열은 빼지 않는다.**
 * source_node: '' 를 넘기면 `?source_node=` 가 붙고, 백엔드는 None 이 아닌
 * '' 로 받아 `source_node = ''` 조건을 건다 → 결과 0 건.
 * 화면에는 "이벤트 없음"만 뜨고 원인이 안 드러나는 종류의 버그다.
 */
function pruneEmpty(query: EventQuery): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(query).filter(([, value]) => value !== undefined && value !== ''),
  )
}

export interface ListEventsOptions {
  /**
   * 진행 중인 요청 취소용.
   * 폴링 도중 필터가 바뀌면 이전 요청 응답이 나중에 도착해 화면을 되돌릴 수 있다.
   */
  signal?: AbortSignal
}

/**
 * 이벤트 목록 조회.
 *
 * 서버는 occurred_at DESC 로 정렬한다(도착순이 아니다). 두절 후 몰아 들어온
 * 이벤트는 목록 맨 위가 아니라 발생 시각 자리에 끼어든다. offset 페이지네이션과
 * 폴링을 동시에 쓰면 페이지가 밀리므로, 호출하는 쪽에서 tail(offset 0 고정 +
 * 폴링)과 history(폴링 정지 + 페이지 이동)를 나눠서 쓴다.
 */
export async function listEvents(
  query: EventQuery = {},
  options: ListEventsOptions = {},
): Promise<EventPage> {
  const response = await api.get<EventPage>('/events', {
    params: pruneEmpty(query),
    signal: options.signal,
  })
  return response.data
}
