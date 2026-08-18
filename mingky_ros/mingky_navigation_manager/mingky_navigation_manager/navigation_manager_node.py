"""엔지니어용 단일 Waypoint 시험 주행을 소유하는 노드.

환자 안내 순서와 방문지 전환은 guide_manager가 담당한다. 이 노드는 환자 세션과
무관한 저장 Waypoint 또는 임시 좌표 시험만 Nav2에 전달하며, 동시에 하나의 목표만
허용한다. 환자 안내가 시작되거나 저전압·비상정지가 발생하면 시험 목표를 취소한다.
"""

import json
import math
from pathlib import Path
from typing import Mapping

from action_msgs.msg import GoalStatus
from ament_index_python.packages import get_package_share_directory, PackageNotFoundError
from geometry_msgs.msg import PoseStamped
from mingky_guide_manager.low_obstacle import (
    LowObstacleConfig,
    SidestepOutcome,
    is_low_obstacle,
    lidar_sector_min_range,
)
from mingky_guide_manager.low_obstacle_driver import SidestepActionDriver
from mingky_interfaces.msg import GuideState
from mingky_smart_recovery.selector import (
    EscapeCandidate,
    candidate_to_map,
    select_diverse_candidates,
    select_escape_candidates,
)
from nav2_msgs.action import ComputePathToPose, DriveOnHeading, NavigateToPose, Spin
import rclpy
from rcl_interfaces.msg import SetParametersResult
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan, Range
from std_msgs.msg import Bool, String
import yaml


BUSY_ERROR = -5
SAFETY_ERROR = -6
CLINICAL_ERROR = -7
LOCALIZATION_ERROR = -8
RECOVERY_ERROR = -9
LOW_OBSTACLE_ERROR = -10


def _yaw_to_quat(yaw: float) -> tuple[float, float]:
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def _quat_to_yaw(z: float, w: float) -> float:
    return math.atan2(2.0 * w * z, 1.0 - 2.0 * z * z)


