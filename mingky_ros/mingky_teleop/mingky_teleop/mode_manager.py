"""주행 제어권이 누구에게 있는지를 로봇이 직접 들고 있는다.

    /mode/set (String) → mode_manager ─┬→ /mode            현재 모드 (늦게 붙어도 보임)
                                        ├→ /emergency_stop  안전 게이트를 건다
                                        ├→ /emergency_stop/release (서비스) 해제
                                        └→ /events          클라우드 타임라인에 남긴다

| 모드 | /cmd_vel 의 주인 |
| --- | --- |
| `auto` | Nav2 |
| `manual` | 텔레옵 |
| `estop` | 아무도 아님 (정지) |

## 왜 서버가 아니라 로봇이 갖고 있나

arming(`backend/app/arming.py`)은 백엔드가 소유하고 로봇이 폴링한다. 몇 초
끊겨도 QR 을 안 읽을 뿐이라 그래도 된다. 모드는 다르다. 통신이 끊긴 동안에도
로봇은 자기가 지금 누구 명령을 들어야 하는지 알아야 하고, estop 이 걸렸다면
연결과 무관하게 계속 걸려 있어야 한다. **서버가 소유하면 두절이 곧 안전
상태의 소실**이 된다.

그래서 서버는 요청만 하고(`set_mode` 명령 → `/mode/set`), 판단과 보관은 로봇이
한다. 서버는 이벤트로 결과를 본다.

## estop 을 직접 구현하지 않는 이유

`mingky_battery_guard` 의 `emergency_stop` 이 이미 안전 게이트다. 그쪽은
정지 상태를 **파일에 남겨 프로세스가 재시작돼도 유지**하고, LED 점멸과 Nav2
목표 취소까지 한다. 여기서 따로 만들면 정지 경로가 둘이 되고, 둘 중 약한
쪽(메모리에만 있는 쪽)이 먼저 풀린다.

그래서 이 노드는 **모드라는 개념만 소유**하고, estop 은 그 게이트에 위임한다.
게이트의 `emergency_stop/state` 가 정본이며, 여기 `/mode` 는 그것을 모드
어휘로 옮긴 표현이다.
"""

import uuid

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger

from mingky_interfaces.msg import Event

MODES = ("auto", "manual", "estop")
DEFAULT_MODE = "auto"

SET_TOPIC = "/mode/set"
MODE_TOPIC = "/mode"
EVENT_TOPIC = "/events"

# mingky_battery_guard 의 emergency_stop 인터페이스.
ESTOP_ENGAGE_TOPIC = "/emergency_stop"
ESTOP_RELEASE_SERVICE = "/emergency_stop/release"
ESTOP_STATE_TOPIC = "/emergency_stop/state"


def _latched(depth: int = 1) -> QoSProfile:
    """늦게 붙는 구독자도 마지막 값을 받게 한다.

    teleop_limiter 나 Foxglove 가 이 노드보다 늦게 떠도 현재 모드를 알아야
    한다. 모르는 채로 두면 조작을 막을지 열지 판단할 수 없다.
    """
    return QoSProfile(
        depth=depth,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        reliability=ReliabilityPolicy.RELIABLE,
    )


