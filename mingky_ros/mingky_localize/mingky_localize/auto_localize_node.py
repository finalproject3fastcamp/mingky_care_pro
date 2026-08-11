"""AMCL 초기 위치를 자동으로 잡는다. RViz 의 2D Pose Estimate 손조작을 없앤다.

    /mode 확인 (auto 아니면 중단)
      → amcl/bt_navigator active 대기
      → /reinitialize_global_localization 호출
      → /particle_cloud 로 수렴 관찰 (mingky_localize.convergence)
      → 수렴 안 되면: /scan 으로 안전 방향 탐색 (mingky_smart_recovery 재사용)
        → 전진 → 후진(원위치) → 재관찰
      → 정해진 횟수/시간 넘으면 포기하고 이벤트만 남긴다 (억지로 계속 안 함)

부팅(Nav2 기동) 시 자동 1회 실행되고, ``auto_localize/trigger`` 서비스로
언제든 수동 재실행도 된다.

## 왜 이동 거리를 고정하지 않는가

map_ambiguity.py 가 찾은 "전진 0.4m + 후진 0.4m" 는 계측된 최소값이 아니라
근사치다. 로봇이 설 수 있는 공간이 2.12㎡ 뿐이라 모든 위치에서 40cm 왕복이
보장되지도 않는다. 그래서 이동 전 라이다로 실측 여유를 재고(mingky_smart_recovery
의 clearance 로직 그대로 재사용), 여유가 없으면 그 방향을 포기하고 다른
방향을 시도한다.

## 왜 이 결과를 "증명"이 아니라 "완화"라고 부르는가

파티클이 위치·방향 모두 좁게 모여도, 그게 실제로 맞다는 보장은 없다.
map_ambiguity.py 가 찾은 것처럼 서로 다른 두 실제 위치가 라이다로 거의
똑같이 보이면, 파티클 전체가 틀린 쪽으로 확신을 갖고 모일 수 있다. 탐색
이동은 이 위험을 줄이는 시도이지 완전한 증명이 아니다. 최종 확인은 사람이
대시보드/Foxglove 로 라이다 윤곽이 맵 벽과 겹치는지 보는 것이다.

## 왜 별도 twist_mux 채널인가

이 노드가 만드는 이동은 사람도 아니고 Nav2 경로계획도 아니다. cmd_vel_teleop
로 쏘면 사람 조작과 충돌하고, cmd_vel_smoothed 로 쏘면 Nav2인 척하게 된다.
twist_mux 에 별도 입력(cmd_vel_localize_probe)이 필요하다 — manual lock 이
이것도 같이 막아야 사람이 몰고 있을 때 로봇이 혼자 움직이지 않는다.
twist_mux.yaml 에 이 채널을 추가해뒀다.
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
        self.declare_parameter("probe_linear_speed", 0.1)
        self.declare_parameter("max_probe_attempts", 3)
        self.declare_parameter("overall_timeout_sec", 60.0)

        get = self.get_parameter
        self.robot_id = str(get("robot_id").value)
        self.auto_run_on_start = bool(get("auto_run_on_start").value)
        self.convergence_threshold_m = float(get("convergence_threshold_m").value)
        self.yaw_threshold_rad = math.radians(float(get("yaw_threshold_deg").value))
        self.observe_seconds = float(get("observe_seconds").value)
        self.probe_distance_m = float(get("probe_distance_m").value)
        self.probe_min_distance_m = float(get("probe_min_distance_m").value)
        self.probe_linear_speed = float(get("probe_linear_speed").value)
        self.max_probe_attempts = int(get("max_probe_attempts").value)
        self.overall_timeout_sec = float(get("overall_timeout_sec").value)

        self.mode = None
        self._particles = None        # list[(x, y, yaw)] | None
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

        # nav2_simple_commander.BasicNavigator.waitUntilNav2Active() 대신 직접
        # GetState 를 부른다. BasicNavigator 는 내부에서 자기 노드를 또
        # spin() 하려고 해서 문제가 됐다 (아래 스레드 설명 참고).
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
    # (콜백 안에서 rclpy.spin_once(self, ...) 를 또 부르면 "Executor is
    # already spinning" 으로 죽는다 -- 로봇에서 실제로 두 번 재현된 문제다.)

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

        for attempt in range(self.max_probe_attempts + 1):
            if self.mode != AUTO_MODE:
                msg = "진행 중 모드가 바뀌어 중단합니다."
                self.get_logger().warn(msg)
                self._emit("localize.failed", Event.LEVEL_ERROR,
                           '{"reason": "mode_changed"}')
                return False, msg

            self._spin_for(self.observe_seconds if attempt > 0 else 2.0)

            result = evaluate_convergence(
                self._particles or [],
                threshold_m=self.convergence_threshold_m,
                yaw_threshold_rad=self.yaw_threshold_rad,
            )

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
            if not self._probe_once():
                self.get_logger().warn("안전한 탐색 방향이 없어 이동을 건너뜁니다.")

        msg = (f"{self.max_probe_attempts}회 시도 후에도 수렴하지 않았습니다. "
               "사람 확인이 필요합니다.")
        self.get_logger().error(msg)
        self._emit("localize.failed", Event.LEVEL_ERROR,
                   f'{{"reason": "not_converged", "attempts": {self.max_probe_attempts}}}')
        return False, msg

    def _wait_until_active(self, names, timeout_sec: float) -> bool:
        """lifecycle 노드들이 전부 active 가 될 때까지 기다린다.

        각 노드의 get_state 서비스를 call_async 로 부르고 time.sleep 으로
        기다린다. 이 메서드는 별도 스레드에서 실행되므로 (위 트리거 설명
        참고), 메인 스레드의 rclpy.spin(self) 가 응답을 처리해줄 때까지 그냥
        기다리기만 하면 된다.
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

    def _probe_once(self) -> bool:
        scan = self._latest_scan
        if scan is None:
            return False
        config = SelectorConfig(
            nominal_distance_m=self.probe_distance_m,
            minimum_distance_m=self.probe_min_distance_m,
        )
        candidates = select_diverse_candidates(
            select_escape_candidates(
                scan.ranges,
                angle_min=float(scan.angle_min),
                angle_increment=float(scan.angle_increment),
                range_min=float(scan.range_min),
                range_max=float(scan.range_max),
                goal_bearing_rad=0.0,
                config=config,
            ),
            limit=1,
        )
        if not candidates:
            return False
        candidate = candidates[0]
        self.get_logger().info(
            f"탐색 방향: {candidate.name}, 거리={candidate.distance_m:.2f}m, "
            f"여유={candidate.clearance_m:.2f}m")
        self._move_relative(candidate.bearing_rad, candidate.distance_m)
        return True

    def _move_relative(self, bearing_rad: float, distance_m: float):
        """지정 방향으로 이동한 뒤 정확히 반대로 돌아온다.

        핑키는 바퀴 2개짜리 차동구동이라 옆으로 못 간다 (linear.y 는
        무시당한다). 그래서 "왼쪽 30도로 0.4m" 같은 대각선 이동을 한 번에
        하지 않고, **제자리 회전 → 직진 → 후진 → 반대로 회전해서 원래
        방향으로 복귀** 로 나눈다. 실제 로봇에서 대각선 이동을 시도했다가
        로봇이 회전을 무시하고 정면으로 그대로 가버려 벽에 부딪힌 것을
        보고서야 발견한 문제라, 여기서 고친다.

        map 좌표가 아니라 odom(누적 주행거리·각도)으로 잰다. 지금 위치
        자체가 불확실해서 하는 이동이니, 상대 이동량만 맞추면 원위치로
        돌아온다.
        """
        if self._latest_odom_xy is None or self._latest_odom_yaw is None:
            return
        need_turn = abs(_wrap_angle(bearing_rad)) > math.radians(3.0)

        if need_turn and not self._rotate_relative(bearing_rad):
            return
        self._drive_straight(distance_m)
        self._drive_straight(-distance_m)
        if need_turn:
            self._rotate_relative(-bearing_rad)

    def _rotate_relative(self, delta_rad: float, angular_speed: float = 0.4) -> bool:
        """odom 각도 기준으로 제자리에서 delta_rad 만큼 돈다."""
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

    def _drive_straight(self, distance_m: float):
        """현재 향하고 있는 방향 그대로 직진(양수)·후진(음수) 한다."""
        start = self._latest_odom_xy
        if start is None:
            return
        target = abs(distance_m)
        speed = self.probe_linear_speed if distance_m >= 0 else -self.probe_linear_speed
        deadline = time.monotonic() + (target / self.probe_linear_speed) * 2.0 + 2.0
        twist = Twist()
        twist.linear.x = speed
        while rclpy.ok() and time.monotonic() < deadline:
            if self.mode != AUTO_MODE:
                self.get_logger().warn("모드가 바뀌어 탐색 이동을 중단합니다.")
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
        self.event_pub.publish(event)


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
