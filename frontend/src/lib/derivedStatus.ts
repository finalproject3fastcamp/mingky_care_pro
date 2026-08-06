/**
 * 이벤트 스트림에서 로봇/세션 상태를 파생하는 순수 함수들.
 *
 * 이 화면 전용이다. 백엔드가 실시간 로봇 상태 API 를 별도로 두지 않기로 해
 * (docs/monitoring-spec.md), 상태는 events 를 뒤로 훑어 계산한다. 그래서
 * 파생 결과가 정확하려면 events 폴링이 세션의 최근 이력을 충분히 담아야 한다
 * (limit 을 넉넉히).
 *
 * 파생은 근사값이다.
 *   - 이벤트 배치 전송 지연이 있으면 반영이 몇 초 늦는다.
 *   - config/event_codes.yaml 에 회복 이벤트가 없는 상태 (battery_low,
 *     paused 등) 는 한 번 발생하면 세션 끝날 때까지 계속 "그 상태" 로 잡힌다.
 * 실시간 상태 push 채널이 붙기 전까지의 임시 계층이다.
 */

import type { EventOut } from '../types/events'

export interface DerivedDestination {
  /** session_steps.visit_name. 예: "X-ray". */
  visitName: string
  /** 해당 목적지에 도달했는지 (같은 세션에서 이후 nav.goal_succeeded 가 있음). */
  arrived: boolean
}

/**
 * 세션의 "현재 목적지" — 가장 최근에 발행된 nav.goal_sent 의 visit_name.
 *
 * events 는 occurred_at DESC 순 (백엔드 정렬 규약, routers/events.py) 을 그대로
 * 받는다고 가정한다. 세션에 nav.goal_sent 가 하나도 없으면 null (아직 이동
 * 시작 전이거나, 폴링 window 를 벗어난 오래된 세션).
 */
export function deriveCurrentDestination(
  events: EventOut[],
  sessionId: number,
): DerivedDestination | null {
  const sessionEvents = events.filter((e) => e.session_id === sessionId)
  const idx = sessionEvents.findIndex((e) => e.event_code === 'nav.goal_sent')
  if (idx < 0) return null

  const visit = sessionEvents[idx].payload.visit_name
  if (typeof visit !== 'string') return null

  // 배열은 DESC 정렬이므로 idx 앞쪽 (0..idx-1) 이 이 nav.goal_sent 이후에
  // 발생한 이벤트다. 그 사이 nav.goal_succeeded 가 있으면 도착 처리.
  const arrived = sessionEvents
    .slice(0, idx)
    .some((e) => e.event_code === 'nav.goal_succeeded')

  return { visitName: visit, arrived }
}
