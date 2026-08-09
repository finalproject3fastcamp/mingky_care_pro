export type RobotState =
  | '대기'
  | 'QR 인식'
  | '환자 확인'
  | '안내중'
  | '도착'
  | '검사중'
  | '완료'
  | 'QR 인식 실패'
  | '경로 이탈'
  | '통신 두절'
  | '배터리 부족'
  | '일시정지'

export interface Patient {
  patient_id: string
  name: string
  age: number
  gender: string
  birth_date: string
  condition_name: string
}

export interface SessionStep {
  step_order: number
  visit_name: string
  arrived_at: string | null
  completed_at: string | null
  completed_source: string | null
}

export interface ActiveSession {
  session_id: number
  robot_id: string
  marker_id: number | null
  started_at: string
  ended_at: string | null
  end_reason: string | null
  patient: Patient
  steps: SessionStep[]
  current_step_order: number | null
  current_visit: string | null
}

/**
 * GET /robots 응답. schemas.py 의 RobotOut 와 1:1.
 *
 * 배터리는 2분 주기 로그의 최신값이지 실시간이 아니다.
 * battery_recorded_at 을 함께 보여줘야 사용자가 stale 인지 알 수 있다.
 *
 * armed_at 은 DB 컬럼이 아니라 백엔드 인메모리 (app/arming.py) 다.
 * 세션 시작 전 의료진이 "이 로봇 쓰겠다" 를 표시한 시각.
 */
export interface Robot {
  robot_id: string
  robot_type: string
  display_name: string
  domain_id: number | null
  is_active: boolean
  battery_voltage: number | null
  battery_percent: number | null
  battery_recorded_at: string | null
  active_session_id: number | null
  active_patient_id: string | null
  armed_at: string | null
  last_seen_at: string | null
  link_state: 'online' | 'offline' | 'unknown'
}

export type NotificationLevel = 'info' | 'warning' | 'error'

export interface NotificationEvent {
  id: string
  level: NotificationLevel
  message: string
  created_at: string
}
