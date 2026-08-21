"""대시보드의 실시간 조작을 로봇 안으로 들여오고, 위치를 내보낸다.

    관제 wss ──→ teleop_bridge ──→ /cmd_vel_teleop_raw → teleop_limiter → twist_mux
    관제 wss ──→ teleop_bridge ──→ /initialpose  (지도에서 위치를 찍어줄 때)
    관제 wss ←── teleop_bridge ←── /amcl_pose, /scan, /particle_cloud,
                                   /navigation_manager/{route,recovery}_plan,
                                   /low_obstacle/observation

## 왜 로봇이 서버로 거는가

로봇은 NAT 뒤에 있어 서버가 들어올 수 없다. `orders` 와 같은 이유로 나가는
연결만 만든다. 끊기면 다시 건다.

## 끊기면 어떻게 되나

**아무것도 하지 않는다.** 명령이 안 들어오면 `/cmd_vel_teleop_raw` 발행이
멈추고, teleop_limiter → twist_mux 도 조용해져 1초 뒤 워치독이 모터를 세운다.
여기서 굳이 0 속도를 쏘지 않는 이유는, 그 코드가 필요한 상황이면 이미 소켓이
끊겨 있어 실행될 보장이 없기 때문이다. **정지를 연결에 의존시키지 않는다.**

## 왜 pose 를 여기서 보내나

지도 위에 로봇을 그리려면 위치가 필요한데, 이벤트로 보내면 안 된다. 5Hz 로
쌓이는 좌표가 타임라인을 덮어버린다 (배터리를 이벤트에서 뺀 것과 같은 이유).
조작 소켓이 이미 열려 있으므로 그 위에 얹는다.
"""

import json
import math
import threading
import time

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from nav2_msgs.msg import ParticleCloud
from nav_msgs.msg import Path
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener

from .scan_geometry import transform_polar_point

try:
    # websocket-client(동기). ROS 노드가 이미 스레드로 돌고 있어 asyncio 를
    # 끌어들이지 않는 편이 단순하다. apt 의 python3-websocket 이다.
    import websocket
except ImportError:  # pragma: no cover
    websocket = None

OUT_TOPIC = "cmd_vel_teleop_raw"
POSE_TOPIC = "/amcl_pose"
SCAN_TOPIC = "/scan"
# Nav2 Jazzy 는 nav2_msgs/ParticleCloud 로 /particle_cloud 에 낸다.
# 옛 이름 /particlecloud (geometry_msgs/PoseArray) 는 발행자가 없다.
PARTICLE_TOPIC = "/particle_cloud"
ROUTE_PLAN_TOPIC = "/navigation_manager/route_plan"
RECOVERY_PLAN_TOPIC = "/navigation_manager/recovery_plan"
APPLIED_MODE_TOPIC = "/teleop_limiter/applied_mode"
LOW_OBSTACLE_TOPIC = "/low_obstacle/observation"
SCAN_BASE_FRAME = "base_footprint"

# 무선 구간을 아끼려고 솎아 보낸다. 화면에서 "맵과 겹치나" 를 보는 데는
# 점 개수가 아니라 윤곽이 중요하다.
SCAN_POINTS = 120
PARTICLE_POINTS = 80
PLAN_POINTS = 60


def parse_low_obstacle_observation(data: str) -> dict | None:
    """Validate the small JSON contract published by the fusion node."""
    try:
        raw = json.loads(data)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("active"), bool):
        return None
    active = raw["active"]
    distance = raw.get("distance_m")
    fov = raw.get("fov_rad")
    state = raw.get("state")
    if active:
        if (
                not isinstance(distance, (int, float))
                or not math.isfinite(float(distance))
                or float(distance) <= 0.0
                or not isinstance(fov, (int, float))
                or not math.isfinite(float(fov))
                or not 0.0 < float(fov) < math.pi):
            return None
    return {
        "type": "low_obstacle",
        "active": active,
        "distance_m": round(float(distance), 3) if active else None,
        "fov_rad": round(float(fov), 4) if active else None,
        "state": state if isinstance(state, str) else None,
    }


