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
  last_session_ended_at: string | null
  last_session_end_reason: string | null
  armed_at: string | null
  last_seen_at: string | null
  link_state: 'online' | 'offline' | 'unknown'
  system_state: 'active' | 'activating' | 'deactivating' | 'inactive' | 'failed' | 'unknown'
  localization_active: boolean
  runtime_reported_at: string | null
  /**
   * 로봇이 heartbeat 로 보고한 자원·큐 상태. 전부 백엔드 인메모리다.
   *
   * 구버전 게이트웨이는 안 보내므로 null 이 정상이다. **null 과 0 을
   * 구분해서 그려야 한다** — "보고 안 함" 과 "0건" 은 다른 사실이다.
   */
  cpu_total_pct: number | null
  queue_pending: number | null
  max_node_cpu_pct: number | null
  max_node_cpu_name: string | null
  inventory_hash: string | null
}

/** GET /robots/{id}/inventory 응답. schemas.py 의 RobotInventoryOut 과 1:1. */
export interface NodeGraphInfo {
  name: string
  namespace: string
  count: number
}

export interface ProcessInfo {
  pid: number
  install_path: string
  workspace_path: string | null
  /** cmdline 리매핑에서 **추정한** 이름. 중복 판정에는 쓰지 않는다. */
  matched_node_names: string[]
  cpu_pct: number | null
  /** 누적 CPU 초. 순간 100% 는 정상일 수 있지만 11시간 누적은 아니다. */
  cpu_seconds_total: number | null
}

export interface WorkspaceInfo {
  path: string
  commit: string | null
  branch: string | null
  /** 커밋 안 된 변경이 있으면 커밋 해시만으로 재현이 불가능하다. */
  dirty: boolean
  process_count: number
}

export interface DuplicateNode {
  name: string
  namespace: string
  count: number
  severity: 'error' | 'warning'
  reason: string
}

export interface RobotInventory {
  robot_id: string
  inventory_hash: string
  reported_at: string
  node_graph: NodeGraphInfo[]
  processes: ProcessInfo[]
  workspaces: WorkspaceInfo[]
  ros_domain_id: number | null
  /** 심각도 판정은 서버가 한다. 프론트가 다시 하면 두 곳이 어긋난다. */
  duplicates: DuplicateNode[]
  mixed_workspaces: boolean
}

export interface QrObservation {
  robot_id: string
  visible: boolean
  distance: number | null
  observed_at: string | null
}

export type NotificationLevel = 'info' | 'warning' | 'error'

export interface NotificationEvent {
  id: string
  level: NotificationLevel
  message: string
  created_at: string
}
