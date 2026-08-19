"""AMCL 초기 위치를 자동으로 잡는다. RViz 의 2D Pose Estimate 손조작을 없앤다.

    /mode·비상정지·주행 상태 확인
      → 현재 OccupancyGrid에서 만든 distance field로 전역 Top-K 검색
      → 끝점 일치도와 free-space 모순, Top1/Top2 점수 차이 확인
      → 애매하면 회전 없이 안전한 앞/뒤로 5cm 이동
      → odom 상대 이동과 새 scan으로 후보를 계속 제거 (최대 15cm)
      → 같은 후보가 연속 확인된 경우에만 /initialpose 발행
      → AMCL particle이 seed 주변에서 안정화되는지 최종 확인
      → 끝까지 애매하면 위치를 찍지 않고 실패 이유를 이벤트로 남김

기본은 자동 실행 없이 대기만 한다(``auto_run_on_start`` 기본값 false) --
전역 재탐색은 로봇을 짧게 움직일 수 있으므로, 관제
화면에서 운영자가 ``auto_localize/trigger`` 서비스로 필요할 때 명시적으로
실행하는 편이 안전하다 (관제 팀 리뷰, 2026-08-12). 부팅 시 자동 실행이
필요한 로봇/환경에서만 launch 인자로 켠다.

## 왜 5cm씩만 움직이는가

30cm 통로에서 기존 40cm 왕복은 크고, 한 번의 수렴 여부만 확인해 이동 중
얻은 scan 정보를 버렸다. 이제 5cm마다 멈춰 새 scan으로 후보를 재평가하고
최대 15cm 안에서 구분되지 않으면 실패한다. 회전은 좁은 통로에서 footprint
위험을 키우므로 1차 구현에서는 앞/뒤 직선 이동만 허용한다.

## 왜 좌표를 Nav2 에게 안 넘기는가 (2026-08-11, 실제 로봇에서 두 번째 교훈)

한 번은 Nav2(NavigateToPose)에게 "이 지도 좌표로 가라"고 맡겨봤다. Nav2 의
controller_server 가 실시간으로 장애물을 감시하니 안전할 거라 생각했는데,
**그 지도 좌표 자체가 AMCL 이 아직 확신 못 하는 위치를 기준으로 계산된
것**이었다. 지금 우리가 이 노드를 돌리는 상황 자체가 "위치를 모른다"는
바로 그 상황이라, 지도 좌표를 신뢰하는 방식과 애초에 안 맞았다. 실제로
로봇이 의도한 거리보다 훨씬 멀리 이동했다.

그래서 오도메트리 기반(상대 이동량)으로 되돌아왔다 -- 지금 위치가 틀려도
"여기서부터 몇 미터"는 정확하다. 대신 이동 전 한 번 잰 라이다 여유만
믿지 않고, **이동하는 동안에도 진행 방향의 라이다를 계속 확인해서 가까운
장애물이 나타나면 즉시 멈춘다.**

## 왜 별도 twist_mux 채널인가

이 노드가 만드는 이동은 사람도 아니고 Nav2 경로계획도 아니다. cmd_vel_teleop
로 쏘면 사람 조작과 충돌하고, cmd_vel_smoothed 로 쏘면 Nav2인 척하게 된다.
twist_mux 에 별도 입력(cmd_vel_localize_probe)이 필요하다 — manual lock 이
이것도 같이 막아야 사람이 몰고 있을 때 로봇이 혼자 움직이지 않는다.

## 왜 Nav2 주행 중에는 아예 시작을 안 하는가 (관제 팀 리뷰, 2026-08-12)

twist_mux 채널 우선순위(probe=20 > nav=10)만 믿으면, 우리가 명령을 쏘는
그 순간에는 우리가 이기지만 "Nav2 가 환자를 안내하러 가는 도중에 로봇이
갑자기 이동하기 시작"하는 그림 자체는 막지 못한다. 그래서
guide_manager/navigation_manager 가 서로에게 쓰는 것과 같은 상태 토픽
(``/guide_manager/state`` 의 ``ROBOT_MOVING``, ``/navigation_manager/active``)
을 구독한다. 환자 안내는 실제 이동 순간뿐 아니라 환자 확인부터 검사실 QR
대기까지 세션 전체를 잠그며, Waypoint 시험 주행도 active 동안 trigger를
거부한다. 반대로 ``/auto_localize/active``를 latched 상태로 공개해 두 주행
관리자가 재탐색 도중 새 목표를 받지 못하게 한다. 그 확인과 실제 이동 사이의
경쟁 상태에 외부 목표가 직접 들어오는 경우까지 막기 위해, 실제로 움직이기
직전에 Nav2의 현재 목표를 ``CancelGoal``로 한 번 더 강제로 취소한다.
"""

import json
import math
import threading
import time
import uuid

from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from lifecycle_msgs.srv import GetState
from mingky_interfaces.msg import Event, GuideState
from nav2_msgs.msg import ParticleCloud
from nav_msgs.msg import OccupancyGrid, Odometry
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger


from .convergence import evaluate_convergence
from .global_matcher import (
    GlobalScanMatcher,
    LaserObservation,
    MatcherConfig,
    OccupancyMap,
    PoseHypothesis,
)

MODE_TOPIC = "/mode"
PARTICLE_TOPIC = "/particle_cloud"
SCAN_TOPIC = "/scan"
MAP_TOPIC = "/map"
ODOM_TOPIC = "/odom"
INITIAL_POSE_TOPIC = "/initialpose"
EMERGENCY_STATE_TOPIC = "/emergency_stop/state"
NAV_MANAGER_ACTIVE_TOPIC = "/navigation_manager/active"
GUIDE_STATE_TOPIC = "/guide_manager/state"
ACTIVE_TOPIC = "/auto_localize/active"
PROBE_CMD_TOPIC = "cmd_vel_localize_probe"
CANCEL_NAV_SERVICE = "/navigate_to_pose/_action/cancel_goal"
TRIGGER_SERVICE = "auto_localize/trigger"
EVENT_TOPIC = "/events"

