"""안내 상태를 소유하는 노드.

프로젝트에서 상태를 소유하는 노드는 이것 하나뿐이다.
다른 노드는 관측만 발행하고, 그 관측이 무엇을 뜻하는지는 여기서 판정한다.

예) 마커가 안 보인다는 관측은
      안내 중(moving)  이면  환자를 놓친 것    → patient.lost
      대기 중(waiting) 이면  환자가 입실한 것  → 정상
    같은 관측이라도 상태에 따라 의미가 반대다. 그래서 검출 노드가 아니라
    상태를 아는 여기서 해석한다.

현재 범위
    - 상태 소유와 GuideState 발행
    - BatteryGuard 판정에 따른 세션 종료와 충전소 복귀
    - Nav2 NavigateToPose 액션 클라이언트, waypoint 로드
    - QR 세션 확인 후 관제 출발 명령으로 첫 방문지 주행
    - 세션 시작/단계 완료를 토픽으로 수동 트리거 (QR·마커 노드가 붙기 전까지)
"""

import json
import math
import threading
import time
from pathlib import Path
from typing import Mapping

import rclpy
import yaml
from action_msgs.msg import GoalStatus
from ament_index_python.packages import (
    PackageNotFoundError,
    get_package_share_directory,
)
from geometry_msgs.msg import PoseStamped
from mingky_interfaces.msg import GuideState, SessionStart
from mingky_smart_recovery.selector import (
    EscapeCandidate,
    candidate_to_map,
    select_diverse_candidates,
    select_escape_candidates,
)
from nav2_msgs.action import ComputePathToPose, DriveOnHeading, NavigateToPose, Spin
from rcl_interfaces.msg import SetParametersResult
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan, Range
from std_msgs.msg import Bool, Float32, String
from std_srvs.srv import SetBool

from .arrival_chime import play_arrival_chime
from .event_publisher import EventPublisher
from .low_obstacle import (
    LowObstacleConfig,
    SidestepOutcome,
    is_low_obstacle,
    lidar_sector_min_range,
)
from .low_obstacle_driver import SidestepActionDriver


def _yaw_to_quat(yaw: float):
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def _quat_to_yaw(z: float, w: float) -> float:
    return math.atan2(2.0 * w * z, 1.0 - 2.0 * z * z)


