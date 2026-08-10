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

# twist_mux 의 lock. manual 동안 Nav2 입력만 막는다.
MANUAL_LOCK_TOPIC = "/manual_mode_lock"


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
        self.manual_lock_pub = self.create_publisher(
            Bool, MANUAL_LOCK_TOPIC, _latched())
        self.release_client = self.create_client(Trigger, ESTOP_RELEASE_SERVICE)

        self.create_subscription(String, SET_TOPIC, self.on_set, 10)

        # 게이트가 정지 상태의 정본이다. 다른 경로(저전압 복귀, 장애물)로
        # 걸렸을 때도 모드가 따라가야 화면이 진실을 보여준다.
        self.create_subscription(
            Bool, ESTOP_STATE_TOPIC, self.on_estop_state, _latched())

        # 게이트 응답을 기다리는 동안의 목적 모드. 확정 전에는 mode 를 안 바꾼다.
        self._pending_mode = None

        self._apply_manual_lock()
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

        # estop 이 얽힌 전환은 **게이트가 실제로 움직인 뒤에** 확정한다.
        # 요청만 하고 모드를 먼저 바꾸면, 게이트가 안 떠 있거나 요청이
        # 유실됐을 때 화면은 "정지됨" 인데 로봇은 그대로 달린다.
        # 확정은 on_estop_state 가 한다.
        if requested == "estop" or self.mode == "estop":
            self._pending_mode = requested
            self.get_logger().warn(
                f"모드 전환 요청: {self.mode} → {requested} (게이트 응답 대기)")
            self._apply_estop(engage=requested == "estop")
            return

        previous = self.mode
        self.mode = requested
        self.get_logger().warn(f"모드 전환: {previous} → {self.mode}")
        self._apply_manual_lock()
        self._announce(previous=previous, source="remote")

    def on_estop_state(self, msg: Bool):
        """게이트 상태가 정본이다. 여기서만 estop 모드를 확정한다.

        요청을 보낸 뒤가 아니라 게이트가 실제로 움직인 것을 보고 바꾼다.
        저전압 복귀나 장애물로 게이트가 걸릴 수도 있어서, 이 경로는 관제
        요청이 없을 때도 동작해야 한다. 그때 모드가 auto 로 남아 있으면
        화면은 "자동 주행 중" 인데 로봇은 서 있다.
        """
        engaged = bool(msg.data)

        if engaged and self.mode != "estop":
            previous, self.mode = self.mode, "estop"
            self._pending_mode = None
            self.get_logger().warn(f"게이트 정지 확인: {previous} → estop")
            self._apply_manual_lock()
            self._announce(previous=previous, source="gate")
            return

        if not engaged and self.mode == "estop":
            # 해제 요청에 담긴 목적 모드가 있으면 그리로, 없으면 auto.
            # 게이트가 다른 이유로 풀렸을 때 임의로 manual 로 두면 안 된다.
            target = self._pending_mode or "auto"
            self.mode = target if target != "estop" else "auto"
            self._pending_mode = None
            self.get_logger().warn(f"게이트 해제 확인: estop → {self.mode}")
            self._apply_manual_lock()
            self._announce(previous="estop", source="gate")

    def _apply_estop(self, engage: bool):
        """게이트에 위임한다. 여기서 모터를 직접 건드리지 않는다.

        요청이 나가지 못하면 모드를 바꾸지 않는다. 확정은 on_estop_state 가
        하므로, 게이트가 없으면 모드는 원래대로 남고 화면도 바뀌지 않는다.
        """
        if engage:
            self.estop_pub.publish(Bool(data=True))
            return

        if not self.release_client.service_is_ready():
            self._pending_mode = None
            self.get_logger().error(
                f"{ESTOP_RELEASE_SERVICE} 가 없다. 게이트가 떠 있는지 확인하라. "
                "정지는 걸린 채로 둔다")
            return
        self.release_client.call_async(Trigger.Request())

    def _apply_manual_lock(self):
        """manual 일 때만 Nav2 를 막는다.

        우선순위만으로는 텔레옵이 1 초 끊길 때마다 Nav2 가 제어권을 도로
        가져간다. 조작자가 잠깐 손을 뗀 사이 로봇이 스스로 출발하는 셈이라
        모드 표가 말하는 것과 다르다. lock 우선순위 50 은 텔레옵(100)보다
        낮고 Nav2(10)보다 높아 **Nav2 만** 막는다.
        """
        self.manual_lock_pub.publish(Bool(data=self.mode == "manual"))

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
