/**
 * 필터 폼 값과 EventQuery 사이의 변환.
 *
 * 컴포넌트 파일(EventFilters.tsx)에 같이 두면 Fast Refresh 가 깨진다.
 * 컴포넌트 외의 것을 export 하는 순간 그 모듈은 통째로 재평가되고,
 * dev 중 필터를 편집할 때마다 입력값이 초기화된다.
 */

import { toIsoOrUndefined } from './eventsApi'
import type { EventLevel, EventQuery } from '../types/events'

/**
 * 폼이 들고 있는 날것의 값. 전부 문자열이다.
 *
 * EventQuery 와 따로 두는 이유
 *   - <input type="datetime-local"> 은 '2026-08-06T11:00' 같은 타임존 없는
 *     문자열을 주고받는다. EventQuery.since 는 오프셋이 붙은 ISO 여야 하므로
 *     그대로 넣을 수 없고, 반대로 ISO 를 input 에 되돌려 넣을 수도 없다.
 *   - 미선택을 빈 문자열로 표현해야 <select> 가 정상 동작한다.
 *     EventQuery 쪽은 미선택이 undefined 다.
 */
export interface EventFilterValues {
  robot_id: string
  min_level: string
  code_prefix: string
  source_node: string
  session_id: string
  since: string
  until: string
}

export const EMPTY_FILTERS: EventFilterValues = {
  robot_id: '',
  min_level: '',
  code_prefix: '',
  source_node: '',
  session_id: '',
  since: '',
  until: '',
}

/**
 * 폼 값 → GET /events 쿼리.
 *
 * limit/offset 은 페이지네이션 소유라 여기서 만들지 않는다.
 * 빈 문자열은 넣지 않는다 — 넘기면 백엔드가 None 이 아닌 '' 로 받아
 * `source_node = ''` 조건을 걸고 결과가 0 건이 된다.
 */
export function toEventQuery(values: EventFilterValues): EventQuery {
  const sessionId = Number.parseInt(values.session_id, 10)

  return {
    robot_id: values.robot_id || undefined,
    min_level: (values.min_level || undefined) as EventLevel | undefined,
    code_prefix: values.code_prefix || undefined,
    source_node: values.source_node.trim() || undefined,
    // 입력 도중의 반쪽 값('-' 만 친 상태 등)은 NaN 이 된다. 조건에서 뺀다.
    session_id: Number.isNaN(sessionId) ? undefined : sessionId,
    // 타임존 규칙은 toIsoOrUndefined 안에 있다. 여기서 직접 문자열을 만들지 않는다.
    since: toIsoOrUndefined(values.since),
    until: toIsoOrUndefined(values.until),
  }
}
