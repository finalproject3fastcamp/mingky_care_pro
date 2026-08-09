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
import type { RobotState } from '../types/monitoring'

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

/**
 * 이벤트에서 현재 로봇 상태를 파생한다.
 *
 * 우선순위 (앞에서 걸리면 그걸로 확정):
 *   1. session.ended_at 이 세팅됨              → '완료'
 *   2. robot.comm_lost 이벤트 있음             → '통신 두절'
 *   3. robot.battery_low 이벤트 있음           → '배터리 부족'
 *   4. robot.paused 이벤트 있음                → '일시정지'
 *   5. 세션의 최신 nav.* 이벤트로 판정
 *        goal_sent      → '안내중'
 *        goal_succeeded → '도착'
 *        goal_aborted   → '경로 이탈'
 *        stuck          → '경로 이탈'
 *   6. session.started 있음                    → '환자 확인'
 *   7. 그 외                                   → '대기'
 *
 * 2~4 는 sticky 하다. event_codes.yaml 에 회복 이벤트가 없어서, 한 번
 * 튀면 세션 끝날 때까지 그 상태로 잡힌다. 회복 이벤트가 정의되면 여기도
 * 함께 확장한다.
 */
export function deriveRobotState(
  events: EventOut[],
  session: { session_id: number; ended_at: string | null },
): RobotState {
  if (session.ended_at) return '완료'

  const sessionEvents = events.filter((e) => e.session_id === session.session_id)

  if (sessionEvents.some((e) => e.event_code === 'robot.comm_lost')) return '통신 두절'
  if (sessionEvents.some((e) => e.event_code === 'robot.battery_low')) return '배터리 부족'
  if (sessionEvents.some((e) => e.event_code === 'robot.paused')) return '일시정지'

  // DESC 정렬 가정. 최신 nav.* 하나만 본다.
  const latestNav = sessionEvents.find((e) => e.event_code.startsWith('nav.'))
  if (latestNav) {
    switch (latestNav.event_code) {
      case 'nav.goal_sent':
        return '안내중'
      case 'nav.goal_succeeded':
        return '도착'
      case 'nav.goal_aborted':
      case 'nav.stuck':
        return '경로 이탈'
    }
  }

  if (sessionEvents.some((e) => e.event_code === 'session.started')) return '환자 확인'
  return '대기'
}
