"""로봇과 관제 조정층을 잇는 링크.

    guide_manager ──/guide_manager/fleet_intent──→ 이 노드 ──ws──→ 관제
    guide_manager ←──────/fleet/decision────────── 이 노드 ←─ws─── 관제

## 왜 별도 노드인가

`teleop_bridge` 에 얹을 수도 있었다. 소켓도 재접속 로직도 이미 거기 있다.
그런데 저쪽은 **사람이 로봇을 모는 통로**이고 여기는 **기계가 기계를 조정하는
통로**다. 둘을 한 노드에 두면 조작 브리지가 죽을 때 조정도 같이 죽고, 반대로
조정 때문에 조작 경로를 건드리게 된다. 사람의 손과 기계의 판정은 서로의
장애로 멈추면 안 된다.

## 이 노드는 로봇을 몰지 않는다

받는 것은 `proceed` 뿐이다. 그것을 토픽으로 흘려보낼 뿐이고, 목표를 취소하고
다시 보내는 일은 `guide_manager` 가 한다 — 주행 상태를 소유한 노드가 하나여야
한다는 것이 이 저장소의 규칙이다.

## 링크가 끊기면

**아무것도 안 하는 것이 아니라, 풀어준다.** 끊긴 순간 `proceed: true` 를 한 번
발행한다. 조정층은 안전장치가 아니라 교착 예방층이라, 조정이 사라지면 이
기능을 붙이기 전 동작(LiDAR·MPPI)으로 돌아가는 것이 맞다.

이 노드가 통째로 죽으면 그 발행조차 못 하므로, `guide_manager` 쪽에도
**데드맨 타이머**가 따로 있다. 그쪽이 진짜 안전망이고 여기는 흔한 경우를
빠르게 푸는 것뿐이다.
"""

import json
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

try:
    # teleop_bridge 와 같은 라이브러리(apt 의 python3-websocket)를 쓴다.
    # ROS 노드가 이미 스레드로 도는 구조라 asyncio 를 끌어들이지 않는다.
    import websocket
except ImportError:  # pragma: no cover
    websocket = None

INTENT_TOPIC = "/guide_manager/fleet_intent"
DECISION_TOPIC = "/fleet/decision"


class FleetAgent(Node):
    def __init__(self):
        super().__init__("fleet_agent")

        self.declare_parameter("backend_url", "https://mingkycarepro.site/api")
        self.declare_parameter("robot_id", "pinky-01")
        self.declare_parameter("reconnect_sec", 5.0)
        # 목표가 안 바뀌어도 이 주기로 다시 올린다. 서버는 오래된 목표를
        # 버리므로(INTENT_STALE), 조용히 있으면 조정 대상에서 빠진다.
        self.declare_parameter("intent_repeat_sec", 3.0)

        base = str(self.get_parameter("backend_url").value).rstrip("/")
        self.robot_id = str(self.get_parameter("robot_id").value)
        self.url = (base.replace("https://", "wss://").replace("http://", "ws://")
                    + f"/robots/{self.robot_id}/fleet")
        self.reconnect = float(self.get_parameter("reconnect_sec").value)
        self.intent_repeat = max(
            0.5, float(self.get_parameter("intent_repeat_sec").value))

        # 판정은 늦게 뜬 guide_manager 도 마지막 값을 받아야 한다. 못 받으면
        # 그쪽 데드맨이 돌아 hold 가 풀리는데, 그 사이 상대와 같은 외길에
        # 들어갈 수 있다.
        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.decision_pub = self.create_publisher(
            String, DECISION_TOPIC, state_qos)
        self.create_subscription(
            String, INTENT_TOPIC, self._on_intent, state_qos)

        self._intent = {"type": "intent", "goal_waypoint": None, "guiding": False}
        self._intent_dirty = True
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.get_logger().info(f"군집 링크 시작 ({self.url})")

    # --- ROS 쪽 -----------------------------------------------------------

    def _on_intent(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except ValueError:
            self.get_logger().warn(f"군집 목표 형식 오류: {msg.data!r}")
            return
        with self._lock:
            self._intent = {
                "type": "intent",
                "goal_waypoint": payload.get("goal_waypoint") or None,
                "guiding": bool(payload.get("guiding")),
            }
            self._intent_dirty = True

    def _publish_decision(self, payload: dict):
        message = String()
        message.data = json.dumps(payload)
        self.decision_pub.publish(message)

    def _release(self, reason: str):
        """조정이 없다는 사실을 알린다. 침묵과 구분되어야 한다."""
        self._publish_decision({
            "type": "decision", "proceed": True, "reason": reason,
            "blocked_by": None, "segments": [], "ttl_sec": None,
        })

    # --- 소켓 쪽 ----------------------------------------------------------

    def _run(self):
        if websocket is None:
            self.get_logger().error(
                "websocket-client 가 없습니다 (apt install python3-websocket). "
                "군집 조정 없이 계속 주행합니다.")
            self._release("no_client")
            return

        while not self._stop.is_set():
            try:
                socket = websocket.create_connection(self.url, timeout=10)
            except Exception as exc:  # noqa: BLE001
                self.get_logger().warn(f"군집 링크 실패, 재시도: {exc}")
                self._release("link_lost")
                self._stop.wait(self.reconnect)
                continue

            self.get_logger().info("군집 링크 연결됨")
            try:
                with self._lock:
                    self._intent_dirty = True     # 붙자마자 목표부터 올린다
                self._serve(socket)
            except Exception as exc:  # noqa: BLE001
                self.get_logger().warn(f"군집 링크 끊김: {exc}")
            finally:
                try:
                    socket.close()
                except Exception:  # noqa: BLE001
                    pass
                # 끊긴 것을 알린다. 이 발행이 없으면 guide_manager 는
                # 데드맨이 돌 때까지 서 있는다.
                self._release("link_lost")
            self._stop.wait(self.reconnect)

    def _serve(self, socket):
        last_intent = 0.0
        while not self._stop.is_set():
            with self._lock:
                dirty, intent = self._intent_dirty, dict(self._intent)
            now = time.monotonic()
            if dirty or now - last_intent >= self.intent_repeat:
                socket.send(json.dumps(intent))
                last_intent = now
                with self._lock:
                    self._intent_dirty = False

            # 판정을 기다리되 오래 막히면 안 된다. 목표가 바뀌었을 때
            # 곧바로 올려야 서버가 옛 목표로 판정하지 않는다.
            socket.settimeout(0.2)
            try:
                raw = socket.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                raise ConnectionError("빈 프레임")
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if payload.get("type") == "decision":
                self._publish_decision(payload)

    def destroy_node(self):
        self._stop.set()
        return super().destroy_node()


def main():
    rclpy.init()
    node = FleetAgent()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
