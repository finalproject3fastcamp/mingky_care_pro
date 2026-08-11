"""Expose a ROS Image topic as an on-demand, low-FPS MJPEG stream."""

from __future__ import annotations

import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

from .mjpeg_server import MjpegServer


class ImageStreamerNode(Node):

    def __init__(self) -> None:
        super().__init__('camera_image_streamer')
        self.declare_parameter('image_topic', '/rear_camera/image_raw')
        self.declare_parameter('port', 8092)
        self.declare_parameter('max_fps', 10.0)
        self.declare_parameter('max_width', 640)
        self.declare_parameter('jpeg_quality', 60)

        topic = str(self.get_parameter('image_topic').value)
        max_fps = float(self.get_parameter('max_fps').value)
        self._bridge = CvBridge()
        self._latest: Image | None = None
        self._server = MjpegServer(
            int(self.get_parameter('port').value),
            self.get_logger(),
            max_fps=max_fps,
            max_width=int(self.get_parameter('max_width').value),
            quality=int(self.get_parameter('jpeg_quality').value),
        )
        self.create_subscription(
            Image, topic, self._on_image, qos_profile_sensor_data)
        self.create_timer(1.0 / max(max_fps, 0.1), self._publish_latest)
        self.get_logger().info(f'camera stream source: {topic}')

    def _on_image(self, message: Image) -> None:
        self._latest = message

    def _publish_latest(self) -> None:
        if not self._server.has_viewers or self._latest is None:
            return
        try:
            frame = self._bridge.imgmsg_to_cv2(
                self._latest, desired_encoding='bgr8')
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warning(f'Image 변환 실패: {exc}')
            return
        self._server.update(frame)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ImageStreamerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
