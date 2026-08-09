"""사람이 보내는 속도 명령에 상한을 씌우고, manual 모드에서만 흘린다.

    cmd_vel_teleop_raw → (모드 확인 → 상한) → cmd_vel_teleop → twist_mux → cmd_vel

왜 상한이 필요한가. 원격 조작에는 왕복 지연이 있다. 클라우드 경유 실측이
260~340ms 라, 누른 뒤 로봇이 반응하기까지 시간이 걸린다. 조작자는 반응이
없다고 느껴 더 밀어 넣기 쉽고, 그 입력이 뒤늦게 한꺼번에 반영된다. 속도가
낮으면 그 사이 이동 거리가 짧아 회복할 여지가 생긴다.

왜 Foxglove 패널 설정으로 막지 않는가. 패널의 속도 값은 조작자가 언제든
바꿀 수 있다. 로봇 쪽에서 한 번 더 자르지 않으면 상한이 아니라 권고다.

**멈추는 책임은 이 노드에 없다.** 명령이 끊겼을 때 세우는 것은 twist_mux 의
timeout 과 pinky_bringup 의 워치독이다. 여기서는 크기만 자른다. 자르는 일과
세우는 일을 한 노드에 두면, 이 노드가 죽었을 때 둘 다 사라진다.

## 모드 확인

`mode_manager` 가 발행하는 `/mode` 가 `manual` 일 때만 흘린다. 토픽에 값이
있다고 조작이 되면 안 되고, 의료진이나 관제가 "지금 사람이 몬다" 를 명시한
뒤에만 열려야 하기 때문이다.

**모드를 한 번도 못 받았으면 막는다.** mode_manager 가 안 떠 있는 상태를
"제한 없음" 으로 해석하면, 안전장치가 빠진 채로 조작이 열린다. 모르면 막는
쪽이 맞다.

estop 은 여기서 막지 않아도 twist_mux 의 lock 이 막는다. 다만 여기서도 함께
막아 두 겹으로 만든다.
"""

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

IN_TOPIC = "cmd_vel_teleop_raw"
OUT_TOPIC = "cmd_vel_teleop"
MODE_TOPIC = "/mode"
PASS_MODE = "manual"


def _clamp(value: float, limit: float) -> float:
    """부호는 두고 크기만 자른다."""
    if value > limit:
        return limit
    if value < -limit:
        return -limit
    return value


class TeleopLimiter(Node):
    def __init__(self):
        super().__init__("teleop_limiter")

        # 기본값은 보수적으로 잡았다. 실측 지연과 현장 통로 폭을 보고 조정한다.
        self.declare_parameter("max_linear", 0.15)
        self.declare_parameter("max_angular", 0.6)

        self.max_linear = self.get_parameter("max_linear").value
        self.max_angular = self.get_parameter("max_angular").value

        self.pub = self.create_publisher(Twist, OUT_TOPIC, 10)
        self.sub = self.create_subscription(Twist, IN_TOPIC, self.on_twist, 10)

        # mode_manager 가 늦게 떠도 마지막 값을 받아야 한다.
        self.create_subscription(
            String, MODE_TOPIC, self.on_mode,
            QoSProfile(depth=1,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL,
                       reliability=ReliabilityPolicy.RELIABLE))
        self.mode = None

        # 매번 찍으면 로그가 덮인다. 상태가 바뀔 때만 알린다.
        self.warned = False
        self.blocked_logged = False

        self.get_logger().info(
            f"텔레옵 상한: 직진 {self.max_linear} m/s, 회전 {self.max_angular} rad/s"
        )
        self.get_logger().info(f"{IN_TOPIC} → {OUT_TOPIC} ({PASS_MODE} 모드에서만)")

    def on_mode(self, msg: String):
        new = msg.data.strip().lower()
        if new != self.mode:
            self.get_logger().info(f"모드 수신: {self.mode} → {new}")
            self.mode = new
            self.blocked_logged = False

    def on_twist(self, msg: Twist):
        if self.mode != PASS_MODE:
            if not self.blocked_logged:
                self.blocked_logged = True
                if self.mode is None:
                    self.get_logger().warn(
                        "모드를 아직 받지 못해 텔레옵을 막는다. "
                        "mode_manager 가 떠 있는지 확인하라")
                else:
                    self.get_logger().warn(
                        f"현재 모드가 '{self.mode}' 라 텔레옵을 막는다. "
                        f"'{PASS_MODE}' 로 전환해야 조작된다")
            return

        out = Twist()
        out.linear.x = _clamp(msg.linear.x, self.max_linear)
        out.linear.y = _clamp(msg.linear.y, self.max_linear)
        out.angular.z = _clamp(msg.angular.z, self.max_angular)

        if not self.warned and (
            out.linear.x != msg.linear.x or out.angular.z != msg.angular.z
        ):
            self.warned = True
            self.get_logger().warn(
                "상한을 넘는 명령이 들어와 잘랐다. "
                f"직진 {msg.linear.x:.2f}→{out.linear.x:.2f}, "
                f"회전 {msg.angular.z:.2f}→{out.angular.z:.2f}"
            )

        self.pub.publish(out)


def main():
    rclpy.init()
    node = TeleopLimiter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
