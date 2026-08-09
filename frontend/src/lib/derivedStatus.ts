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
 *   - 상태/회복 이벤트 쌍 중 최신 이벤트를 기준으로 현재 상태를 판정한다.
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

/** DESC 정렬된 이벤트에서 상태/회복 쌍의 현재 활성 여부를 판정한다. */
function isTransitionActive(
  events: EventOut[],
  activeCode: string,
  clearedCode: string,
): boolean {
  const latest = events.find(
    (event) => event.event_code === activeCode || event.event_code === clearedCode,
  )
  return latest?.event_code === activeCode
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
 *   2. 최신 통신 전이가 robot.comm_lost         → '통신 두절'
 *   3. 최신 배터리 전이가 robot.battery_low     → '배터리 부족'
 *   4. 최신 정지 전이가 robot.paused            → '일시정지'
 *   5. 세션의 최신 nav.* 이벤트로 판정
 *        goal_sent      → '안내중'
 *        goal_succeeded → '도착'
 *        goal_aborted   → '경로 이탈'
 *        stuck          → '경로 이탈'
 *   6. session.started 있음                    → '환자 확인'
 *   7. 그 외                                   → '대기'
 */
export function deriveRobotState(
  events: EventOut[],
  session: { session_id: number; ended_at: string | null },
): RobotState {
  if (session.ended_at) return '완료'

  const sessionEvents = events.filter((e) => e.session_id === session.session_id)

  if (isTransitionActive(sessionEvents, 'robot.comm_lost', 'robot.comm_restored')) {
    return '통신 두절'
  }
  if (isTransitionActive(sessionEvents, 'robot.battery_low', 'robot.battery_recovered')) {
    return '배터리 부족'
  }
  if (isTransitionActive(sessionEvents, 'robot.paused', 'robot.resumed')) {
    return '일시정지'
  }

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
