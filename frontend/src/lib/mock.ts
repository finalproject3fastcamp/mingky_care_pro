import type { RobotStatus } from '../types/monitoring'

// 로봇 실시간 상태(state/목적지/ETA)는 아직 백엔드 API 가 없어 mock 으로
// 남겨둔다. 환자/스케줄은 /sessions/active, 알림은 /events 로 대체됐다.

const status: RobotStatus = {
  state: '안내중',
  battery: 74,
  current_destination: '임상병리실',
  eta_seconds: 42,
}

const delay = <T>(value: T, ms = 200): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(value), ms))

export const mockApi = {
  getRobotStatus: () => delay(status),
}
