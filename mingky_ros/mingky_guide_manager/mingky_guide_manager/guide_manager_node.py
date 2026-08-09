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
    - 세션 시작/단계 완료를 토픽으로 수동 트리거 (QR·마커 노드가 붙기 전까지)
"""

import math
from pathlib import Path
from typing import Mapping

import rclpy
import yaml
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import ComputePathToPose, NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool, Float32, String

from mingky_interfaces.msg import GuideState, SessionStart
from mingky_smart_recovery.selector import (
    EscapeCandidate,
    candidate_to_map,
    select_escape_candidates,
)

from .event_publisher import EventPublisher


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
        self.declare_parameter('recovery_max_attempts', 3)
        self.declare_parameter('recovery_candidate_limit', 4)

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
        self.recovery_max_attempts = max(
            1, int(self.get_parameter('recovery_max_attempts').value))
        self.recovery_candidate_limit = max(
            1, int(self.get_parameter('recovery_candidate_limit').value))

        self.events = EventPublisher(
            self, self.robot_id, self.get_parameter('event_codes_file').value)

        self.waypoints = self._load_waypoints(
            self.get_parameter('waypoints_file').value,
            self.get_parameter('map_name').value)

        # --- 상태. 이 노드만 쓴다 ---
        self.robot_state = GuideState.ROBOT_IDLE
        self.session_state = GuideState.SESSION_NONE
        self.session_id = 0
        self.patient_id = ''
        self.current_visit = ''
        self.voltage = float('nan')
        self.percent = -1
        self._battery_alarm = False
        self._emergency_engaged = False
        self._emergency_reason = 'emergency_stop'
        self._dock_pending = False
        self._dock_attempt = 0
        self._dock_retry_timer = None
        self._latest_scan = None
        self._latest_scan_received_ns = 0
        self._latest_nav_pose = None
        self._latest_nav_pose_received_ns = 0
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

        # QR·마커 노드가 붙기 전까지 손으로 흘려넣기 위한 입구.
        # 나중에 그 노드들이 대체하면 이 두 개는 지운다.
        self.create_subscription(String, '~/start_session', self._on_start_session, 10)
        self.create_subscription(String, '~/goto', self._on_goto, 10)

        # QR 노드가 백엔드 응답을 파싱해 흘려주는 세션 개시 신호.
        # 지금은 수신·저장·로그만 한다. Nav2 자동 트리거는 아직 붙이지 않았다.
        # session_start 자체는 이벤트가 아니라 로봇 내부 배관 신호라 events 로는 쏘지 않는다.
        # 상응하는 session.started 는 백엔드가 POST /qr/scan 안에서 이미 반영한 뒤라 중복이다.
        self.session_visits: list[str] = []
        self.create_subscription(
            SessionStart, '/qr_reader_node/session_start', self._on_session_start, 10)

        self.nav = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.path_planner = ActionClient(
            self, ComputePathToPose, 'compute_path_to_pose')

        self.create_timer(1.0, self._publish_state)
        self.get_logger().info(
            f'guide_manager 시작 (robot_id={self.robot_id}, '
            f'waypoint {len(self.waypoints)}개, 충전소={self.charging_waypoint}, '
            f'recovery={self.recovery_mode}, planner={self.planner_mode})')

    # ------------------------------------------------------------------ 설정

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

        # 파일은 있는데 비어 있는 경우가 제일 찾기 어렵다. 노드는 정상 기동하고
        # 목표를 보낼 때마다 '알 수 없는 waypoint' 만 나온다. 기동 시에 짚는다.
        if not waypoints:
            self.get_logger().error(
                f'{path} 에 waypoint 가 없습니다. 오래된 빌드 산출물일 수 있으니 '
                'install/ 을 지우고 다시 빌드해 보세요.')
        else:
            self.get_logger().info(f'waypoints 로드: {path} ({len(waypoints)}개)')
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
            self._cancel_dock_retry()
            self.events.publish(
                'robot.battery_recovered', {'percent': max(self.percent, 0)})
            if self.robot_state == GuideState.ROBOT_BATTERY_LOW:
                self.robot_state = GuideState.ROBOT_IDLE
            self.get_logger().info(f'배터리 저전압 상태 해제 ({self.percent}%)')
            return

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
        self._dock_attempt = 0
        if self._emergency_engaged:
            self._dock_pending = True
            self.get_logger().warn('비상정지 해제 뒤 충전소 복귀를 시작합니다.')
        else:
            self._return_to_dock()

    # --------------------------------------------------------------- 비상정지

    def _on_emergency_reason(self, msg: String):
        if msg.data:
            self._emergency_reason = msg.data

    def _on_emergency_state(self, msg: Bool):
        if msg.data == self._emergency_engaged:
            return
        self._emergency_engaged = msg.data

        if msg.data:
            # 진행 중인 Nav2 콜백은 EmergencyStop 이 취소한 뒤 늦게 도착할 수 있다.
            # 세대를 넘겨 그 결과가 현재 상태를 덮어쓰지 못하게 한다.
            self._nav_generation += 1
            self._cancel_dock_retry()
            self._dock_pending = self._battery_alarm
            self.robot_state = GuideState.ROBOT_PAUSED
            self.events.publish(
                'robot.paused', {'reason': self._emergency_reason}, self.session_id)
            return

        self.events.publish(
            'robot.resumed', {'reason': self._emergency_reason}, self.session_id)
        if self._battery_alarm:
            self.robot_state = GuideState.ROBOT_BATTERY_LOW
            self._dock_pending = False
            self._return_to_dock()
        else:
            # 취소된 안내 목표는 안전상 자동 재개하지 않는다.
            self.robot_state = GuideState.ROBOT_IDLE
        self._emergency_reason = 'emergency_stop'

    # ------------------------------------------------------------------ 세션

    def _on_start_session(self, msg: String):
        """임시 입구. 나중에 QR 노드가 대체한다."""
        if self._battery_alarm:
            self.get_logger().warn('배터리 부족 상태에서는 세션을 시작할 수 없습니다.')
            return
        self.patient_id = msg.data.strip()
        self.session_state = GuideState.SESSION_CONFIRMED
        self.events.publish(
            'session.started',
            {'patient_id': self.patient_id, 'marker_id': None},
            self.session_id)
        self.get_logger().info(f'세션 시작: {self.patient_id}')

    def _on_goto(self, msg: String):
        self.send_goal(msg.data.strip())

    def _on_session_start(self, msg: SessionStart):
        """QR 노드에서 세션 정보가 들어왔을 때. 저장만 하고 주행은 아직 트리거하지 않는다."""
        if self._battery_alarm:
            # QR API가 이미 DB 세션을 만들었으므로 무시만 하면 활성 세션이 남는다.
            self.events.publish(
                'session.ended', {'end_reason': 'battery'}, int(msg.session_id))
            self.get_logger().warn(
                f'배터리 부족으로 새 세션 {msg.session_id} 을 즉시 종료합니다.')
            return
        self.session_id = int(msg.session_id)
        self.patient_id = str(msg.patient_id)
        self.session_visits = list(msg.visit_names)
        # 현재 방문지는 visit_name 이지 waypoint 키가 아니다. Nav2 매핑은 후속 작업.
        idx = max(int(msg.current_step_order) - 1, 0)
        self.current_visit = (
            self.session_visits[idx] if 0 <= idx < len(self.session_visits) else '')
        self.session_state = GuideState.SESSION_CONFIRMED
        self.get_logger().info(
            f'session_start 수신: session_id={self.session_id} '
            f'patient={self.patient_id} step={msg.current_step_order}/'
            f'{len(self.session_visits)} current_visit={self.current_visit!r} '
            f'visits={self.session_visits}')

    # ------------------------------------------------------------------ 주행

    def _on_scan(self, msg: LaserScan) -> None:
        self._latest_scan = msg
        self._latest_scan_received_ns = self.get_clock().now().nanoseconds

    def _on_nav_feedback(self, feedback_msg, generation: int) -> None:
        if generation != self._nav_generation:
            return
        self._latest_nav_pose = feedback_msg.feedback.current_pose
        self._latest_nav_pose_received_ns = self.get_clock().now().nanoseconds

    def send_goal(self, waypoint_name: str) -> None:
        """환자 안내 목적지로 이동한다."""
        if self._battery_alarm:
            self.get_logger().warn('배터리 부족 상태에서는 안내 목표를 보낼 수 없습니다.')
            return
        self._send_nav_goal(waypoint_name, is_dock=False, session_id=self.session_id)

    def _return_to_dock(self) -> None:
        """현재 안내 목표를 선점하고 이 로봇에 배정된 충전소로 복귀한다."""
        if not self._battery_alarm:
            return
        if self._emergency_engaged:
            self._dock_pending = True
            return
        self._dock_pending = False
        self._dock_attempt += 1
        self.get_logger().warn(
            f'충전소 복귀 시도 {self._dock_attempt}/{self.dock_max_attempts}')
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
        self.robot_state = GuideState.ROBOT_BATTERY_LOW
        if (retryable and self._battery_alarm and not self._emergency_engaged
                and self._dock_attempt < self.dock_max_attempts):
            self.get_logger().warn(
                f'{self.dock_retry_delay:.1f}초 뒤 충전소 복귀를 재시도합니다.')
            self._dock_retry_timer = self.create_timer(
                self.dock_retry_delay, self._retry_dock)
            return
        self.events.publish(
            'dock.return_failed',
            {'station_name': waypoint_name, 'error_code': int(error_code)})

    def _send_nav_goal(
            self, waypoint_name: str, *, is_dock: bool, session_id: int,
            recovery_attempt: int = 0,
            recovery_failures: Mapping[str, int] | None = None,
            announce: bool = True, fallback: bool = False) -> None:
        wp = self.waypoints.get(waypoint_name)
        if wp is None:
            self.get_logger().error(f'알 수 없는 waypoint: {waypoint_name}')
            if is_dock:
                self._dock_failed(waypoint_name, -2, retryable=False)
            return
        if not self.nav.wait_for_server(timeout_sec=3.0):
            self.get_logger().error('navigate_to_pose 액션 서버가 없습니다.')
            if is_dock:
                self._dock_failed(waypoint_name, -3, retryable=True)
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
        if not is_dock and (self.recovery_mode == 'adaptive'
                            or self.planner_mode == 'smac2d'):
            goal.behavior_tree = self._behavior_tree_file(
                fallback=fallback or self.recovery_mode == 'default')

        self._nav_generation += 1
        generation = self._nav_generation
        self.current_visit = waypoint_name
        if is_dock:
            self.robot_state = GuideState.ROBOT_BATTERY_LOW
            self.events.publish(
                'dock.return_started', {'station_name': waypoint_name})
        else:
            self.robot_state = GuideState.ROBOT_MOVING
            self.session_state = GuideState.SESSION_GUIDING
            if announce:
                self.events.publish(
                    'nav.goal_sent', {'visit_name': waypoint_name}, session_id)

        future = self.nav.send_goal_async(
            goal,
            feedback_callback=lambda feedback: self._on_nav_feedback(
                feedback, generation),
        )
        future.add_done_callback(
            lambda done: self._on_goal_response(
                done, generation, waypoint_name, is_dock, session_id,
                recovery_attempt, dict(recovery_failures or {}), fallback))

    def _on_goal_response(
            self, future, generation: int, waypoint_name: str,
            is_dock: bool, session_id: int, recovery_attempt: int = 0,
            recovery_failures: Mapping[str, int] | None = None,
            fallback: bool = False):
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001 - Nav2 오류를 이벤트로 보고
            if generation != self._nav_generation:
                return
            self._goal_failed(
                waypoint_name, is_dock, session_id, -4,
                f'목표 전송 중 예외: {exc}')
            return

        # 충전 복귀 등 새 목표가 이미 생겼다면 이전 목표 결과는 상태에 반영하지
        # 않는다. 서버가 아직 이전 목표를 잡고 있으면 명시적으로 취소한다.
        if generation != self._nav_generation:
            if handle.accepted:
                handle.cancel_goal_async()
            return
        if not handle.accepted:
            self._goal_failed(
                waypoint_name, is_dock, session_id, -1, 'Nav2 가 목표를 거부했습니다.')
            return
        result = handle.get_result_async()
        result.add_done_callback(
            lambda done: self._on_goal_result(
                done, generation, waypoint_name, is_dock, session_id,
                recovery_attempt, dict(recovery_failures or {}), fallback))

    def _on_goal_result(
            self, future, generation: int, waypoint_name: str,
            is_dock: bool, session_id: int, recovery_attempt: int = 0,
            recovery_failures: Mapping[str, int] | None = None,
            fallback: bool = False):
        if generation != self._nav_generation:
            return
        try:
            status = future.result().status
        except Exception as exc:  # noqa: BLE001
            self._goal_failed(
                waypoint_name, is_dock, session_id, -4,
                f'결과 수신 중 예외: {exc}')
            return

        # action_msgs/GoalStatus.STATUS_SUCCEEDED == 4
        if status == 4:
            if is_dock:
                self._cancel_dock_retry()
                # 좌표 도착만으로 충전 전류가 흐른다고 단정할 수는 없다.
                self.robot_state = GuideState.ROBOT_WAITING
                self.events.publish(
                    'dock.return_succeeded', {'station_name': waypoint_name})
                self.get_logger().info(f'충전소 도착: {waypoint_name}')
            else:
                self.robot_state = GuideState.ROBOT_WAITING
                self.session_state = GuideState.SESSION_ARRIVED
                self.events.publish(
                    'nav.goal_succeeded', {'visit_name': waypoint_name}, session_id)
                self.get_logger().info(f'도착: {waypoint_name}')
        else:
            if (not is_dock and not fallback and status == 6
                    and self._start_adaptive_recovery(
                        waypoint_name, session_id, recovery_attempt,
                        dict(recovery_failures or {}))):
                return
            if (not is_dock and not fallback and status == 6
                    and self.recovery_mode == 'adaptive'):
                self._send_fallback_goal(
                    waypoint_name, session_id, recovery_attempt,
                    dict(recovery_failures or {}))
                return
            self._goal_failed(
                waypoint_name, is_dock, session_id, int(status),
                f'주행 실패 (status={status})')

    def _start_adaptive_recovery(
            self, waypoint_name: str, session_id: int, recovery_attempt: int,
            failures: dict[str, int]) -> bool:
        """현재 위치에서 안전한 탈출 후보를 만들고 경로 검증을 시작한다."""
        if self.recovery_mode != 'adaptive':
            return False
        if self._battery_alarm or self._emergency_engaged:
            return False
        if recovery_attempt >= self.recovery_max_attempts:
            self.get_logger().error(
                f'적응형 복구 최대 횟수({self.recovery_max_attempts})에 도달했습니다.')
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
        candidates = select_escape_candidates(
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
        )[:self.recovery_candidate_limit]
        if not candidates:
            self.get_logger().warn('안전 여유를 만족하는 탈출 후보가 없습니다.')
            return False

        context = {
            'waypoint_name': waypoint_name,
            'session_id': session_id,
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
            f'적응형 복구 {recovery_attempt + 1}/{self.recovery_max_attempts}: '
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
            self._send_fallback_goal(
                context['waypoint_name'],
                context['session_id'],
                context['recovery_attempt'],
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
        self.get_logger().info('임시 탈출 지점 도착; 원래 안내 목표를 다시 시도합니다.')
        self._send_nav_goal(
            context['waypoint_name'],
            is_dock=False,
            session_id=context['session_id'],
            recovery_attempt=context['recovery_attempt'] + 1,
            recovery_failures=context['failures'],
            announce=False,
        )

    def _recovery_motion_failed(self, context: dict) -> None:
        candidate = context['active_candidate']
        failures = context['failures']
        failures[candidate.name] = failures.get(candidate.name, 0) + 1
        next_attempt = context['recovery_attempt'] + 1
        if self._start_adaptive_recovery(
                context['waypoint_name'], context['session_id'],
                next_attempt, failures):
            return
        self._send_fallback_goal(
            context['waypoint_name'], context['session_id'], next_attempt, failures)

    def _send_fallback_goal(
            self, waypoint_name: str, session_id: int, recovery_attempt: int,
            failures: Mapping[str, int]) -> None:
        if self._battery_alarm or self._emergency_engaged:
            self._goal_failed(
                waypoint_name, False, session_id, 6,
                '안전 상태 변경으로 최종 Nav2 복구를 시작하지 않습니다.')
            return
        self.get_logger().warn(
            '적응형 탈출을 완료하지 못해 기존 Spin/Wait/Backup 복구를 실행합니다.')
        self._send_nav_goal(
            waypoint_name,
            is_dock=False,
            session_id=session_id,
            recovery_attempt=recovery_attempt,
            recovery_failures=failures,
            announce=False,
            fallback=True,
        )

    def _goal_failed(
            self, waypoint_name: str, is_dock: bool, session_id: int,
            error_code: int, message: str) -> None:
        self.get_logger().error(message)
        if is_dock:
            self._dock_failed(waypoint_name, error_code, retryable=True)
        else:
            self.robot_state = GuideState.ROBOT_IDLE
            self.events.publish(
                'nav.goal_aborted',
                {'visit_name': waypoint_name, 'error_code': int(error_code)},
                session_id)

    # ------------------------------------------------------------------ 발행

    def _publish_state(self):
        msg = GuideState()
        msg.robot_id = self.robot_id
        msg.robot_state = self.robot_state
        msg.session_state = self.session_state
        msg.session_id = self.session_id
        msg.patient_id = self.patient_id
        msg.current_visit = self.current_visit
        msg.battery_voltage = float(self.voltage)
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