class GuideManager(Node):

    def __init__(self, **kwargs):
        # kwargs 는 테스트에서 parameter_overrides 를 넣기 위한 통로다.
        super().__init__('guide_manager', **kwargs)

        self.declare_parameter('robot_id', 'pinky-01')
        self.declare_parameter('event_codes_file', '')
        self.declare_parameter('waypoints_file', '')
        # 좌표 파일은 맵마다 다르다. 맵의 origin 이나 resolution 이 바뀌면
        # 같은 좌표가 전혀 다른 물리 위치를 가리키기 때문이다.
        # Nav2 가 띄운 맵과 반드시 같은 이름을 줘야 한다.
        self.declare_parameter('map_name', 'yun_map_highres_clean')
        # 비우면 robot_id 의 숫자 접미사를 사용한다.
        # pinky-01 -> charging_station_1, pinky-02 -> charging_station_2
        self.declare_parameter('charging_waypoint', '')
        self.declare_parameter('dock_max_attempts', 3)
        self.declare_parameter('dock_retry_delay_sec', 5.0)
        # default 는 기존 Nav2 동작을 그대로 유지한다. adaptive 를 명시한 로봇만
        # LiDAR 후보 생성 -> 경로 검증 -> 임시 탈출 지점 이동을 사용한다.
        self.declare_parameter('recovery_mode', 'default')
        self.declare_parameter('planner_mode', 'navfn')
        self.declare_parameter('recovery_scan_topic', '/scan')
        self.declare_parameter('recovery_scan_stale_sec', 1.0)
        self.declare_parameter('recovery_candidate_limit', 4)
        self.declare_parameter('recovery_candidate_separation_deg', 30.0)
        self.declare_parameter('recovery_retry_delay_sec', 0.3)
        self.declare_parameter('arrival_notice_sec', 3.0)
        self.declare_parameter('use_arrival_chime', True)
        # disabled 는 기존 동작을 그대로 유지한다. range_layer 는 후속 검증 뒤
        # 추가하고, 현재는 실로봇에서 검증한 sidestep 만 선택할 수 있다.
        self.declare_parameter('low_obstacle_mode', 'disabled')
        self.declare_parameter('low_obstacle_range_topic', '/us_sensor/range')
        self.declare_parameter('low_obstacle_scan_stale_sec', 1.0)
        self.declare_parameter('low_obstacle_confirmations', 2)
        self.declare_parameter('low_obstacle_max_sidesteps', 3)
        self.declare_parameter('low_obstacle_trigger_distance', 0.10)
        self.declare_parameter('low_obstacle_lidar_margin', 0.15)
        self.declare_parameter('low_obstacle_lidar_front_center_deg', 180.0)
        self.declare_parameter('low_obstacle_lidar_half_width_deg', 15.0)
        self.declare_parameter('low_obstacle_probe_step_deg', 10.0)
        self.declare_parameter('low_obstacle_probe_max_steps', 4)
        self.declare_parameter('low_obstacle_probe_clearance', 0.45)
        self.declare_parameter('low_obstacle_body_margin_deg', 15.0)
        self.declare_parameter('low_obstacle_drive_step', 0.08)
        self.declare_parameter('low_obstacle_drive_total', 0.35)
        self.declare_parameter('low_obstacle_drive_speed', 0.12)
        self.declare_parameter('low_obstacle_min_drive_clearance', 0.04)
        self.declare_parameter('low_obstacle_range_timeout_sec', 0.8)
        # 환자 거리 관측은 옵션이며, 통합 launch에서 노드와 함께 켠다.
        self.declare_parameter('patient_follow_enabled', False)
        self.declare_parameter('patient_follow_timeout_sec', 2.0)

        self.robot_id = self.get_parameter('robot_id').value
        configured_dock = self.get_parameter('charging_waypoint').value.strip()
        self.charging_waypoint = configured_dock or self._default_charging_waypoint()
        self.dock_max_attempts = max(
            1, int(self.get_parameter('dock_max_attempts').value))
        self.dock_retry_delay = max(
            0.1, float(self.get_parameter('dock_retry_delay_sec').value))
        self.recovery_mode = str(self.get_parameter('recovery_mode').value).lower()
        if self.recovery_mode not in ('default', 'adaptive'):
            self.get_logger().warn(
                f'알 수 없는 recovery_mode={self.recovery_mode!r}; default 를 사용합니다.')
            self.recovery_mode = 'default'
        self.planner_mode = str(self.get_parameter('planner_mode').value).lower()
        if self.planner_mode not in ('navfn', 'smac2d'):
            self.get_logger().warn(
                f'알 수 없는 planner_mode={self.planner_mode!r}; navfn 을 사용합니다.')
            self.planner_mode = 'navfn'
        self._behavior_tree_dir = self._find_behavior_tree_dir()
        if self._behavior_tree_dir is None:
            if self.recovery_mode == 'adaptive':
                self.get_logger().error(
                    '적응형 복구 파일이 없어 recovery_mode=default 로 전환합니다.')
                self.recovery_mode = 'default'
            if self.planner_mode == 'smac2d':
                self.get_logger().error(
                    'Smac2D 선택 파일이 없어 planner_mode=navfn 으로 전환합니다.')
                self.planner_mode = 'navfn'
        self.recovery_scan_stale_sec = max(
            0.1, float(self.get_parameter('recovery_scan_stale_sec').value))
        self.recovery_candidate_limit = max(
            1, int(self.get_parameter('recovery_candidate_limit').value))
        self.recovery_candidate_separation_rad = math.radians(max(
            0.0,
            min(180.0, float(self.get_parameter(
                'recovery_candidate_separation_deg').value)),
        ))
        self.recovery_retry_delay_sec = max(
            0.1, float(self.get_parameter('recovery_retry_delay_sec').value))
        self.arrival_notice_sec = max(
            0.0, float(self.get_parameter('arrival_notice_sec').value))
        self.use_arrival_chime = bool(
            self.get_parameter('use_arrival_chime').value)
        self.low_obstacle_mode = str(
            self.get_parameter('low_obstacle_mode').value).lower()
        if self.low_obstacle_mode not in ('disabled', 'sidestep'):
            self.get_logger().warn(
                f'지원하지 않는 low_obstacle_mode={self.low_obstacle_mode!r}; '
                'disabled 를 사용합니다.')
            self.low_obstacle_mode = 'disabled'
        self.low_obstacle_scan_stale_sec = max(
            0.1, float(self.get_parameter(
                'low_obstacle_scan_stale_sec').value))
        self.low_obstacle_confirmations = max(
            1, int(self.get_parameter('low_obstacle_confirmations').value))
        self.low_obstacle_max_sidesteps = max(
            1, int(self.get_parameter('low_obstacle_max_sidesteps').value))
        self.low_obstacle_config = LowObstacleConfig(
            trigger_distance_m=float(self.get_parameter(
                'low_obstacle_trigger_distance').value),
            lidar_margin_m=float(self.get_parameter(
                'low_obstacle_lidar_margin').value),
            lidar_front_center_deg=float(self.get_parameter(
                'low_obstacle_lidar_front_center_deg').value),
            lidar_half_width_deg=float(self.get_parameter(
                'low_obstacle_lidar_half_width_deg').value),
            probe_step_deg=float(self.get_parameter(
                'low_obstacle_probe_step_deg').value),
            probe_max_steps=max(1, int(self.get_parameter(
                'low_obstacle_probe_max_steps').value)),
            probe_clearance_m=float(self.get_parameter(
                'low_obstacle_probe_clearance').value),
            body_clearance_margin_deg=float(self.get_parameter(
                'low_obstacle_body_margin_deg').value),
            drive_step_m=float(self.get_parameter(
                'low_obstacle_drive_step').value),
            drive_total_m=float(self.get_parameter(
                'low_obstacle_drive_total').value),
            drive_speed_mps=float(self.get_parameter(
                'low_obstacle_drive_speed').value),
            minimum_drive_clearance_m=float(self.get_parameter(
                'low_obstacle_min_drive_clearance').value),
        )
        self.patient_follow_enabled = bool(
            self.get_parameter('patient_follow_enabled').value)
        self.patient_follow_timeout_sec = max(
            0.5,
            float(self.get_parameter('patient_follow_timeout_sec').value),
        )

        self.events = EventPublisher(
            self, self.robot_id, self.get_parameter('event_codes_file').value)

        self.visit_waypoints = {}
        self.waypoints = self._load_waypoints(
            self.get_parameter('waypoints_file').value,
            self.get_parameter('map_name').value)

        # --- 상태. 이 노드만 쓴다 ---
        self.robot_state = GuideState.ROBOT_IDLE
        self.session_state = GuideState.SESSION_NONE
        self.session_id = 0
        self.patient_id = ''
        self.current_step_order = 0
        self.previous_visit = ''
        self.current_visit = ''
        self.voltage = float('nan')
        self.percent = -1
        self._battery_alarm = False
        self._emergency_engaged = False
        self._emergency_reason = 'emergency_stop'
        # 저전압과 정상 안내 완료가 같은 충전소 복귀 경로를 공유한다.
        # 사유를 보관해야 배터리 경보가 없어도 재시도와 안전 보류가 가능하다.
        self._dock_reason = None
        self._dock_pending = False
        self._dock_attempt = 0
        self._dock_retry_timer = None
        self._latest_scan = None
        self._latest_scan_received_ns = 0
        self._latest_nav_pose = None
        self._latest_nav_pose_received_ns = 0
        self._adaptive_retry_timer = None
        self._arrival_notice_timer = None
        self._pending_waiting_move = None
        self._maintenance_nav_active = False
        self._localization_active = False
        self._fire_evacuating = False
        self._active_nav_goal_handle = None
        self._active_nav_result_future = None
        self._active_nav_context = None
        self._pending_low_obstacle_context = None
        self._low_obstacle_confirmed_count = 0
        self._patient_follow_state = 'inactive'
        self._patient_follow_last_at = 0.0
        self._patient_wait_context = None
        self._patient_wait_cancel_pending = False
        self._patient_resume_pending = False
        self._patient_pause_requested = False
        # 새 목표가 이전 목표를 선점했을 때 늦게 도착한 콜백이 상태를 되돌리지
        # 못하도록 세대 번호를 붙인다.
        self._nav_generation = 0

        # 상태 토픽은 늦게 뜬 구독자(LCD, 게이트웨이)도 마지막 값을 받아야 한다.
        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.state_pub = self.create_publisher(GuideState, '~/state', state_qos)
        self.navigation_cancel_pub = self.create_publisher(
            Bool, '/navigation_manager/cancel', 10)
        self.obstacle_stop_pub = self.create_publisher(
            Bool, '/emergency_stop/obstacle', 10)

        self.create_subscription(Float32, '/battery/voltage', self._on_voltage, 10)
        self.create_subscription(Float32, '/battery/percent', self._on_percent, 10)
        battery_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(Bool, '/battery/low', self._on_battery_low, battery_qos)
        self.create_subscription(
            String, '/emergency_stop/reason', self._on_emergency_reason, state_qos)
        self.create_subscription(
            Bool, '/emergency_stop/state', self._on_emergency_state, state_qos)
        self.create_subscription(
            LaserScan,
            str(self.get_parameter('recovery_scan_topic').value),
            self._on_scan,
            10,
        )
        range_qos = QoSProfile(depth=5)
        range_qos.reliability = ReliabilityPolicy.BEST_EFFORT
        self.create_subscription(
            Range,
            str(self.get_parameter('low_obstacle_range_topic').value),
            self._on_low_obstacle_range,
            range_qos,
        )
        self.create_subscription(
            Bool, '/navigation_manager/active',
            self._on_navigation_active, state_qos)
        self.create_subscription(
            String, '/navigation_manager/result',
            self._on_navigation_result, 10)
        self.create_subscription(
            Bool, '/auto_localize/active',
            self._on_localization_active, state_qos)
        self.create_subscription(
            String, '/person_follow/state',
            self._on_patient_follow_state, state_qos)
        self.create_service(
            SetBool, '~/fire_evacuation', self._on_fire_evacuation)

        # QR·마커 노드가 붙기 전까지 손으로 흘려넣기 위한 입구.
        # 나중에 그 노드들이 대체하면 이 두 개는 지운다.
        self.create_subscription(String, '~/start_session', self._on_start_session, 10)

        # 관제 버튼이 생기기 전에는 ros2 topic pub 으로 같은 명령을 시험한다.
        # session_id 를 함께 받아 오래된 화면의 출발 요청이 새 세션을 움직이지
        # 못하게 한다.
        self.create_subscription(
            String, '~/start_guidance', self._on_start_guidance, 10)
        self.create_subscription(
            String, '~/cancel_session', self._on_cancel_session, 10)

        # QR 노드가 백엔드 응답을 파싱해 흘려주는 세션 신호.
        # 최초 QR은 관제 출발을 기다리고, waiting spot에서 받은
        # 같은 세션 QR은 현재 검사를 완료하고 다음 안내를 시작한다.
        # session_start 자체는 이벤트가 아니라 로봇 내부 배관 신호라 events 로는 쏘지 않는다.
        # 상응하는 session.started 는 백엔드가 POST /qr/scan 안에서 이미 반영한 뒤라 중복이다.
        self.session_visits: list[str] = []
        self.create_subscription(
            SessionStart, '/qr_reader_node/session_start', self._on_session_start, 10)

        self.nav = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.path_planner = ActionClient(
            self, ComputePathToPose, 'compute_path_to_pose')
        self.low_obstacle_spin = ActionClient(self, Spin, 'spin')
        self.low_obstacle_drive = ActionClient(
            self, DriveOnHeading, 'drive_on_heading')
        self.low_obstacle_driver = SidestepActionDriver(
            self,
            self.low_obstacle_spin,
            self.low_obstacle_drive,
            self.low_obstacle_config,
            self._on_low_obstacle_sidestep_complete,
            range_timeout_sec=float(self.get_parameter(
                'low_obstacle_range_timeout_sec').value),
        )
        self.add_on_set_parameters_callback(self._on_set_parameters)

        self.create_timer(1.0, self._publish_state)
        self.create_timer(0.5, self._check_patient_follow_timeout)
        self.get_logger().info(
            f'guide_manager 시작 (robot_id={self.robot_id}, '
            f'waypoint {len(self.waypoints)}개, 충전소={self.charging_waypoint}, '
            f'recovery={self.recovery_mode}, planner={self.planner_mode}, '
            f'low_obstacle={self.low_obstacle_mode})')

    # ------------------------------------------------------------------ 설정

    def _on_set_parameters(self, parameters) -> SetParametersResult:
        requested_mode = next(
            (str(parameter.value).lower() for parameter in parameters
             if parameter.name == 'low_obstacle_mode'),
            None,
        )
        if requested_mode is None:
            return SetParametersResult(successful=True)
        if requested_mode not in ('disabled', 'sidestep'):
            return SetParametersResult(
                successful=False,
                reason='low_obstacle_mode은 disabled 또는 sidestep이어야 합니다.',
            )
        if (
                self._active_nav_context is not None
                or self._pending_low_obstacle_context is not None
                or self.low_obstacle_driver.active
                or self.robot_state == GuideState.ROBOT_MOVING):
            return SetParametersResult(
                successful=False,
                reason='주행 중에는 저상 장애물 모드를 변경할 수 없습니다.',
            )
        self.low_obstacle_mode = requested_mode
        self._low_obstacle_confirmed_count = 0
        self.get_logger().info(
            f'저상 장애물 대응 모드 변경: {self.low_obstacle_mode}')
        return SetParametersResult(successful=True)

    def _find_behavior_tree_dir(self) -> Path | None:
        try:
            directory = Path(get_package_share_directory(
                'mingky_smart_recovery')) / 'behavior_trees'
            if directory.is_dir():
                return directory
        except PackageNotFoundError:
            pass
        for parent in Path(__file__).resolve().parents:
            directory = parent / 'mingky_smart_recovery' / 'behavior_trees'
            if directory.is_dir():
                return directory
        self.get_logger().error('적응형 복구 behavior tree 디렉터리를 찾지 못했습니다.')
        return None

    def _behavior_tree_file(self, *, fallback: bool) -> str:
        if self._behavior_tree_dir is None:
            return ''
        if fallback and self.planner_mode == 'navfn':
            # 빈 값은 Nav2의 기존 기본 recovery tree를 뜻한다.
            return ''
        phase = 'recovery' if fallback else 'no_recovery'
        path = self._behavior_tree_dir / f'navigate_{phase}_{self.planner_mode}.xml'
        if path.is_file():
            return str(path)
        self.get_logger().error(f'behavior tree 파일이 없습니다: {path}')
        return ''

    def _waypoint_candidates(self, map_name: str) -> list[Path]:
        """좌표 파일 후보를 우선순위대로 돌려준다.

        파일명이 맵 이름에 묶여 있다(config/waypoints/<맵>_waypoints.yaml).
        고정 이름을 쓰면 맵을 바꿨을 때 예전 좌표를 그대로 읽고, 로봇이 엉뚱한
        곳으로 간다. 이름이 다르면 못 찾고 멈추는 편이 낫다.
        """
        relative = Path('config') / 'waypoints' / f'{map_name}_waypoints.yaml'
        candidates = []
        try:
            candidates.append(
                Path(get_package_share_directory('mingky_bringup')) / relative)
        except PackageNotFoundError:
            pass
        # 빌드하지 않고 소스에서 바로 돌릴 때를 위한 경로.
        for parent in Path(__file__).resolve().parents:
            candidates.append(parent / 'mingky_bringup' / relative)
        return candidates

    def _load_waypoints(self, explicit: str, map_name: str) -> dict:
        """좌표는 맵과 같은 곳(mingky_bringup)에 두고 여기서는 읽기만 한다.

        DB 에 좌표를 넣지 않는 이유는 맵을 다시 만들면 모든 좌표가 무의미해지기
        때문이다. 맵과 waypoint 가 같은 패키지에 있어야 함께 버전 관리된다.
        """
        if explicit:
            path = Path(explicit).expanduser()
            if not path.is_file():
                self.get_logger().error(
                    f'waypoints_file 이 없습니다: {path}. 주행 명령이 동작하지 않습니다.')
                return {}
        else:
            path = next((c for c in self._waypoint_candidates(map_name) if c.is_file()),
                        None)

        if path is None:
            searched = '\n  '.join(str(c) for c in self._waypoint_candidates(map_name))
            self.get_logger().error(
                f"map_name='{map_name}' 의 좌표 파일을 찾지 못했습니다. "
                f'주행 명령이 동작하지 않습니다.\n찾아본 경로:\n  {searched}\n'
                '사용 가능한 맵은 mingky_bringup/config/waypoints/ 를 확인하세요.')
            return {}

        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        waypoints = data.get('waypoints') or {}
        self.visit_waypoints = data.get('visit_waypoints') or {}

        # 파일은 있는데 비어 있는 경우가 제일 찾기 어렵다. 노드는 정상 기동하고
        # 목표를 보낼 때마다 '알 수 없는 waypoint' 만 나온다. 기동 시에 짚는다.
        if not waypoints:
            self.get_logger().error(
                f'{path} 에 waypoint 가 없습니다. 오래된 빌드 산출물일 수 있으니 '
                'install/ 을 지우고 다시 빌드해 보세요.')
        else:
            self.get_logger().info(
                f'waypoints 로드: {path} ({len(waypoints)}개, '
                f'방문지 매핑 {len(self.visit_waypoints)}개)')
        return waypoints

    def _default_charging_waypoint(self) -> str:
        suffix = self.robot_id.rsplit('-', 1)[-1]
        try:
            number = int(suffix)
        except ValueError:
            self.get_logger().warn(
                f'robot_id={self.robot_id!r} 에 숫자 접미사가 없어 '
                'charging_station_1 을 사용합니다.')
            number = 1
        return f'charging_station_{number}'

    # ------------------------------------------------------------------ 배터리

    def _on_voltage(self, msg: Float32):
        # 이 변환은 화면·이벤트 표시 전용이다. 저전압 판정은 BatteryGuard 가
        # 전압으로 하고 결과만 /battery/low 로 받는다. 그래서 이 식이 근사여도
        # 로봇 동작에는 영향이 없다 (mingky_battery_guard/README.md 참고).
        # 패키지 의존을 늘리지 않으려고 공유하지 않고 여기 둔다.
        if math.isnan(msg.data):
            return
        self.voltage = msg.data
        if self.percent < 0:
            self.percent = int(round(max(
                0.0, min(100.0, (self.voltage - 6.8) / (7.6 - 6.8) * 100.0))))

    def _on_percent(self, msg: Float32):
        self.percent = int(round(msg.data))

    def _on_battery_low(self, msg: Bool):
        """BatteryGuard 의 필터링된 상태 변화를 시스템 상태로 해석한다."""
        if msg.data == self._battery_alarm:
            return
        self._battery_alarm = msg.data

        if not msg.data:
            if self._dock_reason == 'battery':
                retry_was_pending = self._dock_retry_timer is not None
                self._cancel_dock_retry()
                if retry_was_pending:
                    self._dock_reason = None
                    self._dock_pending = False
            self.events.publish(
                'robot.battery_recovered', {'percent': max(self.percent, 0)})
            if self.robot_state == GuideState.ROBOT_BATTERY_LOW:
                self.robot_state = GuideState.ROBOT_IDLE
            self.get_logger().info(f'배터리 저전압 상태 해제 ({self.percent}%)')
            return

        self._cancel_arrival_notice()
        self._cancel_adaptive_retry()
        self._cancel_low_obstacle_operation()
        self._cancel_patient_wait_state()
        active_session_id = self.session_id
        self.robot_state = GuideState.ROBOT_BATTERY_LOW
        self.events.publish(
            'robot.battery_low', {'percent': max(self.percent, 0)}, active_session_id)

        if active_session_id > 0 and self.session_state not in (
                GuideState.SESSION_NONE, GuideState.SESSION_COMPLETED):
            self.events.publish(
                'session.ended', {'end_reason': 'battery'}, active_session_id)
            self.session_state = GuideState.SESSION_COMPLETED
            self.get_logger().warn(
                f'배터리 부족으로 세션 {active_session_id} 종료')

        # 이후 충전소 주행 이벤트는 환자 안내 세션과 분리한다.
        self.session_id = 0
        self.session_state = GuideState.SESSION_NONE
        self.patient_id = ''
        self.current_step_order = 0
        self.previous_visit = ''
        self.current_visit = ''
        self._request_dock_return('battery')

    # --------------------------------------------------------------- 비상정지

    def _on_emergency_reason(self, msg: String):
        if msg.data:
            self._emergency_reason = msg.data

    def _on_cancel_session(self, msg: String) -> None:
        """장애 또는 의료진 취소 요청을 받아 안내를 재개 없이 끝낸다."""
        raw = msg.data.strip()
        requested_session_id = 0
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            reason = raw
        else:
            if not isinstance(payload, dict):
                self.get_logger().warn(f'잘못된 세션 취소 요청을 무시합니다: {raw!r}')
                return
            reason = str(payload.get('reason', '')).strip()
            try:
                requested_session_id = int(payload.get('session_id', 0))
            except (TypeError, ValueError):
                self.get_logger().warn(f'잘못된 세션 취소 요청을 무시합니다: {raw!r}')
                return

        if reason not in ('aborted', 'robot_offline', 'system_failure'):
            self.get_logger().warn(f'알 수 없는 세션 취소 사유를 무시합니다: {reason!r}')
            return
        if self.session_id <= 0 or self.session_state in (
                GuideState.SESSION_NONE, GuideState.SESSION_COMPLETED):
            return
        if requested_session_id > 0 and requested_session_id != self.session_id:
            self.get_logger().warn(
                f'현재 세션과 다른 취소 요청을 무시합니다: '
                f'requested={requested_session_id}, current={self.session_id}')
            return

        active_session_id = self.session_id
        self._nav_generation += 1
        self._cancel_low_obstacle_operation()
        self._cancel_patient_wait_state()
        self._cancel_arrival_notice()
        self._cancel_adaptive_retry()
        self._cancel_dock_retry()
        self.navigation_cancel_pub.publish(Bool(data=True))
        self.events.publish(
            'session.ended', {'end_reason': reason}, active_session_id)

        self.session_id = 0
        self.session_state = GuideState.SESSION_NONE
        self.patient_id = ''
        self.current_step_order = 0
        self.previous_visit = ''
        self.current_visit = ''
        self.session_visits = []
        if reason == 'aborted':
            self.get_logger().info(
                f'의료진 요청으로 안내 세션 {active_session_id} 취소; '
                '충전소로 복귀합니다.')
            self._request_dock_return('guidance_canceled')
        else:
            self.robot_state = GuideState.ROBOT_PAUSED
            self.get_logger().error(
                f'장기 장애로 안내 세션 {active_session_id} 취소 ({reason})')

    def _on_fire_evacuation(self, request, response):
        """화재 대피가 안내 상태와 기존 Nav2 콜백을 안전하게 선점하게 한다."""
        active = bool(request.data)
        if active == self._fire_evacuating:
            response.success = True
            response.message = '이미 같은 화재 대피 상태입니다.'
            return response

        self._fire_evacuating = active
        if active:
            active_session_id = self.session_id
            # 이후 도착하는 기존 안내 목표 결과는 상태와 이벤트에 반영하지 않는다.
            self._nav_generation += 1
            self._cancel_low_obstacle_operation()
            self._cancel_patient_wait_state()
            self._cancel_arrival_notice()
            self._cancel_adaptive_retry()
            self._cancel_dock_retry()
            self.navigation_cancel_pub.publish(Bool(data=True))

            if active_session_id > 0 and self.session_state not in (
                    GuideState.SESSION_NONE, GuideState.SESSION_COMPLETED):
                self.events.publish(
                    'session.ended', {'end_reason': 'fire'}, active_session_id)

            self.session_id = 0
            self.session_state = GuideState.SESSION_NONE
            self.patient_id = ''
            self.current_step_order = 0
            self.previous_visit = ''
            self.current_visit = ''
            self.session_visits = []
            self.robot_state = GuideState.ROBOT_PAUSED
            self.get_logger().error('화재 대피 시작: 기존 안내와 예약 주행을 종료합니다.')
            response.message = '기존 안내 상태를 종료하고 화재 대피를 준비했습니다.'
        else:
            self.robot_state = (
                GuideState.ROBOT_PAUSED if self._emergency_engaged
                else GuideState.ROBOT_BATTERY_LOW if self._battery_alarm
                else GuideState.ROBOT_IDLE)
            if self._dock_reason is not None and not self._emergency_engaged:
                self._dock_pending = False
                self._return_to_dock()
            self.get_logger().info('화재 대피 상태가 종료되었습니다.')
            response.message = '화재 대피 상태를 종료했습니다.'

        response.success = True
        return response

    def _on_emergency_state(self, msg: Bool):
        if msg.data == self._emergency_engaged:
            return
        self._emergency_engaged = msg.data

        if msg.data:
            # 진행 중인 Nav2 콜백은 EmergencyStop 이 취소한 뒤 늦게 도착할 수 있다.
            # 세대를 넘겨 그 결과가 현재 상태를 덮어쓰지 못하게 한다.
            self._nav_generation += 1
            self._cancel_low_obstacle_operation()
            self._cancel_patient_wait_state()
            self._cancel_arrival_notice()
            self._cancel_adaptive_retry()
            self._cancel_dock_retry()
            self._dock_pending = self._dock_reason is not None
            self.robot_state = GuideState.ROBOT_PAUSED
            self.events.publish(
                'robot.paused', {'reason': self._emergency_reason}, self.session_id)
            return

        self.events.publish(
            'robot.resumed', {'reason': self._emergency_reason}, self.session_id)
        if self._dock_reason is not None:
            self.robot_state = (
                GuideState.ROBOT_BATTERY_LOW
                if self._dock_reason == 'battery'
                else GuideState.ROBOT_RETURNING_TO_DOCK
                if self._dock_reason == 'guidance_canceled'
                else GuideState.ROBOT_MOVING)
            self._dock_pending = False
            self._return_to_dock()
        else:
            # 취소된 안내 목표는 안전상 자동 재개하지 않는다.
            self.robot_state = GuideState.ROBOT_IDLE
        self._emergency_reason = 'emergency_stop'

    # --------------------------------------------- 비임상 Waypoint 시험 상태

    def _on_navigation_active(self, msg: Bool) -> None:
        self._maintenance_nav_active = bool(msg.data)

    def _on_localization_active(self, msg: Bool) -> None:
        self._localization_active = bool(msg.data)

    def _on_navigation_result(self, msg: String) -> None:
        """navigation_manager의 시험 주행 결과를 관제 이벤트와 상태로 반영한다."""
        try:
            result = json.loads(msg.data)
            status = str(result['status'])
            name = str(result.get('waypoint_name') or 'waypoint_draft')
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            self.get_logger().error(f'시험 주행 결과 형식이 올바르지 않습니다: {exc}')
            return

        if status == 'started':
            self.robot_state = GuideState.ROBOT_MOVING
            self.events.publish('waypoint.test_started', {
                'waypoint_name': name,
                'x': float(result.get('x', 0.0)),
                'y': float(result.get('y', 0.0)),
                'yaw': float(result.get('yaw', 0.0)),
            })
        elif status == 'succeeded':
            self.robot_state = GuideState.ROBOT_WAITING
            self.events.publish(
                'waypoint.test_succeeded', {'waypoint_name': name})
        elif status in ('failed', 'rejected'):
            # 환자 안내가 시작되어 시험 주행이 취소된 경우에는 안내 상태가 정본이다.
            if not self._maintenance_nav_active and self.session_state in (
                    GuideState.SESSION_NONE, GuideState.SESSION_COMPLETED):
                self.robot_state = (
                    GuideState.ROBOT_PAUSED if self._emergency_engaged
                    else GuideState.ROBOT_BATTERY_LOW if self._battery_alarm
                    else GuideState.ROBOT_IDLE)
            self.events.publish('waypoint.test_failed', {
                'waypoint_name': name,
                'error_code': int(result.get('error_code', -4)),
            })

    # ------------------------------------------------------------------ 세션

    def _on_start_session(self, msg: String):
        """임시 입구. 나중에 QR 노드가 대체한다."""
        if self._battery_alarm:
            self.get_logger().warn('배터리 부족 상태에서는 세션을 시작할 수 없습니다.')
            return
        if self._maintenance_nav_active:
            self.navigation_cancel_pub.publish(Bool(data=True))
        self.patient_id = msg.data.strip()
        self.previous_visit = ''
        self.session_state = GuideState.SESSION_CONFIRMED
        self.events.publish(
            'session.started',
            {'patient_id': self.patient_id, 'marker_id': None},
            self.session_id)
        self.get_logger().info(f'세션 시작: {self.patient_id}')

    def _on_start_guidance(self, msg: String) -> None:
        """확인된 현재 세션의 첫 방문지로 출발한다."""
        requested = msg.data.strip()
        try:
            requested_session_id = int(requested)
        except ValueError:
            self._reject_start_guidance(
                'invalid_session_id',
                f'출발 명령의 session_id가 올바르지 않습니다: {requested!r}')
            return
        if self._localization_active:
            self._reject_start_guidance(
                'localization_active',
                'AMCL 자동 재탐색 중에는 환자 안내를 시작할 수 없습니다.')
            return
        if self._dock_reason is not None:
            self._reject_start_guidance(
                'returning_to_dock',
                '충전소 복귀 중에는 환자 안내를 시작할 수 없습니다.')
            return

        if requested_session_id <= 0 or requested_session_id != self.session_id:
            self._reject_start_guidance(
                'session_mismatch',
                f'출발 명령 세션 불일치: 요청={requested_session_id}, '
                f'현재={self.session_id}')
            return
        if self.session_state != GuideState.SESSION_CONFIRMED:
            self._reject_start_guidance(
                'invalid_state',
                f'환자 확인 상태에서만 출발할 수 있습니다: {self.session_state}')
            return
        if self._battery_alarm:
            self._reject_start_guidance(
                'battery_low', '배터리 부족 상태에서는 출발할 수 없습니다.')
            return
        if self._emergency_engaged:
            self._reject_start_guidance(
                'emergency_stop', '비상정지 상태에서는 출발할 수 없습니다.')
            return
        if self._maintenance_nav_active:
            self._reject_start_guidance(
                'waypoint_test_active',
                'Waypoint 시험 주행이 끝나기 전에는 환자 안내를 시작할 수 없습니다.')
            return
        if not self.current_visit:
            self._reject_start_guidance(
                'missing_visit', '현재 세션에 방문할 검사실이 없습니다.')
            return

        waypoint_name = self._visit_waypoint(self.current_visit, 'goal')
        if not waypoint_name:
            self._reject_start_guidance(
                'missing_waypoint',
                f'방문지 goal 매핑이 없습니다: {self.current_visit!r}')
            return

        self.get_logger().info(
            f'안내 출발 승인: session_id={self.session_id} '
            f'{self.current_visit!r} -> {waypoint_name!r}')
        self.send_goal(waypoint_name)

    def _reject_start_guidance(self, reason: str, message: str) -> None:
        self.get_logger().warn(message)
        self.events.publish(
            'session.start_rejected',
            {'reason': reason},
            self.session_id,
        )

    def _on_session_start(self, msg: SessionStart):
        """최초 QR은 세션을 확인하고, 검사실 재스캔은 현재 단계를 완료한다."""
        if self._battery_alarm:
            # QR API가 이미 DB 세션을 만들었으므로 무시만 하면 활성 세션이 남는다.
            self.events.publish(
                'session.ended', {'end_reason': 'battery'}, int(msg.session_id))
            self.get_logger().warn(
                f'배터리 부족으로 새 세션 {msg.session_id} 을 즉시 종료합니다.')
            return

        if self._dock_reason is not None:
            # 백엔드가 복귀 상태를 확인해 세션 생성을 막지만 heartbeat 사이의
            # 짧은 경합까지 로봇에서 한 번 더 방어한다.
            self.events.publish(
                'session.ended', {'end_reason': 'aborted'}, int(msg.session_id))
            self.get_logger().warn(
                f'충전소 복귀 중 생성된 세션 {msg.session_id} 을 종료합니다.')
            return

        incoming_session_id = int(msg.session_id)
        incoming_patient_id = str(msg.patient_id)
        if incoming_session_id <= 0 or not incoming_patient_id:
            self.get_logger().error(
                f'유효하지 않은 session_start: session_id={incoming_session_id} '
                f'patient_id={incoming_patient_id!r}')
            return

        if self._maintenance_nav_active:
            self.navigation_cancel_pub.publish(Bool(data=True))
            self.get_logger().warn(
                '환자 세션 확인을 위해 진행 중인 Waypoint 시험 주행을 취소합니다.')

        active_states = (
            GuideState.SESSION_CONFIRMED,
            GuideState.SESSION_GUIDING,
            GuideState.SESSION_ARRIVED,
            GuideState.SESSION_IN_ROOM,
        )
        if self.session_id > 0 and self.session_state in active_states:
            if (incoming_session_id != self.session_id
                    or incoming_patient_id != self.patient_id):
                self.get_logger().error(
                    '활성 안내와 다른 QR 세션을 거부합니다: '
                    f'현재={self.session_id}/{self.patient_id}, '
                    f'요청={incoming_session_id}/{incoming_patient_id}')
                return
            if self.session_state == GuideState.SESSION_IN_ROOM:
                self._complete_current_step_from_qr()
            else:
                self.get_logger().info(
                    f'진행 중 세션의 중복 QR 무시: session_id={self.session_id} '
                    f'state={self.session_state}')
            return

        self.session_id = incoming_session_id
        self.patient_id = incoming_patient_id
        self.session_visits = list(msg.visit_names)
        # 0은 백엔드가 "남은 단계 없음"을 나타내는 값이다. 0을 첫 단계로
        # 보정하면 완료된 세션을 재스캔했을 때 첫 검사실로 다시 출발한다.
        self.current_step_order = int(msg.current_step_order)
        idx = self.current_step_order - 1
        self.previous_visit = (
            self.session_visits[idx - 1]
            if 0 < idx < len(self.session_visits) else '')
        self.current_visit = (
            self.session_visits[idx] if 0 <= idx < len(self.session_visits) else '')
        self.session_state = (
            GuideState.SESSION_CONFIRMED if self.current_visit
            else GuideState.SESSION_COMPLETED)
        self.get_logger().info(
            f'session_start 수신: session_id={self.session_id} '
            f'patient={self.patient_id} step={msg.current_step_order}/'
            f'{len(self.session_visits)} current_visit={self.current_visit!r} '
            f'visits={self.session_visits}')
        if self.session_state == GuideState.SESSION_CONFIRMED:
            self.events.publish(
                'session.ready',
                {'current_visit': self.current_visit},
                self.session_id,
            )

    def _complete_current_step_from_qr(self) -> None:
        """waiting spot에서 같은 환자 QR을 현재 검사 완료로 해석한다."""
        if self.robot_state != GuideState.ROBOT_WAITING:
            self.get_logger().warn(
                f'로봇이 대기 중이 아니어서 완료 QR을 무시합니다: {self.robot_state}')
            return
        if self._emergency_engaged or self._battery_alarm:
            self.get_logger().warn('안전 정지 상태에서는 다음 검사실로 출발하지 않습니다.')
            return
        if not 1 <= self.current_step_order <= len(self.session_visits):
            self.get_logger().error(
                f'현재 검사 순서가 올바르지 않습니다: {self.current_step_order}')
            return

        completed_order = self.current_step_order
        self.events.publish(
            'session.step_completed',
            {'step_order': completed_order, 'source': 'qr'},
            self.session_id,
        )

        if completed_order >= len(self.session_visits):
            active_session_id = self.session_id
            self.previous_visit = self.current_visit
            self.current_step_order = 0
            self.session_state = GuideState.SESSION_COMPLETED
            self.robot_state = GuideState.ROBOT_IDLE
            self.events.publish(
                'session.ended', {'end_reason': 'completed'}, active_session_id)
            self.get_logger().info(f'안내 세션 완료: session_id={active_session_id}')

            # 이후 충전소 이동 이벤트는 종료된 환자 세션과 분리한다.
            self.session_id = 0
            self.session_state = GuideState.SESSION_NONE
            self.patient_id = ''
            self.current_step_order = 0
            self.previous_visit = ''
            self.current_visit = ''
            self.session_visits = []
            self._request_dock_return('session_completed')
            return

        self.previous_visit = self.current_visit
        self.current_step_order = completed_order + 1
        self.current_visit = self.session_visits[self.current_step_order - 1]
        waypoint_name = self._visit_waypoint(self.current_visit, 'goal')
        if not waypoint_name:
            # 완료 사실은 되돌리지 않는다. 운영자가 매핑을 고친 뒤 명시적으로
            # 재개할 수 있도록 다음 환자 확인 상태에서 멈춘다.
            self.session_state = GuideState.SESSION_CONFIRMED
            self.robot_state = GuideState.ROBOT_WAITING
            self.get_logger().error(
                f'다음 검사실 goal 매핑이 없어 대기합니다: {self.current_visit!r}')
            return

        self.get_logger().info(
            f'검사 완료 QR 확인: step={completed_order}; '
            f'다음 검사실={self.current_visit!r}')
        self.send_goal(waypoint_name)

    # ---------------------------------------------------------- 환자 거리 대기

    def _on_patient_follow_state(self, msg: String) -> None:
        if not self.patient_follow_enabled:
            return
        try:
            payload = json.loads(msg.data)
            state = str(payload['state'])
            session_id = int(payload.get('session_id', 0))
            patient_id = str(payload.get('patient_id') or '')
            source = str(payload.get('source') or 'unknown')
            raw_distance = payload.get('distance')
            distance = None if raw_distance is None else float(raw_distance)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.get_logger().warn(f'환자 추적 상태 형식 오류: {exc}')
            return
        if state not in ('inactive', 'normal', 'slow', 'waiting'):
            self.get_logger().warn(f'알 수 없는 환자 추적 상태: {state!r}')
            return
        if self.session_state != GuideState.SESSION_GUIDING:
            if state == 'inactive':
                self._cancel_patient_wait_state()
            return
        if session_id != self.session_id or patient_id != self.patient_id:
            self.get_logger().warn(
                '현재 안내와 다른 환자 추적 상태를 무시합니다: '
                f'{session_id}/{patient_id!r}')
            return

        self._patient_follow_last_at = time.monotonic()
        self._patient_follow_state = state
        if state == 'waiting':
            self._pause_for_patient(
                reason='patient_distance', distance=distance, source=source)
        elif state in ('normal', 'slow'):
            self._resume_for_patient(source=source)

    def _check_patient_follow_timeout(self) -> None:
        if (not self.patient_follow_enabled
                or self.session_state != GuideState.SESSION_GUIDING
                or self.session_id <= 0):
            return
        now = time.monotonic()
        if (self._patient_follow_last_at > 0.0
                and now - self._patient_follow_last_at
                <= self.patient_follow_timeout_sec):
            return
        self._patient_follow_state = 'waiting'
        self._pause_for_patient(
            reason='sensor_timeout', distance=None, source='watchdog')

    def _pause_for_patient(
            self, *, reason: str, distance: float | None,
            source: str) -> None:
        if (self.session_state != GuideState.SESSION_GUIDING
                or self._battery_alarm
                or self._emergency_engaged
                or self._fire_evacuating):
            return
        if self._patient_wait_context is not None:
            self.robot_state = GuideState.ROBOT_PAUSED
            return
        context = self._active_nav_context
        if context is None or context['is_dock'] or context['is_waiting']:
            return
        handle = self._active_nav_goal_handle
        result_future = self._active_nav_result_future
        self.robot_state = GuideState.ROBOT_PAUSED
        self._cancel_adaptive_retry()
        if handle is None or result_future is None:
            # Nav2가 목표를 수락하는 콜백에서 즉시 취소한다.
            self._patient_pause_requested = True
            return

        self._nav_generation += 1
        self._patient_wait_context = dict(context)
        self._patient_wait_cancel_pending = True
        self._patient_resume_pending = False
        self._active_nav_goal_handle = None
        self._active_nav_result_future = None
        self._active_nav_context = None
        self.events.publish(
            'patient.follow_wait_started',
            {'reason': reason, 'source': source},
            self.session_id,
        )
        self.get_logger().warn(
            f'환자 대기로 안내 목표를 취소합니다 '
            f'(distance={distance}, source={source}).')
        cancel = handle.cancel_goal_async()
        cancel.add_done_callback(
            lambda done: self._on_patient_wait_cancel_response(
                done, result_future, dict(context)))

    def _on_patient_wait_cancel_response(
            self, future, result_future, context: dict) -> None:
        if self._patient_wait_context is None:
            return
        try:
            response = future.result()
            accepted = bool(response.goals_canceling)
        except Exception as exc:  # noqa: BLE001
            self._patient_wait_cancel_pending = False
            self.get_logger().error(f'환자 대기 목표 취소 요청 실패: {exc}')
            if self._patient_resume_pending:
                self._resume_for_patient(source='cancel_error')
            return
        if not accepted:
            self._patient_wait_cancel_pending = False
            self.get_logger().error(
                'Nav2가 환자 대기 목표 취소를 수락하지 않았습니다.')
            if self._patient_resume_pending:
                self._resume_for_patient(source='cancel_rejected')
            return
        result_future.add_done_callback(
            lambda done: self._on_patient_wait_nav_cancelled(done, context))

    def _on_patient_wait_nav_cancelled(self, future, context: dict) -> None:
        if self._patient_wait_context is None:
            return
        try:
            status = int(future.result().status)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f'환자 대기 목표 취소 결과 실패: {exc}')
            status = GoalStatus.STATUS_UNKNOWN
        self._patient_wait_cancel_pending = False
        if status != GoalStatus.STATUS_CANCELED:
            self.get_logger().warn(
                f'환자 대기 목표가 canceled가 아닙니다(status={status}).')
        if self._patient_resume_pending:
            self._resume_for_patient(source='patient_returned')

    def _resume_for_patient(self, *, source: str) -> None:
        context = self._patient_wait_context
        if context is None:
            # 목표 수락 전 대기 요청이 풀린 경우 기존 목표를 계속한다.
            self._patient_pause_requested = False
            return
        if self._patient_wait_cancel_pending:
            self._patient_resume_pending = True
            return
        if (self.session_state != GuideState.SESSION_GUIDING
                or context['session_id'] != self.session_id
                or self._battery_alarm
                or self._emergency_engaged
                or self._fire_evacuating):
            return

        self._patient_wait_context = None
        self._patient_resume_pending = False
        self.robot_state = GuideState.ROBOT_MOVING
        self.events.publish(
            'patient.follow_wait_ended', {'source': source}, self.session_id)
        self.get_logger().info(
            '환자가 복귀해 원래 안내 목표를 다시 전송합니다.')
        self._send_nav_goal(
            context['waypoint_name'],
            is_dock=context['is_dock'],
            session_id=context['session_id'],
            is_waiting=context['is_waiting'],
            recovery_attempt=context['recovery_attempt'],
            recovery_failures=context['recovery_failures'],
            announce=False,
            low_obstacle_attempts=context['low_obstacle_attempts'],
            low_obstacle_side=context['low_obstacle_side'],
        )

    def _cancel_patient_wait_state(self) -> None:
        self._patient_follow_state = 'inactive'
        self._patient_follow_last_at = 0.0
        self._patient_wait_context = None
        self._patient_wait_cancel_pending = False
        self._patient_resume_pending = False
        self._patient_pause_requested = False

    # ------------------------------------------------------------------ 주행

    def _on_scan(self, msg: LaserScan) -> None:
        self._latest_scan = msg
        self._latest_scan_received_ns = self.get_clock().now().nanoseconds

    def _on_low_obstacle_range(self, msg: Range) -> None:
        """초음파 관측을 회피 드라이버와 저상 장애물 판정에 전달한다."""
        self.low_obstacle_driver.update_range(msg.range)
        if self.low_obstacle_mode != 'sidestep':
            self._low_obstacle_confirmed_count = 0
            return
        if (
                self.low_obstacle_driver.active
                or self._pending_low_obstacle_context is not None
                or self._active_nav_goal_handle is None
                or self._active_nav_context is None
                or self._battery_alarm
                or self._emergency_engaged
                or self._fire_evacuating):
            self._low_obstacle_confirmed_count = 0
            return
        now_ns = self.get_clock().now().nanoseconds
        stale_ns = int(self.low_obstacle_scan_stale_sec * 1_000_000_000)
        if (
                self._latest_scan is None
                or now_ns - self._latest_scan_received_ns > stale_ns):
            self._low_obstacle_confirmed_count = 0
            return
        scan = self._latest_scan
        lidar_range = lidar_sector_min_range(
            scan.ranges,
            angle_min=float(scan.angle_min),
            angle_increment=float(scan.angle_increment),
            range_min=float(scan.range_min),
            range_max=float(scan.range_max),
            center_deg=self.low_obstacle_config.lidar_front_center_deg,
            half_width_deg=self.low_obstacle_config.lidar_half_width_deg,
        )
        detected = is_low_obstacle(
            float(msg.range),
            lidar_range,
            trigger_distance_m=self.low_obstacle_config.trigger_distance_m,
            lidar_margin_m=self.low_obstacle_config.lidar_margin_m,
        )
        if not detected:
            self._low_obstacle_confirmed_count = 0
            return
        self._low_obstacle_confirmed_count += 1
        if self._low_obstacle_confirmed_count < self.low_obstacle_confirmations:
            return
        self._low_obstacle_confirmed_count = 0
        self._request_low_obstacle_avoidance(float(msg.range), lidar_range)

    def _request_low_obstacle_avoidance(
            self, ultrasonic_range: float, lidar_range: float) -> None:
        handle = self._active_nav_goal_handle
        result_future = self._active_nav_result_future
        context = self._active_nav_context
        if handle is None or result_future is None or context is None:
            return
        self.get_logger().warn(
            f'저상 장애물 감지(초음파={ultrasonic_range:.2f}m, '
            f'LiDAR={lidar_range:.2f}m); 현재 안내 목표를 일시 취소합니다.')
        self._nav_generation += 1
        self._pending_low_obstacle_context = dict(context)
        self._active_nav_goal_handle = None
        self._active_nav_result_future = None
        self._active_nav_context = None
        cancel = handle.cancel_goal_async()
        cancel.add_done_callback(
            lambda done: self._on_low_obstacle_cancel_response(
                done, result_future, dict(context)))

    def _on_low_obstacle_cancel_response(
            self, future, result_future, context: dict) -> None:
        if self._pending_low_obstacle_context is None:
            return
        try:
            response = future.result()
            accepted = bool(response.goals_canceling)
        except Exception as exc:  # noqa: BLE001 - 안전 정지로 전환
            self._low_obstacle_cancel_failed(
                context, f'안내 목표 취소 요청 실패: {exc}')
            return
        if not accepted:
            self._low_obstacle_cancel_failed(
                context, 'Nav2가 안내 목표 취소를 수락하지 않았습니다.')
            return
        result_future.add_done_callback(
            lambda done: self._on_low_obstacle_nav_cancelled(done, context))

    def _on_low_obstacle_nav_cancelled(self, future, context: dict) -> None:
        if self._pending_low_obstacle_context is None:
            return
        try:
            status = int(future.result().status)
        except Exception as exc:  # noqa: BLE001 - 취소 확인 실패
            self._low_obstacle_cancel_failed(
                context, f'안내 목표 취소 결과 확인 실패: {exc}')
            return
        if status != GoalStatus.STATUS_CANCELED:
            self._low_obstacle_cancel_failed(
                context, f'안내 목표가 취소 상태로 끝나지 않았습니다(status={status}).')
            return
        attempts = int(context.get('low_obstacle_attempts', 0))
        if attempts >= self.low_obstacle_max_sidesteps:
            self._fail_low_obstacle_context(
                context, '저상 장애물 최대 회피 횟수에 도달했습니다.')
            return
        self.robot_state = GuideState.ROBOT_MOVING
        self.get_logger().info(
            f'안내 목표 취소 완료; 옆걸음 회피 {attempts + 1}/'
            f'{self.low_obstacle_max_sidesteps}회를 시작합니다.')
        self.low_obstacle_driver.start(context.get('low_obstacle_side'))

    def _low_obstacle_cancel_failed(self, context: dict, message: str) -> None:
        self.get_logger().error(message)
        # 목표가 실제로 멈췄는지 확인할 수 없으므로 직접 회피를 시작하지 않고
        # 기존 안전 게이트를 잠근다.
        self.obstacle_stop_pub.publish(Bool(data=True))
        self._pending_low_obstacle_context = None
        self._fail_low_obstacle_context(context, message)

    def _on_low_obstacle_sidestep_complete(
            self, outcome: SidestepOutcome) -> None:
        context = self._pending_low_obstacle_context
        if context is None:
            return
        self._pending_low_obstacle_context = None
        if not outcome.succeeded:
            self._fail_low_obstacle_context(
                context, f'저상 장애물 회피 실패: {outcome.reason}')
            return
        if self._battery_alarm or self._emergency_engaged or self._fire_evacuating:
            return
        attempts = int(context.get('low_obstacle_attempts', 0)) + 1
        self.get_logger().info(
            f'옆걸음 회피 완료; 원래 목표를 다시 전송합니다 '
            f'(attempt={attempts}, side={outcome.side}).')
        self._send_nav_goal(
            context['waypoint_name'],
            is_dock=context['is_dock'],
            session_id=context['session_id'],
            is_waiting=context['is_waiting'],
            recovery_attempt=context['recovery_attempt'],
            recovery_failures=context['recovery_failures'],
            announce=False,
            low_obstacle_attempts=attempts,
            low_obstacle_side=outcome.side,
        )

    def _fail_low_obstacle_context(self, context: dict, message: str) -> None:
        self._pending_low_obstacle_context = None
        self._goal_failed(
            context['waypoint_name'],
            context['is_dock'],
            context['session_id'],
            -8,
            message,
            is_waiting=context['is_waiting'],
        )

    def _cancel_low_obstacle_operation(self) -> None:
        self.low_obstacle_driver.cancel()
        if self._active_nav_goal_handle is not None:
            self._active_nav_goal_handle.cancel_goal_async()
        self._active_nav_goal_handle = None
        self._active_nav_result_future = None
        self._active_nav_context = None
        self._pending_low_obstacle_context = None
        self._low_obstacle_confirmed_count = 0

    def _on_nav_feedback(self, feedback_msg, generation: int) -> None:
        if generation != self._nav_generation:
            return
        self._latest_nav_pose = feedback_msg.feedback.current_pose
        self._latest_nav_pose_received_ns = self.get_clock().now().nanoseconds

    def send_goal(self, waypoint_name: str) -> None:
        """환자 안내 목적지로 이동한다."""
        if self._fire_evacuating:
            self.get_logger().warn('화재 대피 중에는 안내 목표를 보낼 수 없습니다.')
            return
        if self._battery_alarm:
            self.get_logger().warn('배터리 부족 상태에서는 안내 목표를 보낼 수 없습니다.')
            return
        self._cancel_adaptive_retry()
        self._send_nav_goal(waypoint_name, is_dock=False, session_id=self.session_id)

    def _visit_name_for_waypoint(self, waypoint_name: str) -> str:
        """Nav2 좌표 키를 관제와 DB가 사용하는 방문지 이름으로 되돌린다."""
        current = self.visit_waypoints.get(self.current_visit) or {}
        if isinstance(current, dict) and waypoint_name in current.values():
            return self.current_visit
        return next(
            (visit for visit, mapping in self.visit_waypoints.items()
             if isinstance(mapping, dict) and waypoint_name in mapping.values()),
            waypoint_name,
        )

    def _visit_waypoint(self, visit_name: str, kind: str) -> str:
        mapping = self.visit_waypoints.get(visit_name) or {}
        if not isinstance(mapping, dict):
            return ''
        return str(mapping.get(kind) or '')

    def _move_to_waiting_spot(self, visit_name: str, session_id: int) -> None:
        mapping = self.visit_waypoints.get(visit_name)
        if not isinstance(mapping, dict) or 'waiting' not in mapping:
            self._waiting_spot_failed(visit_name, '', -2)
            return
        waypoint_name = str(mapping.get('waiting') or '')
        if not waypoint_name:
            # waiting: null 은 설정 누락이 아니라 별도 대기 위치가 없는 방문지다.
            self.robot_state = GuideState.ROBOT_WAITING
            self.session_state = GuideState.SESSION_IN_ROOM
            self.get_logger().info(
                f'별도 waiting spot 없음; goal에서 QR 대기: {visit_name}')
            return
        self.get_logger().info(
            f'검사실 도착; waiting spot으로 이동: {waypoint_name}')
        self._send_nav_goal(
            waypoint_name,
            is_dock=False,
            is_waiting=True,
            session_id=session_id,
            announce=False,
        )

    def _play_arrival_chime(self) -> None:
        """GPIO 음 재생이 ROS 콜백을 막지 않도록 별도 스레드에서 실행한다."""
        if not self.use_arrival_chime:
            return

        def run() -> None:
            try:
                play_arrival_chime()
            except Exception as exc:  # noqa: BLE001
                # 소리 실패가 안내 주행을 막아서는 안 된다.
                self.get_logger().warn(f'도착 알림음 재생 실패: {exc}')

        threading.Thread(target=run, daemon=True).start()

    def _schedule_waiting_move(self, visit_name: str, session_id: int) -> None:
        """도착 안내를 보여준 뒤 waiting waypoint 이동을 예약한다."""
        self._cancel_arrival_notice()
        self._pending_waiting_move = (visit_name, session_id)
        if self.arrival_notice_sec <= 0.0:
            self._finish_arrival_notice()
            return
        self._arrival_notice_timer = self.create_timer(
            self.arrival_notice_sec, self._finish_arrival_notice)
        self.get_logger().info(
            f'도착 안내 후 {self.arrival_notice_sec:.1f}초 뒤 '
            f'waiting spot으로 이동합니다: {visit_name}')

    def _cancel_arrival_notice(self) -> None:
        if self._arrival_notice_timer is not None:
            self._arrival_notice_timer.cancel()
            self.destroy_timer(self._arrival_notice_timer)
            self._arrival_notice_timer = None
        self._pending_waiting_move = None

    def _finish_arrival_notice(self) -> None:
        pending = self._pending_waiting_move
        if self._arrival_notice_timer is not None:
            self._arrival_notice_timer.cancel()
            self.destroy_timer(self._arrival_notice_timer)
            self._arrival_notice_timer = None
        self._pending_waiting_move = None
        if pending is None:
            return
        visit_name, session_id = pending
        if self._battery_alarm or self._emergency_engaged:
            return
        if (session_id != self.session_id
                or visit_name != self.current_visit
                or self.session_state != GuideState.SESSION_ARRIVED):
            self.get_logger().warn(
                '안내 세션 상태가 바뀌어 waiting spot 이동을 취소합니다.')
            return
        self._move_to_waiting_spot(visit_name, session_id)

    def _waiting_spot_failed(
            self, visit_name: str, waypoint_name: str, error_code: int) -> None:
        # 설정 누락이나 실제 주행 실패를 정상 대기로 숨기면 출입구를 막은 채
        # 다음 단계로 진행할 수 있다. 임상 도착 기록은 유지하되 운영자가
        # 확인할 때까지 완료 QR 스캔과 자동 진행을 열지 않는다.
        self.robot_state = GuideState.ROBOT_PAUSED
        self.session_state = GuideState.SESSION_ARRIVED
        self.events.publish(
            'nav.waiting_spot_failed',
            {
                'visit_name': visit_name,
                'waypoint_name': waypoint_name,
                'error_code': int(error_code),
            },
            self.session_id,
        )
        self.get_logger().error(
            f'waiting spot 이동 실패; 운영자 확인이 필요합니다: {visit_name}')

    def _cancel_adaptive_retry(self) -> None:
        if self._adaptive_retry_timer is not None:
            self._adaptive_retry_timer.cancel()
            self.destroy_timer(self._adaptive_retry_timer)
            self._adaptive_retry_timer = None

    def _request_dock_return(self, reason: str) -> None:
        """충전소 복귀 의도를 등록하고 안전한 시점에 첫 이동을 시작한다."""
        self._cancel_dock_retry()
        self._dock_reason = reason
        self._dock_attempt = 0
        if self._emergency_engaged or self._fire_evacuating:
            self._dock_pending = True
            self.get_logger().warn(
                '안전 우선 상태가 끝난 뒤 충전소 복귀를 시작합니다.')
            return
        self._return_to_dock()

    def _return_to_dock(self) -> None:
        """현재 안내 목표를 선점하고 이 로봇에 배정된 충전소로 복귀한다."""
        if self._dock_reason is None:
            return
        if self._dock_reason == 'battery' and not self._battery_alarm:
            self._dock_reason = None
            self._dock_pending = False
            return
        if self._emergency_engaged or self._fire_evacuating:
            self._dock_pending = True
            return
        self._dock_pending = False
        self._dock_attempt += 1
        self.get_logger().info(
            f'충전소 복귀 시도 {self._dock_attempt}/{self.dock_max_attempts} '
            f'(reason={self._dock_reason})')
        self._send_nav_goal(self.charging_waypoint, is_dock=True, session_id=0)

    def _cancel_dock_retry(self) -> None:
        if self._dock_retry_timer is not None:
            self._dock_retry_timer.cancel()
            self.destroy_timer(self._dock_retry_timer)
            self._dock_retry_timer = None

    def _retry_dock(self) -> None:
        self._cancel_dock_retry()
        self._return_to_dock()

    def _dock_failed(
            self, waypoint_name: str, error_code: int, *, retryable: bool) -> None:
        dock_active = (
            self._dock_reason is not None
            and (self._dock_reason != 'battery' or self._battery_alarm))
        self.robot_state = (
            GuideState.ROBOT_BATTERY_LOW
            if self._dock_reason == 'battery'
            else GuideState.ROBOT_RETURNING_TO_DOCK
            if self._dock_reason == 'guidance_canceled'
            else GuideState.ROBOT_MOVING)
        if (retryable and dock_active and not self._emergency_engaged
                and not self._fire_evacuating
                and self._dock_attempt < self.dock_max_attempts):
            self.get_logger().warn(
                f'{self.dock_retry_delay:.1f}초 뒤 충전소 복귀를 재시도합니다.')
            self._dock_retry_timer = self.create_timer(
                self.dock_retry_delay, self._retry_dock)
            return
        self.events.publish(
            'dock.return_failed',
            {'station_name': waypoint_name, 'error_code': int(error_code)})
        failed_reason = self._dock_reason
        self._dock_reason = None
        self._dock_pending = False
        if failed_reason == 'guidance_canceled':
            # 복귀 최종 실패 뒤 재배정되면 현재 위치를 보장할 수 없다.
            self.robot_state = GuideState.ROBOT_PAUSED
        elif not self._battery_alarm:
            self.robot_state = GuideState.ROBOT_IDLE

    def _send_nav_goal(
            self, waypoint_name: str, *, is_dock: bool, session_id: int,
            is_waiting: bool = False,
            recovery_attempt: int = 0,
            recovery_failures: Mapping[str, int] | None = None,
            announce: bool = True,
            low_obstacle_attempts: int = 0,
            low_obstacle_side: int | None = None) -> None:
        wp = self.waypoints.get(waypoint_name)
        if wp is None:
            self.get_logger().error(f'알 수 없는 waypoint: {waypoint_name}')
            if is_dock:
                self._dock_failed(waypoint_name, -2, retryable=False)
            elif is_waiting:
                self._waiting_spot_failed(
                    self.current_visit, waypoint_name, -2)
            else:
                self._goal_failed(
                    waypoint_name, False, session_id, -2,
                    '안내 Waypoint를 찾을 수 없습니다.')
            return
        if not self.nav.wait_for_server(timeout_sec=3.0):
            self.get_logger().error('navigate_to_pose 액션 서버가 없습니다.')
            if is_dock:
                self._dock_failed(waypoint_name, -3, retryable=True)
            elif is_waiting:
                self._waiting_spot_failed(
                    self.current_visit, waypoint_name, -3)
            else:
                self._goal_failed(
                    waypoint_name, False, session_id, -3,
                    'Nav2 없이는 안내를 시작할 수 없습니다.')
            return

        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(wp['x'])
        goal.pose.pose.position.y = float(wp['y'])
        z, w = _yaw_to_quat(float(wp['yaw']))
        goal.pose.pose.orientation.z = z
        goal.pose.pose.orientation.w = w
        if (not is_dock and not is_waiting
                and (self.recovery_mode == 'adaptive'
                     or self.planner_mode == 'smac2d')):
            goal.behavior_tree = self._behavior_tree_file(
                fallback=self.recovery_mode == 'default')

        self._nav_generation += 1
        generation = self._nav_generation
        context = {
            'waypoint_name': waypoint_name,
            'is_dock': is_dock,
            'session_id': session_id,
            'is_waiting': is_waiting,
            'recovery_attempt': recovery_attempt,
            'recovery_failures': dict(recovery_failures or {}),
            'low_obstacle_attempts': low_obstacle_attempts,
            'low_obstacle_side': low_obstacle_side,
        }
        self._active_nav_goal_handle = None
        self._active_nav_result_future = None
        self._active_nav_context = context
        if is_dock:
            self.current_visit = waypoint_name
            self.robot_state = (
                GuideState.ROBOT_BATTERY_LOW
                if self._dock_reason == 'battery'
                else GuideState.ROBOT_RETURNING_TO_DOCK
                if self._dock_reason == 'guidance_canceled'
                else GuideState.ROBOT_MOVING)
            self.events.publish(
                'dock.return_started', {'station_name': waypoint_name})
        elif is_waiting:
            self.robot_state = GuideState.ROBOT_MOVING
            self.session_state = GuideState.SESSION_ARRIVED
        else:
            self.current_visit = self._visit_name_for_waypoint(waypoint_name)
            self.robot_state = GuideState.ROBOT_MOVING
            self.session_state = GuideState.SESSION_GUIDING
            if self.patient_follow_enabled:
                # 최초 상태 heartbeat가 도착할 유예 시간을 준다.
                self._patient_follow_last_at = time.monotonic()
            if announce:
                self.events.publish(
                    'nav.goal_sent', {'visit_name': self.current_visit}, session_id)

        future = self.nav.send_goal_async(
            goal,
            feedback_callback=lambda feedback: self._on_nav_feedback(
                feedback, generation),
        )
        future.add_done_callback(
            lambda done: self._on_goal_response(
                done, generation, waypoint_name, is_dock, session_id,
                is_waiting, recovery_attempt, dict(recovery_failures or {}),
                context))

    def _on_goal_response(
            self, future, generation: int, waypoint_name: str,
            is_dock: bool, session_id: int, is_waiting: bool = False,
            recovery_attempt: int = 0,
            recovery_failures: Mapping[str, int] | None = None,
            nav_context: dict | None = None):
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001 - Nav2 오류를 이벤트로 보고
            if generation != self._nav_generation:
                return
            self._active_nav_context = None
            self._goal_failed(
                waypoint_name, is_dock, session_id, -4,
                f'목표 전송 중 예외: {exc}', is_waiting=is_waiting)
            return

        # 충전 복귀 등 새 목표가 이미 생겼다면 이전 목표 결과는 상태에 반영하지
        # 않는다. 서버가 아직 이전 목표를 잡고 있으면 명시적으로 취소한다.
        if generation != self._nav_generation:
            if handle.accepted:
                handle.cancel_goal_async()
            return
        if not handle.accepted:
            self._active_nav_context = None
            self._goal_failed(
                waypoint_name, is_dock, session_id, -1,
                'Nav2 가 목표를 거부했습니다.', is_waiting=is_waiting)
            return
        self._active_nav_goal_handle = handle
        self._active_nav_context = nav_context
        result = handle.get_result_async()
        self._active_nav_result_future = result
        result.add_done_callback(
            lambda done: self._on_goal_result(
                done, generation, waypoint_name, is_dock, session_id,
                is_waiting, recovery_attempt, dict(recovery_failures or {})))
        if self._patient_pause_requested:
            self._patient_pause_requested = False
            if self._patient_follow_state == 'waiting':
                self._pause_for_patient(
                    reason='patient_distance',
                    distance=None,
                    source='goal_accept',
                )

    def _on_goal_result(
            self, future, generation: int, waypoint_name: str,
            is_dock: bool, session_id: int, is_waiting: bool = False,
            recovery_attempt: int = 0,
            recovery_failures: Mapping[str, int] | None = None):
        if generation != self._nav_generation:
            return
        nav_context = dict(self._active_nav_context or {
            'waypoint_name': waypoint_name,
            'is_dock': is_dock,
            'session_id': session_id,
            'is_waiting': is_waiting,
            'recovery_attempt': recovery_attempt,
            'recovery_failures': dict(recovery_failures or {}),
            'low_obstacle_attempts': 0,
            'low_obstacle_side': None,
        })
        self._active_nav_goal_handle = None
        self._active_nav_result_future = None
        self._active_nav_context = None
        try:
            status = future.result().status
        except Exception as exc:  # noqa: BLE001
            self._goal_failed(
                waypoint_name, is_dock, session_id, -4,
                f'결과 수신 중 예외: {exc}', is_waiting=is_waiting)
            return

        # action_msgs/GoalStatus.STATUS_SUCCEEDED == 4
        if status == 4:
            if is_dock:
                self._cancel_dock_retry()
                # 좌표 도착만으로 충전 전류가 흐른다고 단정할 수는 없다.
                dock_reason = self._dock_reason
                self.robot_state = (
                    GuideState.ROBOT_IDLE
                    if dock_reason == 'guidance_canceled'
                    else GuideState.ROBOT_WAITING)
                self.events.publish(
                    'dock.return_succeeded', {'station_name': waypoint_name})
                self.get_logger().info(f'충전소 도착: {waypoint_name}')
                self._dock_reason = None
                self._dock_pending = False
                self._dock_attempt = 0
            elif is_waiting:
                self.robot_state = GuideState.ROBOT_WAITING
                self.session_state = GuideState.SESSION_IN_ROOM
                self.get_logger().info(
                    f'waiting spot 도착: {self.current_visit}')
            else:
                self._cancel_adaptive_retry()
                self.robot_state = GuideState.ROBOT_WAITING
                self.session_state = GuideState.SESSION_ARRIVED
                visit_name = self._visit_name_for_waypoint(waypoint_name)
                self.events.publish(
                    'nav.goal_succeeded',
                    {'visit_name': visit_name},
                    session_id)
                self.get_logger().info(f'도착: {waypoint_name}')
                self._play_arrival_chime()
                self._schedule_waiting_move(visit_name, session_id)
        else:
            if (self.patient_follow_enabled
                    and self._patient_follow_state == 'waiting'
                    and not is_dock and not is_waiting
                    and session_id == self.session_id
                    and self.session_state == GuideState.SESSION_GUIDING):
                # 환자 대기 신호와 FollowPath ABORTED가 경합해도
                # Adaptive Recovery로 넘기지 않고 원래 목표를 보존한다.
                self._patient_wait_context = nav_context
                self._patient_wait_cancel_pending = False
                self.robot_state = GuideState.ROBOT_PAUSED
                self.events.publish(
                    'patient.follow_wait_started',
                    {'reason': 'nav_aborted_while_waiting', 'source': 'nav2'},
                    session_id,
                )
                return
            adaptive_dock = is_dock and recovery_attempt == 0
            if (not is_waiting and status == 6
                    and (not is_dock or adaptive_dock)
                    and self._start_adaptive_recovery(
                        waypoint_name, session_id, recovery_attempt,
                        dict(recovery_failures or {}), is_dock=is_dock)):
                return
            if (not is_dock and not is_waiting and status == 6
                    and self.recovery_mode == 'adaptive'):
                self._schedule_adaptive_retry(
                    waypoint_name, session_id, recovery_attempt,
                    dict(recovery_failures or {}))
                return
            self._goal_failed(
                waypoint_name, is_dock, session_id, int(status),
                f'주행 실패 (status={status})', is_waiting=is_waiting)

    def _start_adaptive_recovery(
            self, waypoint_name: str, session_id: int, recovery_attempt: int,
            failures: dict[str, int], *, is_dock: bool = False) -> bool:
        """현재 위치에서 안전한 탈출 후보를 만들고 경로 검증을 시작한다."""
        if self.recovery_mode != 'adaptive':
            return False
        if (self._battery_alarm and not is_dock) or self._emergency_engaged:
            return False
        now_ns = self.get_clock().now().nanoseconds
        stale_ns = int(self.recovery_scan_stale_sec * 1_000_000_000)
        if (self._latest_scan is None or self._latest_nav_pose is None
                or now_ns - self._latest_scan_received_ns > stale_ns
                or now_ns - self._latest_nav_pose_received_ns > stale_ns):
            self.get_logger().warn(
                'LiDAR 또는 현재 위치가 오래되어 적응형 복구를 건너뜁니다.')
            return False
        if not self.path_planner.wait_for_server(timeout_sec=1.0):
            self.get_logger().warn(
                'compute_path_to_pose 액션 서버가 없어 적응형 복구를 건너뜁니다.')
            return False

        wp = self.waypoints.get(waypoint_name)
        if wp is None:
            return False
        pose = self._latest_nav_pose.pose
        robot_yaw = _quat_to_yaw(pose.orientation.z, pose.orientation.w)
        map_goal_angle = math.atan2(
            float(wp['y']) - pose.position.y,
            float(wp['x']) - pose.position.x,
        )
        scan = self._latest_scan
        candidates = select_diverse_candidates(
            select_escape_candidates(
                scan.ranges,
                angle_min=float(scan.angle_min),
                angle_increment=float(scan.angle_increment),
                range_min=float(scan.range_min),
                range_max=float(scan.range_max),
                goal_bearing_rad=math.atan2(
                    math.sin(map_goal_angle - robot_yaw),
                    math.cos(map_goal_angle - robot_yaw),
                ),
                failures=failures,
            ),
            limit=self.recovery_candidate_limit,
            minimum_separation_rad=self.recovery_candidate_separation_rad,
        )
        if not candidates:
            self.get_logger().warn('안전 여유를 만족하는 탈출 후보가 없습니다.')
            return False

        context = {
            'waypoint_name': waypoint_name,
            'session_id': session_id,
            'is_dock': is_dock,
            'recovery_attempt': recovery_attempt,
            'failures': failures,
            'candidates': candidates,
            'index': 0,
            'robot_x': float(pose.position.x),
            'robot_y': float(pose.position.y),
            'robot_yaw': robot_yaw,
            'generation': self._nav_generation,
        }
        self.robot_state = GuideState.ROBOT_MOVING
        self.get_logger().warn(
            f'적응형 복구 주기 {recovery_attempt + 1}: '
            f'{len(candidates)}개 후보 경로를 검증합니다.')
        self._validate_next_recovery_candidate(context)
        return True

    def _candidate_pose(self, context: dict, candidate: EscapeCandidate) -> PoseStamped:
        x, y, yaw = candidate_to_map(
            candidate,
            robot_x=context['robot_x'],
            robot_y=context['robot_y'],
            robot_yaw=context['robot_yaw'],
        )
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y
        z, w = _yaw_to_quat(yaw)
        pose.pose.orientation.z = z
        pose.pose.orientation.w = w
        return pose

    def _validate_next_recovery_candidate(self, context: dict) -> None:
        if context['generation'] != self._nav_generation:
            return
        index = context['index']
        if index >= len(context['candidates']):
            self.get_logger().warn('검증 가능한 적응형 탈출 경로가 없습니다.')
            if context['is_dock']:
                self._dock_failed(
                    context['waypoint_name'], 6, retryable=True)
                return
            self._schedule_adaptive_retry(
                context['waypoint_name'],
                context['session_id'],
                context['recovery_attempt'] + 1,
                context['failures'],
            )
            return
        candidate = context['candidates'][index]
        context['index'] += 1
        goal = ComputePathToPose.Goal()
        goal.goal = self._candidate_pose(context, candidate)
        goal.planner_id = 'Smac2D' if self.planner_mode == 'smac2d' else 'GridBased'
        goal.use_start = False
        future = self.path_planner.send_goal_async(goal)
        future.add_done_callback(
            lambda done: self._on_recovery_path_response(done, context, candidate))

    def _on_recovery_path_response(
            self, future, context: dict, candidate: EscapeCandidate) -> None:
        if context['generation'] != self._nav_generation:
            return
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f'탈출 경로 요청 실패({candidate.name}): {exc}')
            self._reject_recovery_candidate(context, candidate)
            return
        if not handle.accepted:
            self._reject_recovery_candidate(context, candidate)
            return
        result = handle.get_result_async()
        result.add_done_callback(
            lambda done: self._on_recovery_path_result(done, context, candidate))

    def _on_recovery_path_result(
            self, future, context: dict, candidate: EscapeCandidate) -> None:
        if context['generation'] != self._nav_generation:
            return
        try:
            wrapped = future.result()
            path_result = wrapped.result
            valid = (
                wrapped.status == 4
                and path_result.error_code == ComputePathToPose.Result.NONE
                and len(path_result.path.poses) >= 2
            )
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f'탈출 경로 결과 실패({candidate.name}): {exc}')
            valid = False
        if not valid:
            self._reject_recovery_candidate(context, candidate)
            return
        self.get_logger().info(
            f'탈출 후보 선택: {candidate.name}, 거리={candidate.distance_m:.2f}m, '
            f'여유={candidate.clearance_m:.2f}m')
        self._send_recovery_goal(context, candidate)

    def _reject_recovery_candidate(
            self, context: dict, candidate: EscapeCandidate) -> None:
        failures = context['failures']
        failures[candidate.name] = failures.get(candidate.name, 0) + 1
        self._validate_next_recovery_candidate(context)

    def _send_recovery_goal(
            self, context: dict, candidate: EscapeCandidate) -> None:
        self._nav_generation += 1
        context['generation'] = self._nav_generation
        context['active_candidate'] = candidate
        goal = NavigateToPose.Goal()
        goal.pose = self._candidate_pose(context, candidate)
        goal.behavior_tree = self._behavior_tree_file(fallback=False)
        future = self.nav.send_goal_async(
            goal,
            feedback_callback=lambda feedback: self._on_nav_feedback(
                feedback, context['generation']),
        )
        future.add_done_callback(
            lambda done: self._on_recovery_goal_response(done, context))

    def _on_recovery_goal_response(self, future, context: dict) -> None:
        if context['generation'] != self._nav_generation:
            return
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f'탈출 목표 전송 실패: {exc}')
            self._recovery_motion_failed(context)
            return
        if not handle.accepted:
            self._recovery_motion_failed(context)
            return
        result = handle.get_result_async()
        result.add_done_callback(
            lambda done: self._on_recovery_goal_result(done, context))

    def _on_recovery_goal_result(self, future, context: dict) -> None:
        if context['generation'] != self._nav_generation:
            return
        try:
            status = future.result().status
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f'탈출 이동 결과 실패: {exc}')
            self._recovery_motion_failed(context)
            return
        if status != 4:
            self._recovery_motion_failed(context)
            return
        target_kind = '충전소' if context['is_dock'] else '안내'
        self.get_logger().info(
            f'임시 탈출 지점 도착; 원래 {target_kind} 목표를 다시 시도합니다.')
        self._send_nav_goal(
            context['waypoint_name'],
            is_dock=context['is_dock'],
            session_id=context['session_id'],
            recovery_attempt=context['recovery_attempt'] + 1,
            recovery_failures=context['failures'],
            announce=False,
        )

    def _recovery_motion_failed(self, context: dict) -> None:
        candidate = context['active_candidate']
        failures = context['failures']
        failures[candidate.name] = failures.get(candidate.name, 0) + 1
        if context['is_dock']:
            self._dock_failed(
                context['waypoint_name'], 6, retryable=True)
            return
        next_attempt = context['recovery_attempt'] + 1
        if self._start_adaptive_recovery(
                context['waypoint_name'], context['session_id'],
                next_attempt, failures):
            return
        self._schedule_adaptive_retry(
            context['waypoint_name'], context['session_id'], next_attempt, failures)

    def _schedule_adaptive_retry(
            self, waypoint_name: str, session_id: int, recovery_attempt: int,
            failures: Mapping[str, int]) -> None:
        if self._battery_alarm or self._emergency_engaged:
            return
        self._cancel_adaptive_retry()
        self._nav_generation += 1
        generation = self._nav_generation
        self.robot_state = GuideState.ROBOT_WAITING
        self.get_logger().warn(
            f'탈출하지 못해 {self.recovery_retry_delay_sec:.1f}초 정지 후 '
            '최신 LiDAR로 다시 판단합니다.')
        retry_failures = dict(failures)
        self._adaptive_retry_timer = self.create_timer(
            self.recovery_retry_delay_sec,
            lambda: self._retry_adaptive_goal(
                generation, waypoint_name, session_id,
                recovery_attempt, retry_failures),
        )

    def _retry_adaptive_goal(
            self, generation: int, waypoint_name: str, session_id: int,
            recovery_attempt: int, failures: Mapping[str, int]) -> None:
        self._cancel_adaptive_retry()
        if generation != self._nav_generation:
            return
        if self._battery_alarm or self._emergency_engaged:
            return
        self._send_nav_goal(
            waypoint_name,
            is_dock=False,
            session_id=session_id,
            recovery_attempt=recovery_attempt,
            recovery_failures=failures,
            announce=False,
        )

    def _goal_failed(
            self, waypoint_name: str, is_dock: bool, session_id: int,
            error_code: int, message: str, *, is_waiting: bool = False) -> None:
        self.get_logger().error(message)
        if is_dock:
            self._dock_failed(waypoint_name, error_code, retryable=True)
        elif is_waiting:
            self._waiting_spot_failed(
                self.current_visit, waypoint_name, error_code)
        else:
            self.robot_state = GuideState.ROBOT_IDLE
            self.session_state = GuideState.SESSION_CONFIRMED
            self.events.publish(
                'nav.goal_aborted',
                {
                    'visit_name': self._visit_name_for_waypoint(waypoint_name),
                    'error_code': int(error_code),
                },
                session_id)

    # ------------------------------------------------------------------ 발행

    def _publish_state(self):
        msg = GuideState()
        msg.robot_id = self.robot_id
        msg.robot_state = self.robot_state
        msg.session_state = self.session_state
        msg.session_id = self.session_id
        msg.patient_id = self.patient_id
        msg.previous_visit = self.previous_visit
        msg.current_visit = self.current_visit
        msg.battery_voltage = float(self.voltage)
        msg.returning_to_dock = self._dock_reason is not None
        msg.battery_percent = self.percent
        self.state_pub.publish(msg)


def main():
    rclpy.init()
    node = GuideManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
