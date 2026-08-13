"""Guide Manager 상태를 Pinky LCD에 표시하는 ROS 노드."""

from mingky_interfaces.msg import GuideState

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from .renderer import render_view, resolve_font_path
from .view_model import DisplayView, build_display_view


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

        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(
            GuideState, '/guide_manager/state', self._on_state, state_qos)
        self._show(build_display_view(
            robot_state=GuideState.ROBOT_IDLE,
            session_state=GuideState.SESSION_NONE,
            previous_visit='',
            current_visit='',
        ))
        self.get_logger().info(
            f'LCD 안내 상태 표시 시작 (robot_id={self.robot_id})')

    def _on_state(self, msg: GuideState) -> None:
        if msg.robot_id != self.robot_id:
            return
        self._show(build_display_view(
            robot_state=msg.robot_state,
            session_state=msg.session_state,
            previous_visit=msg.previous_visit,
            current_visit=msg.current_visit,
        ))

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