class ModeManager(Node):
    def __init__(self):
        super().__init__("mode_manager")

        self.declare_parameter("initial_mode", DEFAULT_MODE)
        self.declare_parameter("robot_id", "pinky-01")

        self.robot_id = self.get_parameter("robot_id").value

        requested = str(self.get_parameter("initial_mode").value).strip().lower()
        if requested not in MODES:
            self.get_logger().warn(
                f"모르는 initial_mode '{requested}' — {DEFAULT_MODE} 로 시작한다")
            requested = DEFAULT_MODE
        self.mode = requested

        self.mode_pub = self.create_publisher(String, MODE_TOPIC, _latched())
        self.event_pub = self.create_publisher(Event, EVENT_TOPIC, 10)
        self.estop_pub = self.create_publisher(Bool, ESTOP_ENGAGE_TOPIC, 10)
        self.release_client = self.create_client(Trigger, ESTOP_RELEASE_SERVICE)

        self.create_subscription(String, SET_TOPIC, self.on_set, 10)

        # 게이트가 정지 상태의 정본이다. 다른 경로(저전압 복귀, 장애물)로
        # 걸렸을 때도 모드가 따라가야 화면이 진실을 보여준다.
        self.create_subscription(
            Bool, ESTOP_STATE_TOPIC, self.on_estop_state, _latched())

        self._announce(previous=None, source="startup")
        self.get_logger().info(f"모드 관리 시작 (현재 {self.mode})")

    # ------------------------------------------------------------------ 전환

    def on_set(self, msg: String):
        requested = msg.data.strip().lower()

        if requested not in MODES:
            self.get_logger().warn(
                f"모르는 모드 요청 '{msg.data}' — 무시한다 (가능: {', '.join(MODES)})")
            return

        if requested == self.mode:
            return

        previous = self.mode
        self.mode = requested
        self.get_logger().warn(f"모드 전환: {previous} → {self.mode}")

        # 게이트를 먼저 움직인다. 상태 토픽이 돌아오면 on_estop_state 가
        # 같은 값이라 다시 알리지 않는다.
        if requested == "estop":
            self._apply_estop(True)
        elif previous == "estop":
            self._apply_estop(False)

        self._announce(previous=previous, source="remote")

    def on_estop_state(self, msg: Bool):
        """게이트가 걸리거나 풀리면 모드를 맞춘다.

        저전압 복귀나 장애물로 게이트가 걸릴 수도 있다. 그때 모드가 auto 로
        남아 있으면 화면은 "자동 주행 중" 이라고 하는데 로봇은 서 있다.
        """
        if msg.data and self.mode != "estop":
            previous, self.mode = self.mode, "estop"
            self.get_logger().warn(f"게이트 정지 감지: {previous} → estop")
            self._announce(previous=previous, source="gate")
        elif not msg.data and self.mode == "estop":
            self.mode = "auto"
            self.get_logger().warn("게이트 해제 감지: estop → auto")
            self._announce(previous="estop", source="gate")

    def _apply_estop(self, engage: bool):
        """게이트에 위임한다. 여기서 모터를 직접 건드리지 않는다."""
        if engage:
            self.estop_pub.publish(Bool(data=True))
            return
        if not self.release_client.service_is_ready():
            self.get_logger().error(
                f"{ESTOP_RELEASE_SERVICE} 가 없다. 게이트가 떠 있는지 확인하라")
            return
        self.release_client.call_async(Trigger.Request())

    def _announce(self, previous, source: str):
        self.mode_pub.publish(String(data=self.mode))

        prev = previous if previous is not None else self.mode
        self._emit("robot.mode_changed", Event.LEVEL_INFO,
                   f'{{"mode": "{self.mode}", "previous": "{prev}", '
                   f'"source": "{source}"}}')

        if self.mode == "estop":
            self._emit("robot.estop_engaged", Event.LEVEL_ERROR,
                       f'{{"source": "{source}"}}')
        elif previous == "estop":
            self._emit("robot.estop_released", Event.LEVEL_WARNING,
                       f'{{"source": "{source}"}}')

    def _emit(self, code: str, level: int, payload: str):
        """게이트웨이가 큐에 넣어 클라우드로 올린다.

        여기서 HTTP 를 직접 부르지 않는다. 서버가 느릴 때 모드 전환이 같이
        늦어지면 안 된다.
        """
        event = Event()
        event.event_id = str(uuid.uuid4())
        event.robot_id = self.robot_id
        event.session_id = 0
        event.occurred_at = self.get_clock().now().to_msg()
        event.level = level
        event.event_code = code
        event.source_node = "mode_manager"
        event.payload = payload
        self.event_pub.publish(event)


def main():
    rclpy.init()
    node = ModeManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
