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

export interface RobotStatus {
  state: RobotState
  battery: number
  current_destination: string | null
  eta_seconds: number | null
}

export type NotificationLevel = 'info' | 'warning' | 'error'

export interface NotificationEvent {
  id: string
  level: NotificationLevel
  message: string
  created_at: string
}
