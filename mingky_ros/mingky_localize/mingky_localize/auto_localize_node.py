"""AMCL 초기 위치를 자동으로 잡는다. RViz 의 2D Pose Estimate 손조작을 없앤다.

    /mode 확인 (auto 아니면 중단)
      → amcl/bt_navigator active 대기
      → /reinitialize_global_localization 호출
      → /particle_cloud 로 수렴 관찰 (mingky_localize.convergence)
      → 수렴 안 되면: /scan 으로 안전 방향 탐색 (mingky_smart_recovery 재사용)
        → 제자리 회전 → 직진(이동 중에도 라이다 계속 감시) → 후진 → 회전 복귀
      → 정해진 횟수/시간 넘으면 포기하고 이벤트만 남긴다 (억지로 계속 안 함)

부팅(Nav2 기동) 시 자동 1회 실행되고, ``auto_localize/trigger`` 서비스로
언제든 수동 재실행도 된다.

## 왜 이동 거리를 고정하지 않는가

map_ambiguity.py 가 찾은 "전진 0.4m + 후진 0.4m" 는 계측된 최소값이 아니라
근사치다. 로봇이 설 수 있는 공간이 2.12㎡ 뿐이라 모든 위치에서 40cm 왕복이
보장되지도 않는다. 그래서 이동 전 라이다로 실측 여유를 재고(mingky_smart_recovery
의 clearance 로직 그대로 재사용), 여유가 없으면 그 방향을 포기하고 다른
방향을 시도한다.

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
"""

import math
import threading
import time
import uuid

import rclpy
from geometry_msgs.msg import Twist
from lifecycle_msgs.srv import GetState
from nav2_msgs.msg import ParticleCloud
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from std_srvs.srv import Empty, Trigger

from mingky_interfaces.msg import Event
from mingky_smart_recovery.selector import (
    SelectorConfig,
    select_diverse_candidates,
    select_escape_candidates,
)

from .convergence import evaluate_convergence

MODE_TOPIC = "/mode"
PARTICLE_TOPIC = "/particle_cloud"
SCAN_TOPIC = "/scan"
ODOM_TOPIC = "/odom"
PROBE_CMD_TOPIC = "cmd_vel_localize_probe"
RESET_SERVICE = "/reinitialize_global_localization"
TRIGGER_SERVICE = "auto_localize/trigger"
EVENT_TOPIC = "/events"

AUTO_MODE = "auto"


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


