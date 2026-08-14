import axios from 'axios'

import type { EventOut } from '../types/events'
import type {
  ActiveSession,
  QrObservation,
  Robot,
  RobotInventory,
} from '../types/monitoring'

const baseURL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export const api = axios.create({
  baseURL,
  timeout: 5000,
})

export async function getActiveSessions(
  options: { signal?: AbortSignal } = {},
): Promise<ActiveSession[]> {
  const { data } = await api.get<ActiveSession[]>('/sessions/active', {
    signal: options.signal,
  })
  return data
}

export async function getRobots(
  options: { signal?: AbortSignal } = {},
): Promise<Robot[]> {
  const { data } = await api.get<Robot[]>('/robots', { signal: options.signal })
  return data
}

export async function getQrObservation(
  robotId: string,
  options: { signal?: AbortSignal } = {},
): Promise<QrObservation> {
  const { data } = await api.get<QrObservation>(
    `/robots/${encodeURIComponent(robotId)}/qr-observation`,
    { signal: options.signal },
  )
  return data
}

export async function armRobot(robotId: string): Promise<Robot> {
  const { data } = await api.post<Robot>(`/robots/${robotId}/arm`)
  return data
}

export async function disarmRobot(robotId: string): Promise<Robot> {
  const { data } = await api.delete<Robot>(`/robots/${robotId}/arm`)
  return data
}

/** 로봇에 내리는 명령. schemas.py 의 OrderIn 과 같은 목록이다. */
export type RobotCommand =
  | 'goto'
  | 'goto_pose'
  | 'start_session'
  | 'start_guidance'
  | 'cancel_guidance'
  | 'set_mode'
  | 'localize'
  | 'system_start'
  | 'system_stop'
  | 'system_restart'
  | 'fire_alarm_reset'
  | 'set_navigation_speed'

/** mode_manager 가 인정하는 값. 그 외는 로봇이 무시한다. */
export type RobotMode = 'auto' | 'manual' | 'estop'

/**
 * 명령을 큐에 넣는다. **응답은 "로봇이 실행했다" 가 아니라 "적재했다" 다.**
 *
 * 로봇은 몇 초 주기로 폴링해 가져가고, 실행 결과는 이벤트로 돌아온다.
 * 특히 set_mode 는 로봇이 정본을 갖는 요청이므로, 화면은 응답이 아니라
 * robot.mode_changed 이벤트를 보고 상태를 갱신해야 한다.
 */
export async function sendOrder(
  robotId: string,
  command: RobotCommand,
  argument: string,
): Promise<void> {
  await api.post(`/robots/${robotId}/orders`, { command, argument })
}

export interface WaypointValue {
  x: number
  y: number
  yaw: number
}

export interface WaypointSet {
  map_name: string
  visit_waypoints: Record<string, Record<string, string | null>>
  waypoints: Record<string, WaypointValue>
}

export interface WaypointCheckItem {
  name: string
  status: 'ok' | 'warning' | 'blocked' | 'outside'
  clearance: number | null
  message: string
}

export interface WaypointCheckResult {
  ok: boolean
  items: WaypointCheckItem[]
  conflicts: { first: string; second: string; distance: number }[]
}

export async function getWaypoints(): Promise<WaypointSet> {
  const { data } = await api.get<WaypointSet>('/waypoints')
  return data
}

export async function checkWaypoints(
  waypoints: Record<string, WaypointValue>,
): Promise<WaypointCheckResult> {
  const { data } = await api.post<WaypointCheckResult>('/waypoints/check', { waypoints })
  return data
}

/**
 * 세션이 왜 그렇게 끝났는지 — 종료 직전 60초 창의 이벤트.
 * schemas.py 의 SessionEndingContextOut 과 1:1.
 */
export interface SessionEndingContext {
  session_id: number
  ended_at: string | null
  end_reason: string | null
  /** 창 안에서 가장 이른 경고/오류. 인과의 시작점이다. */
  lead_event_code: string | null
  lead_event_at: string | null
  /** 그 경고가 종료보다 몇 초 앞섰는가. */
  lead_sec: number | null
  /** 발생 순(ASC). 인과는 시간 순으로 읽는다. */
  events: EventOut[]
}

export async function getSessionEndingContext(
  sessionId: number,
  options: { signal?: AbortSignal } = {},
): Promise<SessionEndingContext> {
  const { data } = await api.get<SessionEndingContext>(
    `/sessions/${sessionId}/ending-context`,
    { signal: options.signal },
  )
  return data
}

/**
 * 로봇에서 실제로 돌고 있는 것 — 실행 코드 버전과 노드 목록.
 *
 * 한 번도 보고하지 않은 로봇은 404 다. 호출부가 "아직 없음" 과 "실패" 를
 * 구분해야 한다 — 전자는 게이트웨이가 구버전이거나 인벤토리를 끈 것이다.
 */
export async function getRobotInventory(
  robotId: string,
  options: { signal?: AbortSignal } = {},
): Promise<RobotInventory | null> {
  try {
    const { data } = await api.get<RobotInventory>(
      `/robots/${encodeURIComponent(robotId)}/inventory`,
      { signal: options.signal },
    )
    return data
  } catch (error) {
    if ((error as { response?: { status?: number } })?.response?.status === 404) {
      return null
    }
    throw error
  }
}

/**
 * 충전/방전 예상. schemas.py 의 BatteryForecastOut 과 1:1.
 *
 * **seconds 가 null 인 경우가 정상적으로 흔하다.** 부하가 출렁이거나
 * 표본이 모자라면 서버가 시간을 내지 않는다 — 틀린 시간은 없는 시간보다
 * 나쁘기 때문이다. 그때는 direction 만 써서 "충전 중" 을 표시한다.
 */
export interface BatteryForecast {
  robot_id: string
  direction: 'charging' | 'discharging' | 'idle' | 'unknown'
  seconds: number | null
  slope_v_per_hour: number | null
  r_squared: number | null
  sample_count: number
  reason: string | null
}

export async function getBatteryForecast(
  robotId: string,
  options: { signal?: AbortSignal } = {},
): Promise<BatteryForecast> {
  const { data } = await api.get<BatteryForecast>(
    `/robots/${encodeURIComponent(robotId)}/battery-forecast`,
    { signal: options.signal },
  )
  return data
}
