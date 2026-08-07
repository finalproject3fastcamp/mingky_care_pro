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
