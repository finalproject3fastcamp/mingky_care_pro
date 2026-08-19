"""Guide Manager 상태를 Pinky LCD에 표시하는 ROS 노드."""

from mingky_interfaces.msg import GuideState

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool

from .renderer import render_view, resolve_font_path
from .view_model import DisplayView, build_display_view

# fire_evac_node(실험 단계, mingky_fire_evac)가 화재를 감지해 대피 이동을
# 시작하면 이 화면으로 강제 전환한다. GuideState 체계는 guide_manager 만
# 발행한다는 규칙(GuideState.msg 주석 참고)을 지키려고, 여기서는 GuideState를
# 새로 만들지 않고 별도 Bool 토픽으로 "지금은 이 화면이 우선이다"만 받는다.
_EMERGENCY_VIEW = DisplayView(
    '긴급 상황 — 화재 감지', '대피소로 이동 중입니다',
    route_to='대피소',
    instruction='로봇을 따라와 주세요',
    accent='red',
)


class LcdStatusNode(Node):
    """Render the selected robot's latest GuideState on the physical LCD."""

    def __init__(self):
        """Initialize the LCD and subscribe with transient-local durability."""
        super().__init__('lcd_status')
        self.declare_parameter('robot_id', 'pinky-01')
        self.declare_parameter('font_path', '')
        self.robot_id = str(self.get_parameter('robot_id').value)
        configured_font = str(self.get_parameter('font_path').value)
        self.font_path = resolve_font_path(configured_font)
        if not self.font_path:
            self.get_logger().warn(
                '한글 글꼴을 찾지 못했습니다. fonts-noto-cjk 설치를 권장합니다.')

        # GPIO/SPI 모듈은 실제 노드를 띄울 때만 불러온다. view/renderer 테스트는
        # Raspberry Pi가 아닌 개발 PC에서도 실행할 수 있어야 한다.
        from pinky_emotion.pinky_lcd import LCD
        self.lcd = LCD()
        self._last_view = None
        self._evacuating = False
        # 대피가 끝나면 원래 보여주던 안내 화면으로 돌아가야 하니, GuideState
        # 기반 화면을 최신으로 하나 저장해둔다 (대피 중엔 화면에 못 띄우고
        # 저장만 해둠).
        self._last_guide_view = build_display_view(
            robot_state=GuideState.ROBOT_IDLE,
            session_state=GuideState.SESSION_NONE,
            previous_visit='',
            current_visit='',
        )

        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(
            GuideState, '/guide_manager/state', self._on_state, state_qos)
        # mingky_fire_evac (실험 단계). 없어도(토픽 발행 노드가 아직 안 떠도)
        # 그냥 계속 평상시 화면만 보여주면 되니 문제 없다.
        self.create_subscription(
            Bool, '/fire_evac/active', self._on_fire_evac, state_qos)
        self._show(self._last_guide_view)
        self.get_logger().info(
            f'LCD 안내 상태 표시 시작 (robot_id={self.robot_id})')

    def _on_state(self, msg: GuideState) -> None:
        if msg.robot_id != self.robot_id:
            return
        self._last_guide_view = build_display_view(
            robot_state=msg.robot_state,
            session_state=msg.session_state,
            previous_visit=msg.previous_visit,
            current_visit=msg.current_visit,
        )
        if self._evacuating:
            # 대피 화면이 우선이다. 나중에 대피가 끝나면 방금 저장한 걸로
            # 복원된다.
            return
        self._show(self._last_guide_view)

    def _on_fire_evac(self, msg: Bool) -> None:
        self._evacuating = bool(msg.data)
        self._show(_EMERGENCY_VIEW if self._evacuating else self._last_guide_view)

    def _show(self, view: DisplayView) -> None:
        if view == self._last_view:
            return
        self.lcd.img_show(render_view(view, self.font_path))
        self._last_view = view

    def destroy_node(self):
        """Clear the display and release its hardware resources."""
        try:
            self.lcd.clear()
        finally:
            try:
                self.lcd.close()
            finally:
                super().destroy_node()


def main(args=None):
    """Run the LCD status node."""
    rclpy.init(args=args)
    node = LcdStatusNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
