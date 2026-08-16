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
 * 로봇 종류와 무관하게 성립하는 것. schemas.py 의 RobotBase 와 1:1.
 *
 * §4.3 의 공통 축이다 — 생존·형상·리소스. 배터리도 세션도 여기 없다.
 */
interface RobotCommon {
  robot_id: string
  display_name: string
  domain_id: number | null
  is_active: boolean
  last_seen_at: string | null
  link_state: 'online' | 'offline' | 'unknown'
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

/**
 * 토픽 하나의 나이와 판정. schemas.py 의 TopicAgeOut 과 1:1 (§7.2).
 *
 * **state 를 프론트가 다시 계산하지 않는다.** 임계는 서버의
 * config/topic_watch.yaml 에 있고, 화면이 자기 숫자로 색을 칠하면 설정을
 * 고쳐도 화면 색이 안 바뀐다.
 */
export interface TopicAge {
  topic: string
  /** 마지막 수신으로부터 경과. 한 번도 못 받았으면 감시 시작부터 잰 값이다. */
  age_sec: number | null
  /** 측정 주기. 표본이 하나뿐이면 null 이다 — 0 과 구분해야 한다. */
  hz: number | null
  expected_hz: number | null
  /**
   * fresh 정상 · slow 늦음 · stale 사실상 끊김 ·
   * idle 상시 발행이 아닌 토픽이 쉬는 중(정상) ·
   * missing 로봇이 나이를 못 냄 · unwatched 감시 노드가 안 보고 있음 ·
   * unrated 정본에 임계가 없어 판정하지 않음
   */
  state: 'fresh' | 'slow' | 'stale' | 'idle' | 'missing' | 'unwatched' | 'unrated'
  /** 끊기면 이벤트를 발행하는 상시 토픽인가. */
  always_on: boolean
  /** 이 토픽이 끊기면 무엇이 안 되는가. 문구도 서버가 준다. */
  why: string
}

/**
 * 주행 로봇(핑키). schemas.py 의 MobileRobotOut 과 1:1.
 *
 * 배터리는 2분 주기 로그의 최신값이지 실시간이 아니다.
 * battery_recorded_at 을 함께 보여줘야 사용자가 stale 인지 알 수 있다.
 *
 * armed_at 은 DB 컬럼이 아니라 백엔드 인메모리 (app/arming.py) 다.
 * 세션 시작 전 의료진이 "이 로봇 쓰겠다" 를 표시한 시각.
 */
export interface MobileRobot extends RobotCommon {
  robot_type: 'mobile'
  battery_voltage: number | null
  battery_percent: number | null
  battery_recorded_at: string | null
  active_session_id: number | null
  active_patient_id: string | null
  last_session_ended_at: string | null
  last_session_end_reason: string | null
  armed_at: string | null
  system_state: 'active' | 'activating' | 'deactivating' | 'inactive' | 'failed' | 'unknown'
  localization_active: boolean
  fire_alarm_active: boolean | null
  returning_to_dock: boolean
  navigation_speed_mps: number | null
  guide_robot_state:
    | 'idle' | 'moving' | 'waiting' | 'charging' | 'battery_low'
    | 'comm_lost' | 'paused' | 'returning_to_dock' | null
  /**
   * 토픽 주기 감시 (§7.2). ROS 개념이라 팔에는 없다.
   *
   * 빈 배열은 "이 게이트웨이가 토픽을 감시하지 않는다" 는 뜻이지 토픽이
   * 죽었다는 뜻이 아니다. 화면이 그 둘을 구분해서 그려야 한다.
   * 나쁜 것부터 정렬돼 온다 — 다시 정렬하지 않는다.
   */
  topics: TopicAge[]
}

/** Dynamixel 이 직접 보고한 하드웨어 에러 (§4.4). */
export interface ServoFault {
  joint: string
  fault_bits: number | null
  temp_c: number | null
  /** 해소를 알리는 이벤트가 없다. 나이를 보여주지 않으면 3일 전 결함이 방금
   *  일어난 것처럼 보인다. */
  occurred_at: string
}

/** 팔 전용 지표. schemas.py 의 ManipulatorDetail 과 1:1 (§7.2 · §7.3). */
export interface ManipulatorDetail {
  policy_checkpoint_id: string | null
  policy_dataset_revision: string | null
  policy_loaded_at: string | null
  /** 시작만 하고 아직 끝나지 않은 사이클. null 이면 유휴다. */
  active_dispense_id: string | null
  active_started_at: string | null
  /** 사람이 홈 복귀를 지시해야 다음 사이클이 시작된다 (§4.4). */
  homing_required: boolean
  homing_reason: string | null
  last_servo_fault: ServoFault | null
  window: number
  /** 창을 못 채운 표본. 2건에 1건 실패를 50% 로 그리면 안 된다. */
  window_cycles: number
  sample_complete: boolean
  cycles_completed: number
  cycles_aborted: number
  last_cycle_ended_at: string | null
  pick_succeeded: number
  pick_failed: number
  /** 재시도로 성공한 건. 한 번에 집은 것과 구분해야 정책 품질이 보인다. */
  pick_retried: number
  /** 표본이 0 이면 null 이다. 0 과 구분해서 그려야 한다. */
  pick_success_rate: number | null
  cycle_time_p50_ms: number | null
  cycle_time_p95_ms: number | null
}

/**
 * 조제 로봇(OMX). schemas.py 의 ManipulatorRobotOut 과 1:1.
 *
 * arming 도 세션도 배터리도 없다. 없는 필드를 null 로 받지 않는 것이 이
 * 분리의 전부다 — 팔에 배터리 카드를 그리는 실수가 컴파일 타임에 걸린다.
 */
export interface ManipulatorRobot extends RobotCommon {
  robot_type: 'manipulator'
  detail: ManipulatorDetail
}

/**
 * GET /robots 응답 한 건. robot_type 이 discriminant 다 (§7.3).
 *
 * 런타임 `if (robot.robot_type === 'mobile')` 로는 "팔에 배터리 카드를 렌더"
 * 하는 실수가 안 잡히지만, 유니온으로 두면 컴파일 타임에 잡힌다.
 */
export type Robot = MobileRobot | ManipulatorRobot

/**
 * 타입 가드. `.filter(isMobile)` 로 쓰면 걸러진 배열이 MobileRobot[] 이 된다.
 *
 * `.filter((r) => r.robot_type === 'mobile')` 는 TS 가 Robot[] 로 남겨서
 * 배터리를 읽는 순간 다시 에러가 난다.
 */
export function isMobile(robot: Robot): robot is MobileRobot {
  return robot.robot_type === 'mobile'
}

export function isManipulator(robot: Robot): robot is ManipulatorRobot {
  return robot.robot_type === 'manipulator'
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
