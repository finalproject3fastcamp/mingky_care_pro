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

export interface ExamStep {
  examination_step_id: number
  step_order: number
  examination_name: string
}

export interface TodaySchedule {
  patient: Patient
  steps: ExamStep[]
  current_step_order: number
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