class TeleopBridge(Node):
    def __init__(self):
        super().__init__("teleop_bridge")

        self.declare_parameter("backend_url", "https://mingkycarepro.site/api")
        self.declare_parameter("robot_id", "pinky-01")
        # 로봇 위치는 조작 화면에서 움직임을 판단하는 핵심 정보다. 진단
        # 레이어보다 가벼우므로 5Hz로 보내 화면 지연을 줄인다.
        self.declare_parameter("pose_interval_sec", 0.2)
        # 진단용 레이어는 더 느려도 된다. 라이다 윤곽과 파티클 퍼짐은
        # 1초에 한 번만 봐도 발산 여부를 판단할 수 있다.
        self.declare_parameter("diag_interval_sec", 1.0)
        self.declare_parameter("mode_status_interval_sec", 1.0)
        self.declare_parameter("mode_status_timeout_sec", 3.0)
        self.declare_parameter("reconnect_sec", 5.0)

        base = str(self.get_parameter("backend_url").value).rstrip("/")
        self.robot_id = self.get_parameter("robot_id").value
        # http(s) → ws(s). 같은 nginx 를 타므로 경로만 맞추면 된다.
        self.url = (base.replace("https://", "wss://").replace("http://", "ws://")
                    + f"/robots/{self.robot_id}/teleop/robot")
        self.pose_interval = float(self.get_parameter("pose_interval_sec").value)
        self.diag_interval = float(self.get_parameter("diag_interval_sec").value)
        self.mode_status_interval = max(
            0.1, float(self.get_parameter("mode_status_interval_sec").value))
        self.mode_status_timeout = max(
            self.mode_status_interval,
            float(self.get_parameter("mode_status_timeout_sec").value),
        )
        self.reconnect = float(self.get_parameter("reconnect_sec").value)

        self.cmd_pub = self.create_publisher(Twist, OUT_TOPIC, 10)

        # /scan 각도는 센서 프레임 기준이다. Pinky의 rplidar_link는 로봇에
        # 대해 180도 돌아가 있고 원점도 조금 어긋나 있으므로, 대시보드로
        # 보내기 전에 TF로 로봇 기준 좌표로 옮긴다. Nav2가 쓰는 것과 같은
        # 정본을 사용해야 관제 라이다도 실제 벽과 겹친다.
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self._scan_tf_warned = False
        # /scan은 10Hz지만 관제 진단 레이어는 기본 1Hz다. 모든 scan을 좌표
        # 변환한 뒤 9장을 버리지 않고, 전송 직전에 필요한 최신 한 장만 만든다.
        self._scan_snapshot_requested = threading.Event()
        self._particle_snapshot_requested = threading.Event()
        # LiDAR 장착 TF는 정적이므로 매 scan마다 TF 트리를 조회하지 않는다.
        self._scan_mount_transform = None
        self._scan_mount_frame_id = None

        # AMCL 은 set_initial_pose 로 (0,0,0) 에서 시작한다. 로봇이 실제로 거기
        # 있지 않으면 **틀린 위치를 확신한 채** 출발한다. 지금까지는 RViz 로만
        # 고칠 수 있었는데, 그러려면 로봇과 같은 망에 있어야 한다.
        # 대시보드에서 지도를 찍어 고칠 수 있게 이 경로를 연다.
        self.initpose_pub = self.create_publisher(
            PoseWithCovarianceStamped, "/initialpose", 10)

        # AMCL 은 신뢰성 낮은 QoS 로 내보낸다. 기본값으로 두면 못 받는다.
        self.create_subscription(
            PoseWithCovarianceStamped, POSE_TOPIC, self._on_pose,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self._pose = None

        # 라이다·파티클·경로. 로컬라이제이션이 맞는지 보려면 이 셋이 필요하다
        # (docs/nav2-debugging.md §AMCL). 라이다는 BEST_EFFORT 로 나온다.
        self.create_subscription(
            LaserScan, SCAN_TOPIC, self._on_scan,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.create_subscription(
            ParticleCloud, PARTICLE_TOPIC, self._on_particles,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        plan_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(
            Path, ROUTE_PLAN_TOPIC, self._on_route_plan, plan_qos)
        self.create_subscription(
            Path, RECOVERY_PLAN_TOPIC, self._on_recovery_plan, plan_qos)
        self.create_subscription(
            String,
            APPLIED_MODE_TOPIC,
            self._on_applied_mode,
            QoSProfile(
                depth=1,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                reliability=ReliabilityPolicy.RELIABLE,
            ),
        )
        self.create_subscription(
            String, LOW_OBSTACLE_TOPIC, self._on_low_obstacle_observation,
            plan_qos)
        self._scan = None
        self._particles = None
        self._plan = None
        self._recovery_plan = None
        self._applied_mode = None
        self._applied_mode_at = None
        self._low_obstacle = None
        self._low_obstacle_revision = 0
        self._low_obstacle_at = None

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

        self.get_logger().info(f"조작 브리지 시작 ({self.url})")

    def _on_pose(self, msg: PoseWithCovarianceStamped):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                         1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        self._pose = {"type": "pose", "x": p.x, "y": p.y, "yaw": yaw}

    def _on_applied_mode(self, msg: String):
        mode = msg.data.strip().lower()
        self._applied_mode = mode if mode in ("auto", "manual", "estop") else None
        self._applied_mode_at = time.monotonic()

    def _on_low_obstacle_observation(self, msg: String):
        observation = parse_low_obstacle_observation(msg.data)
        if observation is None:
            return
        self._low_obstacle = observation
        self._low_obstacle_revision += 1
        self._low_obstacle_at = time.monotonic()

    def _mode_status(self, now: float) -> dict:
        fresh = (
            self._applied_mode_at is not None
            and now - self._applied_mode_at <= self.mode_status_timeout
        )
        return {
            "type": "mode_status",
            "applied_mode": self._applied_mode if fresh else None,
            "fresh": fresh,
        }

    def _on_scan(self, msg: LaserScan):
        """센서 측정점을 TF로 로봇 기준 극좌표로 바꿔 보낸다.

        지도 좌표로 옮기는 계산을 여기서 하지 않는 이유는, 그러려면 pose 와
        시각을 맞춰야 하는데 그 정합은 화면이 pose 와 함께 갖고 있는 편이
        간단하기 때문이다. 여기서는 정적인 센서 장착 회전·오프셋만 적용하고,
        화면은 받은 pose 를 기준으로 지도 좌표까지 회전시켜 그린다.
        """
        if not self._scan_snapshot_requested.is_set():
            return
        # 요청을 먼저 소비해야 계산 중 전송 스레드가 넣은 다음 요청을
        # 마지막 clear()가 지우지 않는다.
        self._scan_snapshot_requested.clear()

        ranges = msg.ranges
        if not ranges:
            self._scan_snapshot_requested.set()
            return
        transform = self._scan_mount_transform
        if transform is None or self._scan_mount_frame_id != msg.header.frame_id:
            try:
                transform = self.tf_buffer.lookup_transform(
                    SCAN_BASE_FRAME, msg.header.frame_id, Time())
            except TransformException as exc:
                if not self._scan_tf_warned:
                    self.get_logger().warn(
                        f"라이다 TF를 찾지 못해 관제 전송을 건너뜁니다: "
                        f"{msg.header.frame_id} → {SCAN_BASE_FRAME}: {exc}")
                    self._scan_tf_warned = True
                self._scan_snapshot_requested.set()
                return
            self._scan_mount_transform = transform
            self._scan_mount_frame_id = msg.header.frame_id

        self._scan_tf_warned = False
        t = transform.transform.translation
        q = transform.transform.rotation
        translation = (t.x, t.y, t.z)
        rotation = (q.x, q.y, q.z, q.w)
        step = max(1, len(ranges) // SCAN_POINTS)
        points = []
        for i in range(0, len(ranges), step):
            r = ranges[i]
            # inf·nan 은 "못 맞음" 이다. 그리면 안 되므로 뺀다.
            if r is None or r != r or r == float("inf") or r <= msg.range_min:
                continue
            angle, distance = transform_polar_point(
                msg.angle_min + i * msg.angle_increment,
                float(r),
                translation=translation,
                rotation=rotation,
            )
            points.append([round(angle, 4), round(distance, 3)])
        self._scan = {"type": "scan", "points": points}

    def _on_particles(self, msg: ParticleCloud):
        """가중치는 버리고 위치만 보낸다. 화면이 보는 것은 퍼진 정도다."""
        if not self._particle_snapshot_requested.is_set():
            return
        self._particle_snapshot_requested.clear()
        particles = msg.particles
        if not particles:
            self._particle_snapshot_requested.set()
            return
        step = max(1, len(particles) // PARTICLE_POINTS)
        self._particles = {
            "type": "particles",
            "points": [[round(p.pose.position.x, 3), round(p.pose.position.y, 3)]
                       for p in particles[::step]],
        }

    def _request_diagnostic_snapshots(self, elapsed: float) -> None:
        """전송 직전 최신 scan과 다음 particle 표본을 요청한다."""
        scan_lead = min(0.2, self.diag_interval)
        if elapsed >= self.diag_interval - scan_lead:
            # websocket 루프가 0.1초 단위로 돌기 때문에 다음 진단 전송 전에
            # 최신 scan 한두 장만 변환한다. 오래된 1초 전 scan은 보내지 않는다.
            self._scan_snapshot_requested.set()

    @staticmethod
    def _path_payload(msg: Path, kind: str) -> dict:
        poses = msg.poses
        if not poses:
            return {"type": kind, "points": []}
        step = max(1, len(poses) // PLAN_POINTS)
        return {
            "type": kind,
            "points": [[round(p.pose.position.x, 3), round(p.pose.position.y, 3)]
                       for p in poses[::step]],
        }

    def _on_route_plan(self, msg: Path):
        self._plan = self._path_payload(msg, "plan")

    def _on_recovery_plan(self, msg: Path):
        self._recovery_plan = self._path_payload(msg, "recovery_plan")

    def _loop(self):
        if websocket is None:
            self.get_logger().error(
                "websocket-client 가 없다. sudo apt install python3-websocket")
            return

        failing = False
        while not self._stop.is_set():
            try:
                socket = websocket.create_connection(self.url, timeout=10)
                if failing:
                    self.get_logger().info("조작 브리지 연결 복구")
                    failing = False
                else:
                    self.get_logger().info("조작 브리지 연결됨")
                try:
                    self._serve(socket)
                finally:
                    socket.close()
            except Exception as exc:  # 연결 실패·중간 끊김 모두 여기로 온다
                if not failing:
                    self.get_logger().warn(f"조작 브리지 끊김: {exc}")
                    failing = True
            self._stop.wait(self.reconnect)

    def _serve(self, socket):
        """명령을 받아 발행하고, 주기적으로 pose 와 진단 레이어를 올린다."""
        self._particle_snapshot_requested.set()
        last_pose = 0.0
        # 첫 진단도 최신 센서 표본을 받을 시간을 둔 뒤 보낸다.
        last_diag = time.monotonic()
        last_mode_status = 0.0
        last_low_obstacle_revision = -1
        while not self._stop.is_set():
            # pose 를 보내려면 수신에서 오래 막히면 안 되므로 짧게 끊어 받는다.
            socket.settimeout(0.1)
            try:
                raw = socket.recv()
            except websocket.WebSocketTimeoutException:
                raw = None

            if raw:
                self._handle(raw)

            now = time.monotonic()
            diag_elapsed = now - last_diag
            self._request_diagnostic_snapshots(diag_elapsed)
            if self._pose is not None and now - last_pose >= self.pose_interval:
                last_pose = now
                socket.send(json.dumps(self._pose))

            if diag_elapsed >= self.diag_interval:
                last_diag = now
                for payload in (
                    self._scan,
                    self._particles,
                    self._plan,
                    self._recovery_plan,
                ):
                    if payload is not None:
                        socket.send(json.dumps(payload))
                # particle은 발행 주기가 낮을 수 있어 다음 갱신을 미리 잡는다.
                # scan은 위에서 전송 직전에만 별도로 요청한다.
                self._particle_snapshot_requested.set()

            if now - last_mode_status >= self.mode_status_interval:
                last_mode_status = now
                socket.send(json.dumps(self._mode_status(now)))

            if (
                    self._low_obstacle_at is not None
                    and now - self._low_obstacle_at > 1.0
                    and self._low_obstacle is not None
                    and self._low_obstacle.get("active") is True):
                # 융합 노드가 죽었는데 마지막 부채꼴이 지도에 고정돼 있으면
                # 현재 장애물처럼 보인다. 상태 배지는 별도 진단 경로가 맡고,
                # 공간 표시는 1초 안에 내린다.
                self._low_obstacle = {
                    "type": "low_obstacle",
                    "active": False,
                    "distance_m": None,
                    "fov_rad": None,
                    "state": "STALE",
                }
                self._low_obstacle_revision += 1

            revision = self._low_obstacle_revision
            if (
                    self._low_obstacle is not None
                    and revision != last_low_obstacle_revision):
                last_low_obstacle_revision = revision
                socket.send(json.dumps(self._low_obstacle))

    def _handle(self, raw: str):
        try:
            message = json.loads(raw)
        except ValueError:
            return
        kind = message.get("type")

        if kind == "set_pose":
            self._publish_initial_pose(message)
            return

        if kind != "cmd_vel":
            return

        twist = Twist()
        twist.linear.x = float(message.get("linear", 0.0))
        twist.angular.z = float(message.get("angular", 0.0))
        # 상한은 여기서 안 건다. teleop_limiter 가 잘라야 경로가 하나로 모인다.
        self.cmd_pub.publish(twist)

    def _publish_initial_pose(self, message: dict):
        """지도에서 찍은 위치를 AMCL 에 알린다. RViz 의 2D Pose Estimate 와 같다."""
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = float(message.get("x", 0.0))
        msg.pose.pose.position.y = float(message.get("y", 0.0))
        yaw = float(message.get("yaw", 0.0))
        msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(yaw / 2.0)

        # 사람이 눈대중으로 찍은 값이라 정확하지 않다. 분산을 넉넉히 줘야
        # AMCL 이 라이다로 보정할 여지를 갖는다. 0 에 가깝게 주면 틀린 값을
        # 그대로 확신한다. RViz 기본값과 같은 수준이다.
        msg.pose.covariance[0] = 0.25    # x
        msg.pose.covariance[7] = 0.25    # y
        msg.pose.covariance[35] = 0.068  # yaw

        self.initpose_pub.publish(msg)
        self.get_logger().info(
            f"초기 위치 지정: x={msg.pose.pose.position.x:.2f} "
            f"y={msg.pose.pose.position.y:.2f} yaw={math.degrees(yaw):.0f}°")

    def destroy_node(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
        return super().destroy_node()


def main():
    rclpy.init()
    node = TeleopBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