AUTO_MODE = "auto"

ACTIVE_GUIDE_SESSION_STATES = (
    GuideState.SESSION_CONFIRMED,
    GuideState.SESSION_GUIDING,
    GuideState.SESSION_ARRIVED,
    GuideState.SESSION_IN_ROOM,
)


def _guide_session_active(session_id: int, session_state: str) -> bool:
    return session_id > 0 and session_state in ACTIVE_GUIDE_SESSION_STATES


def _latched(depth: int = 1) -> QoSProfile:
    """늦게 붙는 구독자도 마지막 값을 받게 한다 (mode_manager 와 같은 이유)."""
    return QoSProfile(
        depth=depth,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        reliability=ReliabilityPolicy.RELIABLE,
    )


def _quat_to_yaw(z: float, w: float) -> float:
    return math.atan2(2.0 * w * z, 1.0 - 2.0 * z * z)


def _wrap_angle(angle: float) -> float:
    """각도를 [-pi, pi] 로 접는다. 359도와 1도가 358도 차이로 계산되는 것을 막는다."""
    return math.atan2(math.sin(angle), math.cos(angle))


def _circular_mean(angles) -> float:
    if not angles:
        return 0.0
    return math.atan2(
        sum(math.sin(angle) for angle in angles),
        sum(math.cos(angle) for angle in angles),
    )