class NavigationManager(Node):

    def __init__(self, **kwargs):
        super().__init__('navigation_manager', **kwargs)

        self.declare_parameter('robot_id', 'pinky-01')
        self.declare_parameter('waypoints_file', '')
        self.declare_parameter('map_name', 'yun_map_highres_clean')
        # 단독 실행은 기존 Nav2 동작을 유지한다. 통합 launch는 환자 안내와
        # 같은 adaptive 값을 명시해 시험주행에서도 프로젝트 복구를
        # 사용한다.
        self.declare_parameter('recovery_mode', 'default')
        self.declare_parameter('planner_mode', 'navfn')
        self.declare_parameter('recovery_scan_topic', '/scan')
        self.declare_parameter('recovery_scan_stale_sec', 1.0)
        self.declare_parameter('recovery_candidate_limit', 4)
        self.declare_parameter('recovery_candidate_separation_deg', 30.0)
        self.declare_parameter('recovery_retry_delay_sec', 5.0)
        # 임상 안내와 달리 엔지니어 시험은 유한 시간에 성공/실패가
        # 나야 한다.
        self.declare_parameter('recovery_max_attempts', 3)
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

        self.robot_id = str(self.get_parameter('robot_id').value)
        self.waypoints = self._load_waypoints(
            str(self.get_parameter('waypoints_file').value),
            str(self.get_parameter('map_name').value),
        )
        self.recovery_mode = str(
            self.get_parameter('recovery_mode').value).lower()
        if self.recovery_mode not in ('default', 'adaptive'):
            self.get_logger().warn(
                f'지원하지 않는 recovery_mode={self.recovery_mode!r}; '
                'default를 사용합니다.')
            self.recovery_mode = 'default'
        self.planner_mode = str(
            self.get_parameter('planner_mode').value).lower()
        if self.planner_mode not in ('navfn', 'smac2d'):
            self.get_logger().warn(
                f'지원하지 않는 planner_mode={self.planner_mode!r}; '
                'navfn을 사용합니다.')
            self.planner_mode = 'navfn'
        self._behavior_tree_dir = self._find_behavior_tree_dir()
        if self._behavior_tree_dir is None:
            if self.recovery_mode == 'adaptive':
                self.get_logger().error(
                    'Adaptive Recovery 파일이 없어 default로 전환합니다.')
                self.recovery_mode = 'default'
            if self.planner_mode == 'smac2d':
                self.get_logger().error(
                    'Smac2D 선택 파일이 없어 navfn으로 전환합니다.')
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
        self.recovery_max_attempts = max(
            1, int(self.get_parameter('recovery_max_attempts').value))
        self.low_obstacle_mode = str(
            self.get_parameter('low_obstacle_mode').value).lower()
        if self.low_obstacle_mode not in ('disabled', 'sidestep'):
            self.get_logger().warn(
                f'지원하지 않는 low_obstacle_mode={self.low_obstacle_mode!r}; '
                'disabled를 사용합니다.')
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

        self._clinical_active = False
        self._battery_low = False
        self._emergency = False
        self._localization_active = False
        self._active = False
        self._generation = 0
        self._goal_handle = None
        self._goal_result_future = None
        self._recovery_retry_timer = None
        self._latest_scan = None
        self._latest_scan_received_ns = 0
        self._latest_nav_pose = None
        self._latest_nav_pose_received_ns = 0
        # Node._context 는 rclpy 자체가 ROS Context를 보관하는 내부 속성이다.
        # 시험 목표 메타데이터는 별도 이름으로 두어 ActionClient가 사용하는
        # ROS Context를 덮어쓰지 않는다.
        self._test_context: dict | None = None
        self._pending_low_obstacle_context: dict | None = None
        self._low_obstacle_confirmed_count = 0

        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.result_pub = self.create_publisher(String, '~/result', 10)
        self.active_pub = self.create_publisher(Bool, '~/active', state_qos)
        self.obstacle_stop_pub = self.create_publisher(
            Bool, '/emergency_stop/obstacle', 10)
        self.create_subscription(String, '~/goto', self._on_goto, 10)
        self.create_subscription(String, '~/goto_pose', self._on_goto_pose, 10)
        self.create_subscription(Bool, '~/cancel', self._on_cancel, 10)
        self.create_subscription(
            GuideState, '/guide_manager/state', self._on_guide_state, state_qos)
        self.create_subscription(Bool, '/battery/low', self._on_battery, state_qos)
        self.create_subscription(
            Bool, '/emergency_stop/state', self._on_emergency, state_qos)
        self.create_subscription(
            Bool, '/auto_localize/active', self._on_localization, state_qos)
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
        self._publish_active()
        self.get_logger().info(
            f'navigation_manager 시작 (robot_id={self.robot_id}, '
            f'waypoint {len(self.waypoints)}개, recovery={self.recovery_mode}, '
            f'planner={self.planner_mode}, '
            f'low_obstacle={self.low_obstacle_mode})')

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
        if (self._active or self._pending_low_obstacle_context is not None
                or self.low_obstacle_driver.active):
            return SetParametersResult(
                successful=False,
                reason='Waypoint 시험 주행 중에는 저상 장애물 모드를 변경할 수 없습니다.',
            )
        self.low_obstacle_mode = requested_mode
        self._low_obstacle_confirmed_count = 0
        self.get_logger().info(
            f'Waypoint 시험 저상 장애물 모드 변경: {self.low_obstacle_mode}')
        return SetParametersResult(successful=True)

    def _waypoint_candidates(self, map_name: str) -> list[Path]:
        relative = Path('config') / 'waypoints' / f'{map_name}_waypoints.yaml'
        candidates: list[Path] = []
        try:
            candidates.append(
                Path(get_package_share_directory('mingky_bringup')) / relative)
        except PackageNotFoundError:
            pass
        for parent in Path(__file__).resolve().parents:
            candidates.append(parent / 'mingky_bringup' / relative)
        return candidates

    def _load_waypoints(self, explicit: str, map_name: str) -> dict:
        if explicit:
            path = Path(explicit).expanduser()
        else:
            path = next(
                (candidate for candidate in self._waypoint_candidates(map_name)
                 if candidate.is_file()),
                None,
            )
        if path is None or not path.is_file():
            self.get_logger().error(
                f'map_name={map_name!r}의 Waypoint 파일을 찾지 못했습니다.')
            return {}
        with path.open(encoding='utf-8') as stream:
            data = yaml.safe_load(stream) or {}
        return data.get('waypoints') or {}

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
        return None

    def _behavior_tree_file(self, *, fallback: bool) -> str:
        if self._behavior_tree_dir is None:
            return ''
        if fallback and self.planner_mode == 'navfn':
            # 빈 값은 Nav2 기본 recovery tree를 뜻한다.
            return ''
        phase = 'recovery' if fallback else 'no_recovery'
        path = self._behavior_tree_dir / (
            f'navigate_{phase}_{self.planner_mode}.xml')
        return str(path) if path.is_file() else ''

    def _on_scan(self, msg: LaserScan) -> None:
        self._latest_scan = msg
        self._latest_scan_received_ns = self.get_clock().now().nanoseconds

    def _on_low_obstacle_range(self, msg: Range) -> None:
        """시험 주행 중 초음파와 LiDAR 차이로 저상 장애물을 판별한다."""
        self.low_obstacle_driver.update_range(msg.range)
        if self.low_obstacle_mode != 'sidestep':
            self._low_obstacle_confirmed_count = 0
            return
        if (
                self.low_obstacle_driver.active
                or self._pending_low_obstacle_context is not None
                or not self._active
                or self._goal_handle is None
                or self._goal_result_future is None
                or self._test_context is None
                or self._battery_low
                or self._emergency
                or self._clinical_active
                or self._localization_active):
            self._low_obstacle_confirmed_count = 0
            return
        now_ns = self.get_clock().now().nanoseconds
        stale_ns = int(self.low_obstacle_scan_stale_sec * 1_000_000_000)
        if (self._latest_scan is None
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
        if not is_low_obstacle(
                float(msg.range),
                lidar_range,
                trigger_distance_m=self.low_obstacle_config.trigger_distance_m,
                lidar_margin_m=self.low_obstacle_config.lidar_margin_m):
            self._low_obstacle_confirmed_count = 0
            return
        self._low_obstacle_confirmed_count += 1
        if self._low_obstacle_confirmed_count < self.low_obstacle_confirmations:
            return
        self._low_obstacle_confirmed_count = 0
        self._request_low_obstacle_avoidance(float(msg.range), lidar_range)

    def _request_low_obstacle_avoidance(
            self, ultrasonic_range: float, lidar_range: float) -> None:
        handle = self._goal_handle
        result_future = self._goal_result_future
        context = self._test_context
        if handle is None or result_future is None or context is None:
            return
        self.get_logger().warn(
            f'저상 장애물 감지(초음파={ultrasonic_range:.2f}m, '
            f'LiDAR={lidar_range:.2f}m); Waypoint 시험 목표를 일시 취소합니다.')
        self._generation += 1
        self._pending_low_obstacle_context = dict(context)
        self._goal_handle = None
        self._goal_result_future = None
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
        except Exception as exc:  # noqa: BLE001
            self._fail_low_obstacle(
                f'Waypoint 시험 목표 취소 요청 실패: {exc}',
                engage_safety_stop=True,
            )
            return
        if not accepted:
            self._fail_low_obstacle(
                'Nav2가 Waypoint 시험 목표 취소를 거부했습니다.',
                engage_safety_stop=True,
            )
            return
        result_future.add_done_callback(
            lambda done: self._on_low_obstacle_nav_cancelled(done, context))

    def _on_low_obstacle_nav_cancelled(self, future, context: dict) -> None:
        if self._pending_low_obstacle_context is None:
            return
        try:
            status = int(future.result().status)
        except Exception as exc:  # noqa: BLE001
            self._fail_low_obstacle(
                f'Waypoint 시험 목표 취소 확인 실패: {exc}',
                engage_safety_stop=True,
            )
            return
        if status != GoalStatus.STATUS_CANCELED:
            self._fail_low_obstacle(
                f'Waypoint 시험 목표가 취소 상태로 끝나지 않았습니다(status={status}).',
                engage_safety_stop=True,
            )
            return
        attempts = int(context.get('low_obstacle_attempts', 0))
        if attempts >= self.low_obstacle_max_sidesteps:
            self._fail_low_obstacle('저상 장애물 최대 회피 횟수에 도달했습니다.')
            return
        self.get_logger().info(
            f'Waypoint 시험 목표 취소 완료; 옆걸음 회피 {attempts + 1}/'
            f'{self.low_obstacle_max_sidesteps}회를 시작합니다.')
        if not self.low_obstacle_driver.start(context.get('low_obstacle_side')):
            self._fail_low_obstacle('저상 장애물 옆걸음 회피를 시작하지 못했습니다.')

    def _on_low_obstacle_sidestep_complete(
            self, outcome: SidestepOutcome) -> None:
        context = self._pending_low_obstacle_context
        if context is None:
            return
        self._pending_low_obstacle_context = None
        if not outcome.succeeded:
            self._finish(
                'failed', LOW_OBSTACLE_ERROR,
                f'저상 장애물 회피 실패: {outcome.reason}')
            return
        if (self._battery_low or self._emergency
                or self._clinical_active or self._localization_active):
            return
        attempts = int(context.get('low_obstacle_attempts', 0)) + 1
        context['low_obstacle_attempts'] = attempts
        context['low_obstacle_side'] = outcome.side
        self._test_context = context
        self.get_logger().info(
            f'옆걸음 회피 완료; 원래 Waypoint 시험 목표를 다시 전송합니다 '
            f'(attempt={attempts}, side={outcome.side}).')
        self._send_original_goal()

    def _fail_low_obstacle(
            self, message: str, *, engage_safety_stop: bool = False) -> None:
        if engage_safety_stop:
            # Nav2 목표가 실제로 멈췄는지 확인할 수 없을 때 직접 동작을
            # 이어가지 않고 기존 안전 게이트를 잠근다.
            self.obstacle_stop_pub.publish(Bool(data=True))
        self._pending_low_obstacle_context = None
        self._finish('failed', LOW_OBSTACLE_ERROR, message)

    def _cancel_low_obstacle_operation(self) -> None:
        self.low_obstacle_driver.cancel()
        self._pending_low_obstacle_context = None
        self._low_obstacle_confirmed_count = 0

    def _on_nav_feedback(self, feedback_msg, generation: int) -> None:
        if generation != self._generation:
            return
        self._latest_nav_pose = feedback_msg.feedback.current_pose
        self._latest_nav_pose_received_ns = self.get_clock().now().nanoseconds

    def _on_guide_state(self, msg: GuideState) -> None:
        active_states = (
            GuideState.SESSION_CONFIRMED,
            GuideState.SESSION_GUIDING,
            GuideState.SESSION_ARRIVED,
            GuideState.SESSION_IN_ROOM,
        )
        clinical_active = msg.session_id > 0 and msg.session_state in active_states
        if clinical_active and not self._clinical_active and self._active:
            self._cancel_active(
                CLINICAL_ERROR,
                '환자 안내가 시작되어 시험 주행을 취소했습니다.',
            )
        self._clinical_active = clinical_active

    def _on_battery(self, msg: Bool) -> None:
        self._battery_low = bool(msg.data)
        if self._battery_low and self._active:
            self._cancel_active(
                SAFETY_ERROR, '저전압으로 시험 주행을 취소했습니다.')

    def _on_emergency(self, msg: Bool) -> None:
        self._emergency = bool(msg.data)
        if self._emergency and self._active:
            self._cancel_active(
                SAFETY_ERROR, '비상정지로 시험 주행을 취소했습니다.')

    def _on_localization(self, msg: Bool) -> None:
        self._localization_active = bool(msg.data)
        if self._localization_active and self._active:
            self._cancel_active(
                LOCALIZATION_ERROR,
                'AMCL 자동 재탐색이 시작되어 시험 주행을 취소했습니다.')

    def _on_cancel(self, msg: Bool) -> None:
        if msg.data and self._active:
            self._cancel_active(
                CLINICAL_ERROR,
                '상위 작업 요청으로 시험 주행을 취소했습니다.',
            )

    def _on_goto(self, msg: String) -> None:
        name = msg.data.strip()
        raw_waypoint = self.waypoints.get(name)
        if raw_waypoint is None:
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -2,
                'message': '시험 Waypoint를 찾을 수 없습니다.',
            })
            return
        try:
            waypoint = {
                key: float(raw_waypoint[key]) for key in ('x', 'y', 'yaw')
            }
        except (KeyError, TypeError, ValueError) as exc:
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -2,
                'message': f'저장 Waypoint 형식이 올바르지 않습니다: {exc}',
            })
            return
        if not all(math.isfinite(value) for value in waypoint.values()):
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -2,
                'message': '저장 Waypoint 좌표는 유한한 수여야 합니다.',
            })
            return
        self._start_test(name, waypoint)

    def _on_goto_pose(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
            name = str(payload.get('name') or 'waypoint_draft').strip()
            waypoint = {
                'x': float(payload['x']),
                'y': float(payload['y']),
                'yaw': float(payload['yaw']),
            }
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._publish_result('failed', {
                'waypoint_name': 'waypoint_draft',
                'error_code': -2,
                'message': f'시험 Waypoint 형식이 올바르지 않습니다: {exc}',
            })
            return
        if not all(math.isfinite(value) for value in waypoint.values()):
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -2,
                'message': '시험 Waypoint 좌표는 유한한 수여야 합니다.',
            })
            return
        self._start_test(name, waypoint)

    def _start_test(self, name: str, waypoint: dict) -> None:
        if self._active:
            self._publish_result('rejected', {
                'waypoint_name': name,
                'error_code': BUSY_ERROR,
                'message': '다른 Waypoint 시험 주행이 진행 중입니다.',
            })
            return
        if self._clinical_active:
            self._publish_result('rejected', {
                'waypoint_name': name,
                'error_code': CLINICAL_ERROR,
                'message': (
                    '환자 안내 중에는 Waypoint 시험 주행을 '
                    '시작할 수 없습니다.'),
            })
            return
        if self._localization_active:
            self._publish_result('rejected', {
                'waypoint_name': name,
                'error_code': LOCALIZATION_ERROR,
                'message': (
                    'AMCL 자동 재탐색 중에는 Waypoint 시험 주행을 '
                    '시작할 수 없습니다.'),
            })
            return
        if self._battery_low or self._emergency:
            self._publish_result('rejected', {
                'waypoint_name': name,
                'error_code': SAFETY_ERROR,
                'message': (
                    '안전 정지 상태에서는 Waypoint 시험 주행을 '
                    '시작할 수 없습니다.'),
            })
            return
        if not self.nav.wait_for_server(timeout_sec=3.0):
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -3,
                'message': 'Nav2 액션 서버가 없습니다.',
            })
            return

        self._active = True
        self._test_context = {
            'waypoint_name': name,
            'x': float(waypoint['x']),
            'y': float(waypoint['y']),
            'yaw': float(waypoint['yaw']),
            'recovery_attempt': 0,
            'recovery_failures': {},
            'low_obstacle_attempts': 0,
            'low_obstacle_side': None,
        }
        self._publish_active()
        self._publish_result('started', self._public_test_context())
        self._send_original_goal()

    def _goal_pose(self) -> PoseStamped:
        context = self._test_context or {}
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(context['x'])
        pose.pose.position.y = float(context['y'])
        z, w = _yaw_to_quat(float(context['yaw']))
        pose.pose.orientation.z = z
        pose.pose.orientation.w = w
        return pose

    def _send_original_goal(self) -> None:
        if not self._active or self._test_context is None:
            return
        if not self.nav.wait_for_server(timeout_sec=3.0):
            self._finish('failed', -3, 'Nav2 액션 서버가 없습니다.')
            return
        goal = NavigateToPose.Goal()
        goal.pose = self._goal_pose()
        if self.recovery_mode == 'adaptive' or self.planner_mode == 'smac2d':
            goal.behavior_tree = self._behavior_tree_file(
                fallback=self.recovery_mode == 'default')

        self._generation += 1
        generation = self._generation
        self._goal_handle = None
        self._goal_result_future = None
        future = self.nav.send_goal_async(
            goal,
            feedback_callback=lambda feedback: self._on_nav_feedback(
                feedback, generation),
        )
        future.add_done_callback(
            lambda done: self._on_goal_response(done, generation))

    def _on_goal_response(self, future, generation: int) -> None:
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001
            if generation == self._generation:
                self._finish('failed', -4, f'목표 전송 중 예외: {exc}')
            return
        if generation != self._generation:
            if handle.accepted:
                handle.cancel_goal_async()
            return
        if not handle.accepted:
            self._finish('failed', -1, 'Nav2가 시험 목표를 거부했습니다.')
            return
        self._goal_handle = handle
        result = handle.get_result_async()
        self._goal_result_future = result
        result.add_done_callback(
            lambda done: self._on_goal_result(done, generation))

    def _on_goal_result(self, future, generation: int) -> None:
        if generation != self._generation:
            return
        self._goal_handle = None
        self._goal_result_future = None
        try:
            status = int(future.result().status)
        except Exception as exc:  # noqa: BLE001
            self._finish('failed', -4, f'결과 수신 중 예외: {exc}')
            return
        if status == GoalStatus.STATUS_SUCCEEDED:
            self._finish('succeeded', 0, 'Waypoint 시험 주행에 도착했습니다.')
            return
        context = self._test_context
        if (status == GoalStatus.STATUS_ABORTED
                and self.recovery_mode == 'adaptive'
                and context is not None):
            attempt = int(context.get('recovery_attempt', 0))
            failures = dict(context.get('recovery_failures') or {})
            if attempt >= self.recovery_max_attempts:
                self._finish(
                    'failed', RECOVERY_ERROR,
                    f'Adaptive Recovery 최대 {self.recovery_max_attempts}회 후에도 '
                    'Waypoint에 도달하지 못했습니다.')
                return
            if self._start_adaptive_recovery(attempt, failures):
                return
            self._schedule_recovery_retry(
                attempt + 1,
                failures,
                '최신 LiDAR·현재 위치 또는 안전한 탈출 후보를 '
                '확보하지 못했습니다.',
            )
            return
        self._finish(
            'failed', status, f'Waypoint 시험 주행 실패 (status={status})')

    def _start_adaptive_recovery(
            self, recovery_attempt: int, failures: dict[str, int]) -> bool:
        """현재 위치에서 탈출 후보를 만들고 경로 검증을 시작한다."""
        if (not self._active or self._test_context is None
                or self._battery_low or self._emergency
                or self._clinical_active or self._localization_active):
            return False
        now_ns = self.get_clock().now().nanoseconds
        stale_ns = int(self.recovery_scan_stale_sec * 1_000_000_000)
        if (self._latest_scan is None or self._latest_nav_pose is None
                or now_ns - self._latest_scan_received_ns > stale_ns
                or now_ns - self._latest_nav_pose_received_ns > stale_ns):
            self.get_logger().warn(
                'LiDAR 또는 현재 위치가 오래되어 Adaptive Recovery를 기다립니다.')
            return False
        if not self.path_planner.wait_for_server(timeout_sec=1.0):
            self.get_logger().warn(
                'compute_path_to_pose 액션 서버가 없어 '
                'Adaptive Recovery를 기다립니다.')
            return False

        pose = self._latest_nav_pose.pose
        robot_yaw = _quat_to_yaw(pose.orientation.z, pose.orientation.w)
        map_goal_angle = math.atan2(
            float(self._test_context['y']) - pose.position.y,
            float(self._test_context['x']) - pose.position.x,
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

        recovery = {
            'recovery_attempt': recovery_attempt,
            'failures': failures,
            'candidates': candidates,
            'index': 0,
            'robot_x': float(pose.position.x),
            'robot_y': float(pose.position.y),
            'robot_yaw': robot_yaw,
            'generation': self._generation,
        }
        self.get_logger().warn(
            f'Waypoint Adaptive Recovery {recovery_attempt + 1}/'
            f'{self.recovery_max_attempts}: '
            f'{len(candidates)}개 탈출 후보 경로를 검증합니다.')
        self._validate_next_recovery_candidate(recovery)
        return True

    def _candidate_pose(
            self, recovery: dict, candidate: EscapeCandidate) -> PoseStamped:
        x, y, yaw = candidate_to_map(
            candidate,
            robot_x=recovery['robot_x'],
            robot_y=recovery['robot_y'],
            robot_yaw=recovery['robot_yaw'],
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

    def _validate_next_recovery_candidate(self, recovery: dict) -> None:
        if (not self._active
                or recovery['generation'] != self._generation):
            return
        index = int(recovery['index'])
        candidates = recovery['candidates']
        if index >= len(candidates):
            self._schedule_recovery_retry(
                int(recovery['recovery_attempt']) + 1,
                recovery['failures'],
                '검증 가능한 탈출 경로가 없습니다.',
            )
            return
        candidate = candidates[index]
        recovery['index'] = index + 1
        goal = ComputePathToPose.Goal()
        goal.goal = self._candidate_pose(recovery, candidate)
        goal.planner_id = (
            'Smac2D' if self.planner_mode == 'smac2d' else 'GridBased')
        goal.use_start = False
        future = self.path_planner.send_goal_async(goal)
        future.add_done_callback(
            lambda done: self._on_recovery_path_response(
                done, recovery, candidate))

    def _on_recovery_path_response(
            self, future, recovery: dict, candidate: EscapeCandidate) -> None:
        if recovery['generation'] != self._generation:
            return
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(
                f'탈출 경로 요청 실패({candidate.name}): {exc}')
            self._reject_recovery_candidate(recovery, candidate)
            return
        if not handle.accepted:
            self._reject_recovery_candidate(recovery, candidate)
            return
        result = handle.get_result_async()
        result.add_done_callback(
            lambda done: self._on_recovery_path_result(
                done, recovery, candidate))

    def _on_recovery_path_result(
            self, future, recovery: dict, candidate: EscapeCandidate) -> None:
        if recovery['generation'] != self._generation:
            return
        try:
            wrapped = future.result()
            path_result = wrapped.result
            valid = (
                wrapped.status == GoalStatus.STATUS_SUCCEEDED
                and path_result.error_code == ComputePathToPose.Result.NONE
                and len(path_result.path.poses) >= 2
            )
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(
                f'탈출 경로 결과 실패({candidate.name}): {exc}')
            valid = False
        if not valid:
            self._reject_recovery_candidate(recovery, candidate)
            return
        self.get_logger().info(
            f'탈출 후보 선택: {candidate.name}, 거리={candidate.distance_m:.2f}m, '
            f'여유={candidate.clearance_m:.2f}m')
        self._send_recovery_goal(recovery, candidate)

    def _reject_recovery_candidate(
            self, recovery: dict, candidate: EscapeCandidate) -> None:
        failures = recovery['failures']
        failures[candidate.name] = failures.get(candidate.name, 0) + 1
        self._validate_next_recovery_candidate(recovery)

    def _send_recovery_goal(
            self, recovery: dict, candidate: EscapeCandidate) -> None:
        self._generation += 1
        recovery['generation'] = self._generation
        recovery['active_candidate'] = candidate
        goal = NavigateToPose.Goal()
        goal.pose = self._candidate_pose(recovery, candidate)
        goal.behavior_tree = self._behavior_tree_file(fallback=False)
        generation = self._generation
        future = self.nav.send_goal_async(
            goal,
            feedback_callback=lambda feedback: self._on_nav_feedback(
                feedback, generation),
        )
        future.add_done_callback(
            lambda done: self._on_recovery_goal_response(done, recovery))

    def _on_recovery_goal_response(self, future, recovery: dict) -> None:
        if recovery['generation'] != self._generation:
            return
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f'탈출 목표 전송 실패: {exc}')
            self._recovery_motion_failed(recovery)
            return
        if not handle.accepted:
            self._recovery_motion_failed(recovery)
            return
        self._goal_handle = handle
        result = handle.get_result_async()
        result.add_done_callback(
            lambda done: self._on_recovery_goal_result(done, recovery))

    def _on_recovery_goal_result(self, future, recovery: dict) -> None:
        if recovery['generation'] != self._generation:
            return
        self._goal_handle = None
        try:
            status = int(future.result().status)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f'탈출 이동 결과 실패: {exc}')
            self._recovery_motion_failed(recovery)
            return
        if status != GoalStatus.STATUS_SUCCEEDED:
            self._recovery_motion_failed(recovery)
            return
        if self._test_context is None:
            return
        attempt = int(recovery['recovery_attempt']) + 1
        self._test_context['recovery_attempt'] = attempt
        self._test_context['recovery_failures'] = dict(recovery['failures'])
        self.get_logger().info(
            '임시 탈출 지점 도착; 원래 Waypoint 시험 목표를 '
            '다시 전송합니다.')
        self._send_original_goal()

    def _recovery_motion_failed(self, recovery: dict) -> None:
        candidate = recovery['active_candidate']
        failures = recovery['failures']
        failures[candidate.name] = failures.get(candidate.name, 0) + 1
        next_attempt = int(recovery['recovery_attempt']) + 1
        if next_attempt < self.recovery_max_attempts and self._start_adaptive_recovery(
                next_attempt, failures):
            return
        self._schedule_recovery_retry(
            next_attempt, failures, '탈출 지점 이동에 실패했습니다.')

    def _schedule_recovery_retry(
            self, recovery_attempt: int, failures: Mapping[str, int],
            reason: str) -> None:
        if not self._active or self._test_context is None:
            return
        if recovery_attempt >= self.recovery_max_attempts:
            self._finish(
                'failed', RECOVERY_ERROR,
                f'{reason} Adaptive Recovery 최대 '
                f'{self.recovery_max_attempts}회에 도달했습니다.')
            return
        self._cancel_recovery_retry()
        self._generation += 1
        generation = self._generation
        self._goal_handle = None
        self._goal_result_future = None
        self._test_context['recovery_attempt'] = recovery_attempt
        self._test_context['recovery_failures'] = dict(failures)
        self.get_logger().warn(
            f'{reason} {self.recovery_retry_delay_sec:.1f}초 정지 후 '
            '원래 Waypoint를 다시 시도합니다.')
        self._recovery_retry_timer = self.create_timer(
            self.recovery_retry_delay_sec,
            lambda: self._retry_original_goal(generation),
        )

    def _retry_original_goal(self, generation: int) -> None:
        self._cancel_recovery_retry()
        if generation != self._generation or not self._active:
            return
        if (self._battery_low or self._emergency
                or self._clinical_active or self._localization_active):
            return
        self._send_original_goal()

    def _cancel_recovery_retry(self) -> None:
        if self._recovery_retry_timer is None:
            return
        self._recovery_retry_timer.cancel()
        self.destroy_timer(self._recovery_retry_timer)
        self._recovery_retry_timer = None

    def _public_test_context(self) -> dict:
        context = self._test_context or {}
        return {
            key: context[key]
            for key in ('waypoint_name', 'x', 'y', 'yaw')
            if key in context
        }

    def _cancel_active(self, error_code: int, message: str) -> None:
        context = self._public_test_context()
        self._generation += 1
        self._cancel_recovery_retry()
        self._cancel_low_obstacle_operation()
        if self._goal_handle is not None:
            self._goal_handle.cancel_goal_async()
        self._goal_handle = None
        self._goal_result_future = None
        self._active = False
        self._test_context = None
        self._publish_active()
        self._publish_result('failed', {
            **context,
            'error_code': error_code,
            'message': message,
        })

    def _finish(self, status: str, error_code: int, message: str) -> None:
        context = self._public_test_context()
        self._cancel_recovery_retry()
        self._cancel_low_obstacle_operation()
        self._goal_handle = None
        self._goal_result_future = None
        self._active = False
        self._test_context = None
        self._publish_active()
        self._publish_result(status, {
            **context,
            'error_code': error_code,
            'message': message,
        })

    def _publish_active(self) -> None:
        self.active_pub.publish(Bool(data=self._active))

    def _publish_result(self, status: str, payload: dict) -> None:
        self.result_pub.publish(String(data=json.dumps({
            'status': status,
            **payload,
        }, ensure_ascii=False)))


def main() -> None:
    rclpy.init()
    node = NavigationManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