class AutoLocalizeNode(Node):

    def __init__(self):
        super().__init__("auto_localize_node")

        self.declare_parameter("robot_id", "pinky-01")
        self.declare_parameter("auto_run_on_start", True)
        self.declare_parameter("convergence_threshold_m", 0.3)
        self.declare_parameter("yaw_threshold_deg", 15.0)
        self.declare_parameter("observe_seconds", 5.0)
        self.declare_parameter("probe_distance_m", 0.4)
        self.declare_parameter("probe_min_distance_m", 0.15)
        self.declare_parameter("probe_linear_speed", 0.18)
        self.declare_parameter("probe_angular_speed", 0.6)
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

        get = self.get_parameter
        self.robot_id = str(get("robot_id").value)
        self.auto_run_on_start = bool(get("auto_run_on_start").value)
        self.convergence_threshold_m = float(get("convergence_threshold_m").value)
        self.yaw_threshold_rad = math.radians(float(get("yaw_threshold_deg").value))
        self.observe_seconds = float(get("observe_seconds").value)
        self.probe_distance_m = float(get("probe_distance_m").value)
        self.probe_min_distance_m = float(get("probe_min_distance_m").value)
        self.probe_linear_speed = float(get("probe_linear_speed").value)
        self.probe_angular_speed = float(get("probe_angular_speed").value)
        self.max_probe_attempts = int(get("max_probe_attempts").value)
        self.overall_timeout_sec = float(get("overall_timeout_sec").value)
        self.obstacle_stop_distance_m = float(get("obstacle_stop_distance_m").value)
        self.obstacle_check_half_width_rad = math.radians(
            float(get("obstacle_check_half_width_deg").value))
        self.scan_yaw_offset_rad = math.radians(float(get("scan_yaw_offset_deg").value))

        self.mode = None
        self._particles = None        # list[(x, y, yaw)] | None
        self._particles_updated_at = None  # time.monotonic() | None
        self._latest_scan = None
        self._latest_odom_xy = None    # (x, y) | None
        self._latest_odom_yaw = None   # radians | None
        self._busy = False

        self.create_subscription(String, MODE_TOPIC, self._on_mode, _latched())
        self.create_subscription(
            ParticleCloud, PARTICLE_TOPIC, self._on_particles,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.create_subscription(
            LaserScan, SCAN_TOPIC, self._on_scan,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.create_subscription(Odometry, ODOM_TOPIC, self._on_odom, 10)

        self.probe_pub = self.create_publisher(Twist, PROBE_CMD_TOPIC, 10)
        self.event_pub = self.create_publisher(Event, EVENT_TOPIC, 10)
        self.reset_client = self.create_client(Empty, RESET_SERVICE)
        self.create_service(Trigger, TRIGGER_SERVICE, self._on_trigger_request)

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

    def _on_odom(self, msg: Odometry):
        self._latest_odom_xy = (msg.pose.pose.position.x, msg.pose.pose.position.y)
        self._latest_odom_yaw = _quat_to_yaw(
            msg.pose.pose.orientation.z, msg.pose.pose.orientation.w)

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
        # 서비스 콜백은 빨리 반환해야 한다 (여기서 결과까지 기다리면 이 콜백을
        # 처리하는 메인 스레드가 막혀 절차 자체가 못 돈다). 실제 결과는
        # /events 의 localize.converged / localize.failed 로 확인한다.
        threading.Thread(
            target=self._start_sequence, args=("manual",), daemon=True).start()
        response.success = True
        response.message = "시작했습니다. 결과는 /events 에서 확인하세요."
        return response

    def _start_sequence(self, source: str):
        if self.mode != AUTO_MODE:
            msg = f"현재 모드가 '{self.mode}' 라 자동 로컬라이제이션을 건너뜁니다."
            self.get_logger().warn(msg)
            return False, msg
        self._busy = True
        try:
            return self._run_sequence(source)
        finally:
            self._busy = False

    # ------------------------------------------------------------ 본 절차

    def _run_sequence(self, source: str):
        self.get_logger().info("자동 로컬라이제이션 시작")
        self._emit("localize.started", Event.LEVEL_INFO, f'{{"source": "{source}"}}')

        if not self._wait_until_active(('amcl', 'bt_navigator'), timeout_sec=20.0):
            msg = "amcl/bt_navigator 가 20초 안에 active 상태가 되지 않았습니다."
            self.get_logger().error(msg)
            self._emit("localize.failed", Event.LEVEL_ERROR, '{"reason": "nav2_not_active"}')
            return False, msg

        if not self.reset_client.wait_for_service(timeout_sec=5.0):
            msg = f"{RESET_SERVICE} 서비스가 없습니다."
            self.get_logger().error(msg)
            self._emit("localize.failed", Event.LEVEL_ERROR, '{"reason": "no_service"}')
            return False, msg

        deadline = time.monotonic() + self.overall_timeout_sec
        self._call_reset_async()

        failures: dict = {}  # 방향 이름 -> 시도했는데도 안 된 횟수
        for attempt in range(self.max_probe_attempts + 1):
            if self.mode != AUTO_MODE:
                msg = "진행 중 모드가 바뀌어 중단합니다."
                self.get_logger().warn(msg)
                self._emit("localize.failed", Event.LEVEL_ERROR,
                           '{"reason": "mode_changed"}')
                return False, msg

            result = self._observe_convergence(
                self.observe_seconds if attempt > 0 else 2.0)

            if result.converged:
                self.get_logger().info(
                    f"수렴 확인 (위치 퍼짐 {result.spread_m:.2f}m, "
                    f"방향 퍼짐 {math.degrees(result.yaw_spread_rad):.1f}도)")
                self._emit(
                    "localize.converged", Event.LEVEL_INFO,
                    f'{{"spread_m": {result.spread_m:.3f}, '
                    f'"yaw_spread_deg": {math.degrees(result.yaw_spread_rad):.1f}, '
                    f'"attempt": {attempt}}}')
                return True, "수렴 완료"

            if time.monotonic() > deadline or attempt == self.max_probe_attempts:
                break

            self.get_logger().warn(
                f"미수렴 (위치 퍼짐 {result.spread_m:.2f}m) — 탐색 이동 {attempt + 1}회차")
            tried_name = self._probe_once(failures)
            if tried_name is None:
                self.get_logger().warn("안전한 탐색 방향이 없어 이동을 건너뜁니다.")
            else:
                # 이번에도 안 됐다는 게 다음 루프 시작에서 드러난다 (여기서는
                # 아직 관찰 전이라 모른다). 일단 시도했다는 사실 자체를
                # 기록해두고, 다음 관찰에서도 여전히 미수렴이면 그 시도가
                # 헛수고였다는 뜻이니 감점을 유지한다.
                failures[tried_name] = failures.get(tried_name, 0) + 1

        msg = (f"{self.max_probe_attempts}회 시도 후에도 수렴하지 않았습니다. "
               "사람 확인이 필요합니다.")
        self.get_logger().error(msg)
        self._emit("localize.failed", Event.LEVEL_ERROR,
                   f'{{"reason": "not_converged", "attempts": {self.max_probe_attempts}}}')
        return False, msg

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

    def _call_reset_async(self):
        """reinitialize_global_localization 을 비동기로 부르고 완료까지 기다린다.

        Client.call() 은 내부에서 같은 노드를 재귀적으로 spin 하려 해서
        안전하지 않다. call_async + time.sleep 대기로 우회한다 (메인
        스레드가 응답을 처리해준다).
        """
        future = self.reset_client.call_async(Empty.Request())
        deadline = time.monotonic() + 5.0
        while not future.done() and rclpy.ok() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not future.done():
            self.get_logger().warn(f"{RESET_SERVICE} 응답을 못 받았습니다 (타임아웃).")

    def _probe_once(self, failures: dict) -> "str | None":
        """방향을 골라 이동한다. 고른 방향 이름을 돌려준다 (없으면 None).

        failures 는 "이 방향 이름을 최근에 몇 번 시도했는데도 안 됐는가"
        를 담는다. select_escape_candidates 에 그대로 넘기면 반복 실패한
        방향에 감점을 줘서 다음엔 다른 방향을 우선한다 -- 실제 로봇에서
        같은 방향(left_015)을 3번 반복하고도 퍼짐이 거의 안 줄어든 걸
        보고 추가했다 (mingky_smart_recovery 가 원래 갖고 있던 기능인데
        안 쓰고 있었다).
        """
        scan = self._latest_scan
        if scan is None:
            return None
        config = SelectorConfig(
            nominal_distance_m=self.probe_distance_m,
            minimum_distance_m=self.probe_min_distance_m,
            # 이동 중 감시(_sector_min_range)와 같은 폭을 써야 한다 -- 위
            # obstacle_check_half_width_deg 파라미터 설명 참고.
            sector_half_width_rad=self.obstacle_check_half_width_rad,
        )
        # scan.angle_min 은 라이다 센서 기준이다. scan_yaw_offset_rad(180도)
        # 를 더해 로봇 몸체(base_footprint) 기준 각도로 바꾼 뒤 후보를
        # 고른다 -- 안 하면 정면/후면이 뒤바뀐다.
        candidates = select_diverse_candidates(
            select_escape_candidates(
                scan.ranges,
                angle_min=float(scan.angle_min) + self.scan_yaw_offset_rad,
                angle_increment=float(scan.angle_increment),
                range_min=float(scan.range_min),
                range_max=float(scan.range_max),
                goal_bearing_rad=0.0,
                failures=failures,
                config=config,
            ),
            limit=1,
        )
        if not candidates:
            return None
        candidate = candidates[0]
        self.get_logger().info(
            f"탐색 방향: {candidate.name}, 거리={candidate.distance_m:.2f}m, "
            f"여유={candidate.clearance_m:.2f}m"
            + (f" (이전 실패 {failures[candidate.name]}회)"
               if failures.get(candidate.name) else ""))
        self._move_relative(candidate.bearing_rad, candidate.distance_m)
        return candidate.name

    def _move_relative(self, bearing_rad: float, distance_m: float):
        """지정 방향으로 이동한 뒤 정확히 반대로 돌아온다.

        핑키는 바퀴 2개짜리 차동구동이라 옆으로 못 간다 (linear.y 는
        무시당한다). 그래서 "왼쪽 30도로 0.4m" 같은 대각선 이동을 한 번에
        하지 않고, 제자리 회전 → 직진 → 후진 → 반대로 회전해서 원래 방향
        복귀로 나눈다.

        map 좌표가 아니라 odom(누적 주행거리·각도)으로 잰다. 지금 위치
        자체가 불확실해서 하는 이동이니, 상대 이동량만 맞추면 원위치로
        돌아온다 -- Nav2 의 지도좌표 이동과 달리 AMCL 확신도에 의존하지
        않는다.
        """
        if self._latest_odom_xy is None or self._latest_odom_yaw is None:
            return
        need_turn = abs(_wrap_angle(bearing_rad)) > math.radians(3.0)

        if need_turn and not self._rotate_relative(bearing_rad):
            return
        # 장애물 때문에 목표 거리에 못 미쳤을 수 있다. 실제로 간 만큼만
        # 되돌아와야 원위치로 정확히 복귀한다 (계획한 거리로 되돌리면
        # 못 간 만큼 반대쪽으로 더 밀려난다).
        traveled = self._drive_straight(distance_m)
        self._drive_straight(-traveled)
        if need_turn:
            self._rotate_relative(-bearing_rad)

    def _rotate_relative(self, delta_rad: float, angular_speed: float = None) -> bool:
        """odom 각도 기준으로 제자리에서 delta_rad 만큼 돈다."""
        if angular_speed is None:
            angular_speed = self.probe_angular_speed
        if self._latest_odom_yaw is None:
            return False
        target_yaw = _wrap_angle(self._latest_odom_yaw + delta_rad)
        direction = 1.0 if delta_rad >= 0 else -1.0
        deadline = (time.monotonic()
                    + (abs(delta_rad) / angular_speed) * 2.0 + 2.0)
        twist = Twist()
        twist.angular.z = direction * angular_speed
        while rclpy.ok() and time.monotonic() < deadline:
            if self.mode != AUTO_MODE:
                self.get_logger().warn("모드가 바뀌어 회전을 중단합니다.")
                self._publish_probe(Twist())
                return False
            self._publish_probe(twist)
            time.sleep(0.05)
            if self._latest_odom_yaw is None:
                continue
            remaining = _wrap_angle(target_yaw - self._latest_odom_yaw)
            if abs(remaining) < math.radians(3.0):
                break
        self._publish_probe(Twist())
        return True

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
        target = abs(distance_m)
        forward = distance_m >= 0
        speed = self.probe_linear_speed if forward else -self.probe_linear_speed
        check_center = 0.0 if forward else math.pi
        deadline = time.monotonic() + (target / self.probe_linear_speed) * 2.0 + 2.0
        twist = Twist()
        twist.linear.x = speed
        traveled = 0.0
        while rclpy.ok() and time.monotonic() < deadline:
            if self.mode != AUTO_MODE:
                self.get_logger().warn("모드가 바뀌어 탐색 이동을 중단합니다.")
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
        바꾼 뒤 잰다 (_probe_once 의 후보 선택과 같은 보정).
        """
        scan = self._latest_scan
        if scan is None:
            return float("inf")
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

    def _spin_for(self, seconds: float):
        """별도 스레드에서 결과가 쌓이길 기다린다 (메인 스레드가 계속 처리 중)."""
        deadline = time.monotonic() + seconds
        while rclpy.ok() and time.monotonic() < deadline:
            time.sleep(0.1)

    def _observe_convergence(self, max_seconds: float):
        """최대 max_seconds 동안 새로 들어오는 파티클로 수렴 여부를 계속 잰다.

        예전엔 max_seconds 를 무조건 다 채운 뒤 딱 한 번 확인했다 -- 2초
        만에 이미 수렴했어도 5초를 다 기다려야 했다. 이제는 0.3초마다
        다시 재고, 수렴하면 그 즉시 반환한다.

        _particles_updated_at 로 "신선한" 데이터인지 확인한다 -- 방금 한
        이동/리셋 이전의 오래된 파티클로 잘못 "수렴했다" 고 판단하는 것을
        막는다.
        """
        start = time.monotonic()
        deadline = start + max_seconds
        result = None
        while rclpy.ok() and time.monotonic() < deadline:
            if self._particles_updated_at is not None and self._particles_updated_at >= start:
                result = evaluate_convergence(
                    self._particles or [],
                    threshold_m=self.convergence_threshold_m,
                    yaw_threshold_rad=self.yaw_threshold_rad,
                )
                if result.converged:
                    return result
            time.sleep(0.3)
        if result is None:
            result = evaluate_convergence(
                self._particles or [],
                threshold_m=self.convergence_threshold_m,
                yaw_threshold_rad=self.yaw_threshold_rad,
            )
        return result

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
