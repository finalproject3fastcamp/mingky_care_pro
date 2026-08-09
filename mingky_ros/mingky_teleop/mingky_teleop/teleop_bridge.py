"""대시보드의 실시간 조작을 로봇 안으로 들여오고, 위치를 내보낸다.

    관제 wss ──→ teleop_bridge ──→ /cmd_vel_teleop_raw → teleop_limiter → twist_mux
    관제 wss ──→ teleop_bridge ──→ /initialpose  (지도에서 위치를 찍어줄 때)
    관제 wss ←── teleop_bridge ←── /amcl_pose, /scan, /particle_cloud, /plan

## 왜 로봇이 서버로 거는가

로봇은 NAT 뒤에 있어 서버가 들어올 수 없다. `orders` 와 같은 이유로 나가는
연결만 만든다. 끊기면 다시 건다.

## 끊기면 어떻게 되나

**아무것도 하지 않는다.** 명령이 안 들어오면 `/cmd_vel_teleop_raw` 발행이
멈추고, teleop_limiter → twist_mux 도 조용해져 1초 뒤 워치독이 모터를 세운다.
여기서 굳이 0 속도를 쏘지 않는 이유는, 그 코드가 필요한 상황이면 이미 소켓이
끊겨 있어 실행될 보장이 없기 때문이다. **정지를 연결에 의존시키지 않는다.**

## 왜 pose 를 여기서 보내나

지도 위에 로봇을 그리려면 위치가 필요한데, 이벤트로 보내면 안 된다. 2Hz 로
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
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan

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
PLAN_TOPIC = "/plan"

# 무선 구간을 아끼려고 솎아 보낸다. 화면에서 "맵과 겹치나" 를 보는 데는
# 점 개수가 아니라 윤곽이 중요하다.
SCAN_POINTS = 120
PARTICLE_POINTS = 80
PLAN_POINTS = 60


class TeleopBridge(Node):
    def __init__(self):
        super().__init__("teleop_bridge")

        self.declare_parameter("backend_url", "https://mingkycarepro.site/api")
        self.declare_parameter("robot_id", "pinky-01")
        self.declare_parameter("pose_interval_sec", 0.5)
        # 진단용 레이어는 더 느려도 된다. 라이다 윤곽과 파티클 퍼짐은
        # 1초에 한 번만 봐도 발산 여부를 판단할 수 있다.
        self.declare_parameter("diag_interval_sec", 1.0)
        self.declare_parameter("reconnect_sec", 5.0)

        base = str(self.get_parameter("backend_url").value).rstrip("/")
        self.robot_id = self.get_parameter("robot_id").value
        # http(s) → ws(s). 같은 nginx 를 타므로 경로만 맞추면 된다.
        self.url = (base.replace("https://", "wss://").replace("http://", "ws://")
                    + f"/robots/{self.robot_id}/teleop/robot")
        self.pose_interval = float(self.get_parameter("pose_interval_sec").value)
        self.diag_interval = float(self.get_parameter("diag_interval_sec").value)
        self.reconnect = float(self.get_parameter("reconnect_sec").value)

        self.cmd_pub = self.create_publisher(Twist, OUT_TOPIC, 10)

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
        self.create_subscription(Path, PLAN_TOPIC, self._on_plan, 1)
        self._scan = None
        self._particles = None
        self._plan = None

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

    def _on_scan(self, msg: LaserScan):
        """로봇 기준 극좌표 그대로 보낸다.

        지도 좌표로 옮기는 계산을 여기서 하지 않는 이유는, 그러려면 pose 와
        시각을 맞춰야 하는데 그 정합은 화면이 pose 와 함께 갖고 있는 편이
        간단하기 때문이다. 화면은 받은 pose 를 기준으로 회전시켜 그린다.
        """
        ranges = msg.ranges
        if not ranges:
            return
        step = max(1, len(ranges) // SCAN_POINTS)
        points = []
        for i in range(0, len(ranges), step):
            r = ranges[i]
            # inf·nan 은 "못 맞음" 이다. 그리면 안 되므로 뺀다.
            if r is None or r != r or r == float("inf") or r <= msg.range_min:
                continue
            points.append([round(msg.angle_min + i * msg.angle_increment, 4),
                           round(float(r), 3)])
        self._scan = {"type": "scan", "points": points}

    def _on_particles(self, msg: ParticleCloud):
        """가중치는 버리고 위치만 보낸다. 화면이 보는 것은 퍼진 정도다."""
        particles = msg.particles
        if not particles:
            return
        step = max(1, len(particles) // PARTICLE_POINTS)
        self._particles = {
            "type": "particles",
            "points": [[round(p.pose.position.x, 3), round(p.pose.position.y, 3)]
                       for p in particles[::step]],
        }

    def _on_plan(self, msg: Path):
        poses = msg.poses
        if not poses:
            self._plan = {"type": "plan", "points": []}
            return
        step = max(1, len(poses) // PLAN_POINTS)
        self._plan = {
            "type": "plan",
            "points": [[round(p.pose.position.x, 3), round(p.pose.position.y, 3)]
                       for p in poses[::step]],
        }

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
        last_pose = 0.0
        last_diag = 0.0
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
            if self._pose is not None and now - last_pose >= self.pose_interval:
                last_pose = now
                socket.send(json.dumps(self._pose))

            if now - last_diag >= self.diag_interval:
                last_diag = now
                for payload in (self._scan, self._particles, self._plan):
                    if payload is not None:
                        socket.send(json.dumps(payload))

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
