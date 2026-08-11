import axios from 'axios'

import type { ActiveSession, Robot } from '../types/monitoring'

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
  | 'set_mode'

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