class AutoLocalizeNode(Node):

    def __init__(self):
        super().__init__("auto_localize_node")

        self.declare_parameter("robot_id", "pinky-01")
        # 관제 팀 리뷰(2026-08-12): AMCL 전역 재탐색은 기존 위치 추정을 지우고
        # 로봇을 움직이기까지 한다. 부팅 때마다 무조건 도는 건 위험하니
        # 기본은 꺼두고, 관제 화면에서 운영자가 필요할 때 auto_localize/trigger
        # 서비스로 명시적으로 실행하게 한다. 부팅 시 자동 실행이 필요한
        # 로봇/환경에서만 launch 인자로 켠다.
        self.declare_parameter("auto_run_on_start", False)
        self.declare_parameter("probe_distance_m", 0.05)
        self.declare_parameter("probe_linear_speed", 0.18)
        self.declare_parameter("max_probe_attempts", 3)
        self.declare_parameter("overall_timeout_sec", 60.0)
        # 이동 중 정면(또는 후진이면 후면) 라이다 최소거리가 이보다 가까워지면
        # 목표 거리에 안 닿았어도 즉시 멈춘다. 출발 전 한 번 잰 여유만
        # 믿으면 안 된다는 걸 실제 로봇에서 배웠다 (아래 확인 참고).
        self.declare_parameter("obstacle_stop_distance_m", 0.20)
        # 방향 후보를 고를 때 재는 부채꼴 폭과 반드시 같아야 한다 --
        # 다르면 "이 방향 10도 폭은 안전하다"고 골라놓고 실제로는 더 넓은
        # 각도를 감시하다가 애초에 검증 안 한 옆쪽에 걸려 불필요하게 일찍
        # 멈추는 일이 생긴다 (2026-08-11 로봇에서 실측: 후보 선택은 10도인데
        # 감시가 20도라 매번 여유 있다고 고른 방향에서도 일찍 멈췄다).
        self.declare_parameter("obstacle_check_half_width_deg", 10.0)
        # 라이다(rplidar_link)가 로봇 몸체(base_footprint) 기준 180도 돌아서
        # 달려있다 (2026-08-11 로봇에서 tf2_echo base_footprint rplidar_link
        # 로 실측: RPY 180도, 평행이동은 1.7cm 라 무시할 만큼 작음). 이 보정
        # 없이 /scan 각도를 그대로 "로봇 기준 방향"으로 쓰면 정면이라고 판단한
        # 게 실제로는 후면이 되어, 실제 로봇이 벽 쪽으로 이동하는 사고가 났다.
        self.declare_parameter("scan_yaw_offset_deg", 180.0)
        # 라이다가 죽거나 연결이 끊겨도 self._latest_scan 은 마지막으로 받은
        # (이제는 낡은) 값을 계속 들고 있다. 그 값을 계속 "지금의 여유
        # 거리"로 믿으면 실제로는 안 보이는 상태로 계속 전진하게 된다
        # (관제 팀 리뷰: 이동 중 라이다가 끊기면 즉시 멈춰야 한다). 이
        # 시간보다 오래된 스캔은 "안 보인다 = 막혀있다"로 취급한다.
        self.declare_parameter("scan_max_age_sec", 1.0)
        # 전역 검색은 거친 격자에서 시작하고 상위 후보만 원본 맵 해상도로
        # 정밀화한다. 작은 맵에서도 모든 자세를 2.5cm로 훑으면 Pi 5 부하가
        # 커지므로 이 상한을 파라미터로 고정한다.
        self.declare_parameter("matcher_coarse_xy_m", 0.10)
        self.declare_parameter("matcher_coarse_yaw_deg", 15.0)
        self.declare_parameter("matcher_fine_xy_m", 0.025)
        self.declare_parameter("matcher_fine_yaw_deg", 3.0)
        self.declare_parameter("matcher_max_beams", 60)
        self.declare_parameter("matcher_top_k", 5)
        self.declare_parameter("matcher_min_score", 0.52)
        self.declare_parameter("matcher_min_margin", 0.08)
        self.declare_parameter("matcher_confirmations", 2)
        self.declare_parameter("matcher_seed_timeout_sec", 6.0)
        self.declare_parameter("matcher_seed_spread_m", 0.10)
        self.declare_parameter("matcher_seed_yaw_spread_deg", 10.0)
        self.declare_parameter("matcher_seed_pose_tolerance_m", 0.15)
        self.declare_parameter("matcher_seed_yaw_tolerance_deg", 15.0)

        get = self.get_parameter
        self.robot_id = str(get("robot_id").value)
        self.auto_run_on_start = bool(get("auto_run_on_start").value)
        self.probe_distance_m = float(get("probe_distance_m").value)
        self.probe_linear_speed = float(get("probe_linear_speed").value)
        self.max_probe_attempts = int(get("max_probe_attempts").value)
        self.overall_timeout_sec = float(get("overall_timeout_sec").value)
        self.obstacle_stop_distance_m = float(get("obstacle_stop_distance_m").value)
        self.obstacle_check_half_width_rad = math.radians(
            float(get("obstacle_check_half_width_deg").value))
        self.scan_yaw_offset_rad = math.radians(float(get("scan_yaw_offset_deg").value))
        self.scan_max_age_sec = float(get("scan_max_age_sec").value)
        self.matcher_config = MatcherConfig(
            coarse_xy_m=float(get("matcher_coarse_xy_m").value),
            coarse_yaw_rad=math.radians(
                float(get("matcher_coarse_yaw_deg").value)),
            fine_xy_m=float(get("matcher_fine_xy_m").value),
            fine_yaw_rad=math.radians(
                float(get("matcher_fine_yaw_deg").value)),
            max_beams=int(get("matcher_max_beams").value),
            top_k=int(get("matcher_top_k").value),
            min_score=float(get("matcher_min_score").value),
            min_margin=float(get("matcher_min_margin").value),
        )
        self.matcher_confirmations = max(
            2, int(get("matcher_confirmations").value))
        self.matcher_seed_timeout_sec = float(
            get("matcher_seed_timeout_sec").value)
        self.matcher_seed_spread_m = float(
            get("matcher_seed_spread_m").value)
        self.matcher_seed_yaw_spread_rad = math.radians(
            float(get("matcher_seed_yaw_spread_deg").value))
        self.matcher_seed_pose_tolerance_m = float(
            get("matcher_seed_pose_tolerance_m").value)
        self.matcher_seed_yaw_tolerance_rad = math.radians(
            float(get("matcher_seed_yaw_tolerance_deg").value))

        self.mode = None
        self._particles = None        # list[(x, y, yaw)] | None
        self._particles_updated_at = None  # time.monotonic() | None
        self._latest_scan = None
        self._latest_scan_at = None    # time.monotonic() | None
        self._latest_odom_xy = None    # (x, y) | None
        self._latest_odom_yaw = None   # radians | None
        self._matcher = None           # GlobalScanMatcher | None
        self._map_error = None         # str | None
        self._emergency_stopped = False
        self._nav_manager_active = False   # navigation_manager 의 Waypoint 시험 주행
        self._guide_session_id = 0
        self._guide_session_state = GuideState.SESSION_NONE
        self._busy = False
        self._busy_lock = threading.Lock()

        self.create_subscription(String, MODE_TOPIC, self._on_mode, _latched())
        self.create_subscription(
            ParticleCloud, PARTICLE_TOPIC, self._on_particles,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.create_subscription(
            LaserScan, SCAN_TOPIC, self._on_scan,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.create_subscription(
            OccupancyGrid, MAP_TOPIC, self._on_map, _latched())
        self.create_subscription(Odometry, ODOM_TOPIC, self._on_odom, 10)
        self.create_subscription(
            Bool, EMERGENCY_STATE_TOPIC, self._on_emergency_state, _latched())
        # 재탐색 이동은 Nav2 주행(환자 안내든 엔지니어 화면의 Waypoint 시험
        # 주행이든)과 동시에 벌어지면 안 된다 -- 두 쪽 다 로봇을 직접
        # 움직이려 든다. guide_manager/navigation_manager 가 이미 서로에게
        # 쓰는 것과 같은 상태 토픽을 그대로 구독해서 "지금 주행 중인가"를
        # 판단한다 (관제 팀 리뷰: 주행 목표 활성 중에는 수동 trigger 거부).
        self.create_subscription(
            Bool, NAV_MANAGER_ACTIVE_TOPIC, self._on_nav_manager_active, _latched())
        self.create_subscription(
            GuideState, GUIDE_STATE_TOPIC, self._on_guide_state, _latched())

        self.probe_pub = self.create_publisher(Twist, PROBE_CMD_TOPIC, 10)
        self.initial_pose_pub = self.create_publisher(
            PoseWithCovarianceStamped, INITIAL_POSE_TOPIC, 10)
        self.event_pub = self.create_publisher(Event, EVENT_TOPIC, 10)
        # 관제와 다른 주행 노드가 재탐색의 전체 실행 구간을 알 수 있게 한다.
        # 늦게 뜬 노드도 현재 상태를 즉시 받아야 하므로 latched QoS를 쓴다.
        self.active_pub = self.create_publisher(Bool, ACTIVE_TOPIC, _latched())
        # 우리 쪽 사전 확인과 실제 이동 사이에 새 주행 목표가 끼어들 수
        # 있다. 이동 직전에 한 번 더 이걸로 Nav2 의 현재 목표를 강제로
        # 취소해 확실히 막는다.
        self.cancel_nav_client = self.create_client(CancelGoal, CANCEL_NAV_SERVICE)
        self.create_service(Trigger, TRIGGER_SERVICE, self._on_trigger_request)
        self._publish_active()

        # amcl/bt_navigator 가 active 인지 직접 GetState 로 확인한다.
        # nav2_simple_commander.BasicNavigator 는 내부에서 spin 계열 함수를
        # 광범위하게 써서 "Executor is already spinning" 으로 반복해서
        # 죽었다 (별도 스레드로 옮겨도 재현됨 -- 문제가 콜백 중첩이 아니라
        # 라이브러리 자체의 spin 사용에 있었다). 그래서 여기서도 안 쓴다.
        self.lifecycle_clients = {
            name: self.create_client(GetState, f'/{name}/get_state')
            for name in ('amcl', 'bt_navigator')
        }

        if self.auto_run_on_start:
            # /mode 는 TRANSIENT_LOCAL 이라 늦게 구독해도 마지막 값을 받긴
            # 하지만, 실제 WiFi 환경에서는 그 도착까지 0.1초보다 오래 걸릴 수
            # 있다 (로컬 테스트는 지연이 0이라 이 문제가 안 드러났다 -- 실제
            # 로봇에서 돌려보고서야 잡힘). 그래서 한 번만 보고 판단하지 않고
            # 값이 실제로 올 때까지 최대 5초 기다린다.
            self._startup_wait_deadline = time.monotonic() + 5.0
            self._startup_timer = self.create_timer(0.2, self._wait_for_mode_then_start)

        self.get_logger().info("자동 로컬라이제이션 노드 시작")

    # ------------------------------------------------------------ 구독 콜백

    def _on_mode(self, msg: String):
        self.mode = msg.data

    def _on_particles(self, msg: ParticleCloud):
        self._particles = [
            (p.pose.position.x, p.pose.position.y,
             _quat_to_yaw(p.pose.orientation.z, p.pose.orientation.w))
            for p in msg.particles
        ]
        self._particles_updated_at = time.monotonic()

    def _on_scan(self, msg: LaserScan):
        self._latest_scan = msg
        self._latest_scan_at = time.monotonic()

    def _on_map(self, msg: OccupancyGrid):
        """현재 Nav2 맵으로 읽기 전용 distance field를 만든다."""
        try:
            origin = msg.info.origin
            grid = OccupancyMap(
                width=msg.info.width,
                height=msg.info.height,
                resolution=msg.info.resolution,
                origin_x=origin.position.x,
                origin_y=origin.position.y,
                origin_yaw=_quat_to_yaw(
                    origin.orientation.z, origin.orientation.w),
                data=msg.data,
            )
            self._matcher = GlobalScanMatcher(grid, self.matcher_config)
            self._map_error = None
            self.get_logger().info(
                f'전역 LiDAR 매처 준비: {msg.info.width}x{msg.info.height}, '
                f'{msg.info.resolution:.3f}m/cell')
        except (TypeError, ValueError) as exc:
            self._matcher = None
            self._map_error = str(exc)
            self.get_logger().error(f'전역 LiDAR 매처 맵 준비 실패: {exc}')

    def _on_odom(self, msg: Odometry):
        self._latest_odom_xy = (msg.pose.pose.position.x, msg.pose.pose.position.y)
        self._latest_odom_yaw = _quat_to_yaw(
            msg.pose.pose.orientation.z, msg.pose.pose.orientation.w)

    def _on_emergency_state(self, msg: Bool):
        self._emergency_stopped = bool(msg.data)

    def _on_nav_manager_active(self, msg: Bool):
        self._nav_manager_active = bool(msg.data)

    def _on_guide_state(self, msg: GuideState):
        self._guide_session_id = int(msg.session_id)
        self._guide_session_state = msg.session_state

    def _nav_goal_active(self) -> bool:
        """Nav2 가 지금 실제로 로봇을 주행시키고 있는가.

        환자 안내 주행(guide_manager)이든 엔지니어 화면의 Waypoint 시험
        주행(navigation_manager)이든, 그 도중에 재탐색 이동을 겹쳐 보내면
        두 명령이 같은 로봇 위에서 충돌한다.
        """
        guide_session_active = _guide_session_active(
            self._guide_session_id, self._guide_session_state)
        return self._nav_manager_active or guide_session_active

    def _publish_active(self) -> None:
        self.active_pub.publish(Bool(data=self._busy))

    def _reserve_run(self) -> bool:
        """한 실행만 예약하고 즉시 잠금 상태를 외부에 공개한다."""
        with self._busy_lock:
            if self._busy:
                return False
            self._busy = True
            self._publish_active()
            return True

    def _release_run(self) -> None:
        with self._busy_lock:
            self._busy = False
            self._publish_active()

    def _scan_is_fresh(self) -> bool:
        return (self._latest_scan is not None
                and self._latest_scan_at is not None
                and time.monotonic() - self._latest_scan_at <= self.scan_max_age_sec)

    # ------------------------------------------------------------ 트리거
    #
    # _run_sequence 는 최대 수십 초 걸리는 절차라, 구독/서비스 콜백을 처리하는
    # rclpy.spin(self) 스레드 안에서 직접 돌리면 그 스레드가 그동안 막혀서
    # 아무 메시지도 못 받는다. 그래서 별도 스레드에서 돌린다. 그 스레드는
    # rclpy.spin_once 로 직접 펌프하지 않고 그냥 time.sleep 으로 기다린다 --
    # 메인 스레드의 rclpy.spin(self) 가 이미 계속 돌면서 구독·서비스 응답을
    # 처리해주고 있어서, 결과(예: future.done())는 기다리기만 하면 된다.

    def _wait_for_mode_then_start(self):
        if self.mode is None and time.monotonic() < self._startup_wait_deadline:
            return  # 아직 5초 안 지났고 모드도 안 왔으면 계속 기다린다
        self._startup_timer.cancel()
        if self.mode is None:
            self.get_logger().warn(
                "부팅 후 5초가 지나도 /mode 를 못 받았습니다. "
                "mode_manager 가 떠 있는지 확인하세요.")
        threading.Thread(
            target=self._start_sequence, args=("startup",), daemon=True).start()

    def _on_trigger_request(self, request, response):
        if self._busy:
            response.success = False
            response.message = "이미 실행 중입니다."
            return response
        if self.mode != AUTO_MODE:
            response.success = False
            response.message = f"현재 모드가 '{self.mode}' 라 시작할 수 없습니다."
            return response
        if self._emergency_stopped:
            response.success = False
            response.message = "비상정지 상태라 재탐색을 시작할 수 없습니다."
            return response
        if self._nav_goal_active():
            response.success = False
            response.message = "주행 목표가 진행 중이라 지금은 재탐색을 시작할 수 없습니다."
            return response
        if not self._reserve_run():
            response.success = False
            response.message = "이미 실행 중입니다."
            return response
        # 서비스 콜백은 빨리 반환해야 한다 (여기서 결과까지 기다리면 이 콜백을
        # 처리하는 메인 스레드가 막혀 절차 자체가 못 돈다). 실제 결과는
        # /events 의 localize.converged / localize.failed 로 확인한다.
        threading.Thread(
            target=self._start_sequence, args=("manual", True), daemon=True).start()
        response.success = True
        response.message = "시작했습니다. 결과는 /events 에서 확인하세요."
        return response

    def _start_sequence(self, source: str, reserved: bool = False):
        if not reserved and not self._reserve_run():
            return False, "이미 실행 중입니다."
        try:
            if self.mode != AUTO_MODE:
                msg = f"현재 모드가 '{self.mode}' 라 자동 로컬라이제이션을 건너뜁니다."
                self.get_logger().warn(msg)
                return False, msg
            if self._nav_goal_active():
                # 서비스 승인 직후 세션이 생긴 경쟁 상황도 여기서 막는다.
                msg = "안내 세션 또는 주행 목표가 활성 상태라 자동 로컬라이제이션을 건너뜁니다."
                self.get_logger().warn(msg)
                return False, msg
            if self._emergency_stopped:
                msg = "비상정지 상태라 자동 로컬라이제이션을 건너뜁니다."
                self.get_logger().warn(msg)
                return False, msg
            return self._run_sequence(source)
        finally:
            self._release_run()

    # ------------------------------------------------------------ 본 절차

    def _run_sequence(self, source: str):
        self.get_logger().info("자동 로컬라이제이션 시작")
        self._emit("localize.started", Event.LEVEL_INFO, f'{{"source": "{source}"}}')

        if not self._wait_until_active(('amcl', 'bt_navigator'), timeout_sec=20.0):
            msg = "amcl/bt_navigator 가 20초 안에 active 상태가 되지 않았습니다."
            self.get_logger().error(msg)
            self._emit("localize.failed", Event.LEVEL_ERROR, '{"reason": "nav2_not_active"}')
            return False, msg

        if self._matcher is None:
            detail = f': {self._map_error}' if self._map_error else ''
            return self._localization_failure(
                'no_map', f'위치 재탐색용 맵을 받지 못했습니다{detail}')
        if self._latest_scan is None:
            return self._localization_failure(
                'no_scan', 'LiDAR 데이터를 받지 못했습니다.')
        if not self._scan_is_fresh():
            return self._localization_failure(
                'stale_scan', 'LiDAR 데이터가 지연되어 재탐색을 중단합니다.')
        if self._latest_odom_xy is None or self._latest_odom_yaw is None:
            return self._localization_failure(
                'no_odom', '오도메트리 데이터를 받지 못했습니다.')

        deadline = time.monotonic() + self.overall_timeout_sec
        matcher = self._matcher
        observation = self._latest_observation()
        if observation is None:
            return self._localization_failure(
                'no_scan', '위치 비교에 사용할 유효 LiDAR 점이 부족합니다.')

        result = matcher.global_match(observation)
        hypotheses = result.hypotheses
        previous_best = None
        confirmations = 0
        static_confirmation_used = False
        probe_direction = None
        probe_attempts = 0
        total_probe_distance = 0.0
        previous_odom = self._odom_pose()

        while rclpy.ok():
            safety_reason = self._localization_abort_reason()
            if safety_reason is not None:
                return self._localization_failure(*safety_reason)
            if time.monotonic() >= deadline:
                self._return_probe_offset(total_probe_distance)
                return self._localization_failure(
                    'timeout', '전역 위치 검색 제한 시간을 초과했습니다.')
            if not hypotheses:
                self._return_probe_offset(total_probe_distance)
                return self._localization_failure(
                    'no_candidates', '현재 LiDAR와 일치하는 위치 후보가 없습니다.')

            best = hypotheses[0]
            if self._same_hypothesis(previous_best, best):
                confirmations += 1
            else:
                confirmations = 1
            previous_best = best
            self.get_logger().info(
                f'전역 위치 후보: score={result.best_score:.3f}, '
                f'margin={result.margin:.3f}, confirmations={confirmations}, '
                f'pose=({best.x:.2f}, {best.y:.2f}, '
                f'{math.degrees(best.yaw):.1f}도)')

            if result.confident and confirmations >= self.matcher_confirmations:
                self._publish_initial_pose(best)
                accepted = self._wait_for_seed_acceptance(best)
                if not accepted:
                    abort_reason = self._localization_abort_reason()
                    if abort_reason is not None:
                        return self._localization_failure(*abort_reason)
                    return self._localization_failure(
                        'amcl_seed_rejected',
                        '찾은 위치를 AMCL이 제한 시간 안에 인수하지 못했습니다.',
                        score=result.best_score, margin=result.margin)
                payload = json.dumps({
                    'x': round(best.x, 4),
                    'y': round(best.y, 4),
                    'yaw': round(best.yaw, 5),
                    'score': round(result.best_score, 4),
                    'margin': round(result.margin, 4),
                    'scans': best.observations,
                    'probe_distance_m': round(abs(total_probe_distance), 3),
                })
                self._emit('localize.converged', Event.LEVEL_INFO, payload)
                return True, 'LiDAR 위치 검증 및 AMCL 적용 완료'

            # 첫 scan이 이미 충분히 좋아도 순간 노이즈 한 장만으로 확정하지
            # 않는다. 제자리에서 새 scan을 한 장 더 받아 같은 후보인지 본다.
            should_wait_static = result.confident and not static_confirmation_used
            if should_wait_static:
                static_confirmation_used = True
                scan_time = self._latest_scan_at or 0.0
                if not self._wait_for_new_scan(scan_time, timeout_sec=2.0):
                    abort_reason = self._localization_abort_reason()
                    if abort_reason is not None:
                        return self._localization_failure(*abort_reason)
                    return self._localization_failure(
                        'stale_scan', '확인용 LiDAR scan이 갱신되지 않았습니다.')
                current_odom = self._odom_pose()
                delta = self._relative_odom(previous_odom, current_odom)
                observation = self._latest_observation()
                if observation is None:
                    return self._localization_failure(
                        'no_scan', '확인용 LiDAR 점이 부족합니다.')
                result = matcher.update(
                    observation, hypotheses,
                    delta_x=delta[0], delta_y=delta[1], delta_yaw=delta[2])
                hypotheses = result.hypotheses
                previous_odom = current_odom
                continue

            if probe_attempts >= self.max_probe_attempts:
                self._return_probe_offset(total_probe_distance)
                reason = (
                    'ambiguous_candidates'
                    if result.best_score >= self.matcher_config.min_score
                    else 'no_candidates'
                )
                return self._localization_failure(
                    reason,
                    '짧은 이동 후에도 위치 후보를 안전하게 구분하지 못했습니다.',
                    score=result.best_score, margin=result.margin,
                    attempts=probe_attempts)

            if probe_direction is None:
                probe_direction = self._select_probe_direction()
            if probe_direction is None:
                self._return_probe_offset(total_probe_distance)
                return self._localization_failure(
                    'probe_blocked', '앞뒤 모두 탐색 이동 공간이 부족합니다.')

            scan_time = self._latest_scan_at or 0.0
            before_odom = self._odom_pose()
            requested = probe_direction * self.probe_distance_m
            moved = self._drive_straight(requested)
            if abs(moved) < self.probe_distance_m * 0.5:
                self._return_probe_offset(total_probe_distance)
                return self._localization_failure(
                    'probe_blocked', '5cm 탐색 이동을 안전하게 완료하지 못했습니다.')
            total_probe_distance += moved
            probe_attempts += 1
            if not self._wait_for_new_scan(scan_time, timeout_sec=2.0):
                abort_reason = self._localization_abort_reason()
                if abort_reason is not None:
                    return self._localization_failure(*abort_reason)
                self._return_probe_offset(total_probe_distance)
                return self._localization_failure(
                    'stale_scan', '탐색 이동 후 LiDAR scan이 갱신되지 않았습니다.')

            current_odom = self._odom_pose()
            delta = self._relative_odom(before_odom, current_odom)
            observation = self._latest_observation()
            if observation is None:
                self._return_probe_offset(total_probe_distance)
                return self._localization_failure(
                    'no_scan', '탐색 이동 후 유효 LiDAR 점이 부족합니다.')
            result = matcher.update(
                observation, hypotheses,
                delta_x=delta[0], delta_y=delta[1], delta_yaw=delta[2])
            hypotheses = result.hypotheses
            previous_odom = current_odom

    def _latest_observation(self):
        scan = self._latest_scan
        if scan is None or not self._scan_is_fresh():
            return None
        observation = LaserObservation.from_ranges(
            scan.ranges,
            angle_min=float(scan.angle_min),
            angle_increment=float(scan.angle_increment),
            range_min=float(scan.range_min),
            range_max=float(scan.range_max),
            yaw_offset_rad=self.scan_yaw_offset_rad,
            max_beams=self.matcher_config.max_beams,
        )
        return observation if observation.size >= 8 else None

    def _odom_pose(self):
        if self._latest_odom_xy is None or self._latest_odom_yaw is None:
            return None
        return (
            self._latest_odom_xy[0], self._latest_odom_xy[1],
            self._latest_odom_yaw,
        )

    @staticmethod
    def _relative_odom(previous, current):
        """이전 base 좌표계에서 본 odom 상대 이동량."""
        if previous is None or current is None:
            return 0.0, 0.0, 0.0
        world_dx = current[0] - previous[0]
        world_dy = current[1] - previous[1]
        cos_yaw = math.cos(previous[2])
        sin_yaw = math.sin(previous[2])
        return (
            cos_yaw * world_dx + sin_yaw * world_dy,
            -sin_yaw * world_dx + cos_yaw * world_dy,
            _wrap_angle(current[2] - previous[2]),
        )

    def _same_hypothesis(self, previous, current) -> bool:
        if previous is None or current is None:
            return False
        return (
            math.hypot(previous.x - current.x, previous.y - current.y)
            <= self.matcher_config.cluster_xy_m
            and abs(_wrap_angle(previous.yaw - current.yaw))
            <= self.matcher_config.cluster_yaw_rad
        )

    def _localization_abort_reason(self):
        if self.mode != AUTO_MODE:
            return 'mode_changed', '진행 중 모드가 바뀌어 재탐색을 중단합니다.'
        if self._emergency_stopped:
            return 'emergency_stop', '비상정지가 활성화되어 재탐색을 중단합니다.'
        if self._nav_goal_active():
            return 'navigation_started', '새 주행이 시작되어 재탐색을 중단합니다.'
        return None

    def _localization_failure(self, reason: str, message: str, **details):
        payload = {'reason': reason, **details}
        self.get_logger().error(message)
        self._emit(
            'localize.failed', Event.LEVEL_ERROR,
            json.dumps(payload, ensure_ascii=False))
        return False, message

    def _wait_for_new_scan(self, previous_time: float, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while rclpy.ok() and time.monotonic() < deadline:
            if self._localization_abort_reason() is not None:
                return False
            if (
                self._latest_scan_at is not None
                and self._latest_scan_at > previous_time
                and self._scan_is_fresh()
            ):
                return True
            time.sleep(0.05)
        return False

    def _select_probe_direction(self):
        """회전하지 않고 5cm 이동할 수 있는 앞/뒤 중 넓은 쪽을 고른다."""
        required = (
            self.obstacle_stop_distance_m + self.probe_distance_m
            + self.matcher_config.robot_clearance_m
        )
        front = self._sector_min_range(0.0)
        rear = self._sector_min_range(math.pi)
        choices = []
        if front >= required:
            choices.append((front, 1.0))
        if rear >= required:
            choices.append((rear, -1.0))
        if not choices:
            return None
        clearance, direction = max(choices, key=lambda item: item[0])
        self.get_logger().info(
            f'짧은 탐색 이동: {"전진" if direction > 0 else "후진"} '
            f'{self.probe_distance_m:.2f}m, 여유={clearance:.2f}m')
        return direction

    def _return_probe_offset(self, distance_m: float) -> None:
        """위치 확정 없이 끝났으면 안전할 때만 시작점으로 돌아간다."""
        if abs(distance_m) < 0.005:
            return
        if self._localization_abort_reason() is not None:
            self._publish_probe(Twist())
            return
        self.get_logger().info(
            f'위치 미확정 — 탐색 이동 {abs(distance_m):.2f}m 복귀')
        self._drive_straight(-distance_m)

    def _publish_initial_pose(self, hypothesis: PoseHypothesis) -> None:
        msg = PoseWithCovarianceStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'map'
        msg.pose.pose.position.x = hypothesis.x
        msg.pose.pose.position.y = hypothesis.y
        msg.pose.pose.orientation.z = math.sin(hypothesis.yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(hypothesis.yaw / 2.0)
        msg.pose.covariance[0] = 0.05 ** 2
        msg.pose.covariance[7] = 0.05 ** 2
        msg.pose.covariance[35] = math.radians(5.0) ** 2
        # Wi-Fi/DDS 재발견 직후 한 메시지를 놓쳐도 AMCL이 받을 수 있게 짧게
        # 두 번 보낸다. 좌표는 동일하므로 중복 초기화로 위치가 달라지지 않는다.
        self.initial_pose_pub.publish(msg)
        time.sleep(0.1)
        msg.header.stamp = self.get_clock().now().to_msg()
        self.initial_pose_pub.publish(msg)

    def _wait_for_seed_acceptance(self, hypothesis: PoseHypothesis) -> bool:
        """AMCL particle이 seed 주변에서 두 번 연속 안정적인지 확인한다."""
        start = time.monotonic()
        deadline = start + self.matcher_seed_timeout_sec
        confirmations = 0
        last_particle_time = None
        while rclpy.ok() and time.monotonic() < deadline:
            safety_reason = self._localization_abort_reason()
            if safety_reason is not None:
                return False
            updated_at = self._particles_updated_at
            if (
                updated_at is None or updated_at < start
                or updated_at == last_particle_time
            ):
                time.sleep(0.1)
                continue
            last_particle_time = updated_at
            particles = self._particles or []
            result = evaluate_convergence(
                particles,
                threshold_m=self.matcher_seed_spread_m,
                yaw_threshold_rad=self.matcher_seed_yaw_spread_rad,
            )
            mean_yaw = _circular_mean([item[2] for item in particles])
            pose_close = (
                math.hypot(
                    result.centroid[0] - hypothesis.x,
                    result.centroid[1] - hypothesis.y,
                ) <= self.matcher_seed_pose_tolerance_m
                and abs(_wrap_angle(mean_yaw - hypothesis.yaw))
                <= self.matcher_seed_yaw_tolerance_rad
            )
            confirmations = confirmations + 1 if result.converged and pose_close else 0
            if confirmations >= 2:
                return True
            time.sleep(0.1)
        return False

    def _wait_until_active(self, names, timeout_sec: float) -> bool:
        """lifecycle 노드들이 전부 active 가 될 때까지 기다린다.

        각 노드의 get_state 서비스를 call_async 로 부르고 time.sleep 으로
        기다린다 (spin 계열 함수를 안 쓰는 이유는 위 독스트링 참고).
        """
        deadline = time.monotonic() + timeout_sec
        pending = set(names)
        while pending and rclpy.ok() and time.monotonic() < deadline:
            for name in list(pending):
                client = self.lifecycle_clients[name]
                if not client.service_is_ready():
                    continue
                future = client.call_async(GetState.Request())
                call_deadline = time.monotonic() + 2.0
                while not future.done() and rclpy.ok() and time.monotonic() < call_deadline:
                    time.sleep(0.1)
                if future.done() and future.result() is not None:
                    if future.result().current_state.label == 'active':
                        pending.discard(name)
            if pending:
                time.sleep(0.3)
        if pending:
            self.get_logger().warn(f"active 대기 시간 초과: {sorted(pending)}")
        return not pending

    def _cancel_active_nav_goal(self):
        """Nav2(bt_navigator) 의 지금 목표를 강제로 전부 취소한다.

        action_msgs/CancelGoal 은 goal_id 와 시각이 둘 다 비어있으면(기본값)
        "모든 목표 취소" 로 정의돼 있다. 그래서 어떤 노드가 보낸 목표인지
        (guide_manager 든 navigation_manager 든) 몰라도 이거 하나로 정리된다.
        서비스가 아직 안 떠 있으면(Nav2 시작 전 등) 그냥 넘어간다 -- 애초에
        취소할 목표도 없다는 뜻이다.
        """
        if not self.cancel_nav_client.service_is_ready():
            return
        future = self.cancel_nav_client.call_async(CancelGoal.Request())
        deadline = time.monotonic() + 2.0
        while not future.done() and rclpy.ok() and time.monotonic() < deadline:
            time.sleep(0.05)

    def _drive_straight(self, distance_m: float) -> float:
        """현재 향하고 있는 방향 그대로 직진(양수)·후진(음수) 한다.

        출발 전 잰 여유만 믿지 않는다. 매 루프마다 진행 방향(후진이면
        후면)의 라이다 최소거리를 다시 재서, obstacle_stop_distance_m 보다
        가까워지면 목표 거리 전이라도 즉시 멈춘다. 실제 로봇이 "출발 전
        확인 → 이동 중엔 안 봄" 구조 때문에 벽에 부딪힌 뒤 추가했다.

        Returns:
            실제로 이동한 거리(부호 있음, distance_m 과 같은 부호).
        """
        start = self._latest_odom_xy
        if start is None:
            return 0.0
        self._cancel_active_nav_goal()
        target = abs(distance_m)
        forward = distance_m >= 0
        speed = self.probe_linear_speed if forward else -self.probe_linear_speed
        check_center = 0.0 if forward else math.pi
        deadline = time.monotonic() + (target / self.probe_linear_speed) * 2.0 + 2.0
        twist = Twist()
        twist.linear.x = speed
        traveled = 0.0
        while rclpy.ok() and time.monotonic() < deadline:
            abort_reason = self._localization_abort_reason()
            if abort_reason is not None:
                self.get_logger().warn(abort_reason[1])
                break
            clearance = self._sector_min_range(check_center)
            if clearance < self.obstacle_stop_distance_m:
                self.get_logger().warn(
                    f"이동 중 장애물 감지({clearance:.2f}m) — 목표 거리 전이지만 "
                    "즉시 정지합니다.")
                break
            self._publish_probe(twist)
            time.sleep(0.05)
            if self._latest_odom_xy is None:
                continue
            traveled = math.hypot(
                self._latest_odom_xy[0] - start[0], self._latest_odom_xy[1] - start[1])
            if traveled >= target:
                break
        self._publish_probe(Twist())
        return traveled if forward else -traveled

    def _sector_min_range(self, center_rad: float) -> float:
        """지금 이 순간, 로봇 기준 center_rad 방향 좁은 부채꼴의 라이다 최소거리(m).

        scan.angle_min 에 scan_yaw_offset_rad 를 더해 로봇 몸체 기준으로
        바꾼 뒤 잰다 (전역 매처의 LiDAR 보정과 같은 기준).

        스캔이 없거나 낡았으면(scan_max_age_sec 초과) 0.0 을 돌려준다 --
        "안 보인다" 를 "안전하다"(inf) 가 아니라 "바로 앞이 막혀있다"
        로 취급해야 이동 루프가 즉시 멈춘다 (관제 팀 리뷰: 이동 중 라이다
        데이터가 끊기면 즉시 정지).
        """
        if not self._scan_is_fresh():
            return 0.0
        scan = self._latest_scan
        angle_min = float(scan.angle_min) + self.scan_yaw_offset_rad
        increment = float(scan.angle_increment)
        half_width = self.obstacle_check_half_width_rad
        best = float("inf")
        for i, r in enumerate(scan.ranges):
            if r is None or r != r or not (scan.range_min <= r <= scan.range_max):
                continue
            angle = _wrap_angle(angle_min + i * increment)
            if abs(_wrap_angle(angle - center_rad)) <= half_width:
                best = min(best, float(r))
        return best

    def _publish_probe(self, twist: Twist):
        """워커 스레드에서 부른다. 메인 스레드가 종료 처리 중(퍼블리셔가 이미
        파괴됨)이면 rclpy 가 예외를 던지는데, 이건 "지금 멈추라"는 신호일
        뿐이라 워커 스레드까지 지저분한 traceback 으로 죽을 필요는 없다.
        """
        try:
            self.probe_pub.publish(twist)
        except rclpy._rclpy_pybind11.InvalidHandle:
            self.get_logger().debug("종료 중이라 이동 명령을 건너뜁니다.")

    def _emit(self, code: str, level: int, payload: str):
        event = Event()
        event.event_id = str(uuid.uuid4())
        event.robot_id = self.robot_id
        event.session_id = 0
        event.occurred_at = self.get_clock().now().to_msg()
        event.level = level
        event.event_code = code
        event.source_node = "auto_localize_node"
        event.payload = payload
        try:
            self.event_pub.publish(event)
        except rclpy._rclpy_pybind11.InvalidHandle:
            self.get_logger().debug("종료 중이라 이벤트 발행을 건너뜁니다.")


def main():
    rclpy.init()
    node = AutoLocalizeNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        # SIGTERM(예: `timeout` 명령, systemd stop)을 받으면 rclpy 는 이쪽으로
        # 온다. KeyboardInterrupt 만 잡으면 정상 종료인데도 traceback 이 찍힌다.
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
