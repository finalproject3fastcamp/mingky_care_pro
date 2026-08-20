"""Expose a ROS Image topic as an on-demand, low-FPS MJPEG stream."""

from __future__ import annotations

import cv2
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_sensor_data,
)
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import Bool

from .mjpeg_server import MjpegServer


class ImageStreamerNode(Node):

    def __init__(self) -> None:
        super().__init__('camera_image_streamer')
        self.declare_parameter('image_topic', '/rear_camera/image_raw')
        self.declare_parameter('port', 8092)
        self.declare_parameter('max_fps', 10.0)
        self.declare_parameter('max_width', 640)
        self.declare_parameter('jpeg_quality', 60)
        self.declare_parameter('compressed_topic', '')
        self.declare_parameter('compressed_enable_topic', '')
        self.declare_parameter('compressed_jpeg_quality', 70)

        topic = str(self.get_parameter('image_topic').value)
        max_fps = float(self.get_parameter('max_fps').value)
        self._bridge = CvBridge()
        self._latest: Image | None = None
        compressed_topic = str(
            self.get_parameter('compressed_topic').value).strip()
        self._compressed_quality = int(
            self.get_parameter('compressed_jpeg_quality').value)
        self._compressed_pub = (
            self.create_publisher(
                CompressedImage, compressed_topic, qos_profile_sensor_data)
            if compressed_topic else None
        )
        compressed_enable_topic = str(
            self.get_parameter('compressed_enable_topic').value).strip()
        self._compressed_enable_topic = compressed_enable_topic
        self._compressed_enabled = not compressed_enable_topic
        if compressed_enable_topic:
            state_qos = QoSProfile(
                depth=1,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                reliability=ReliabilityPolicy.RELIABLE,
            )
            self.create_subscription(
                Bool,
                compressed_enable_topic,
                self._on_compressed_enabled,
                state_qos,
            )
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

    def _on_compressed_enabled(self, message: Bool) -> None:
        self._compressed_enabled = bool(message.data)

    def _on_image(self, message: Image) -> None:
        self._latest = message

    def _compressed_gate_allows_processing(self) -> bool:
        if not self._compressed_enable_topic:
            return True
        # 게이트 발행자가 없는 단독 카메라 점검과 Person Follow 장애 시에는
        # 기존 compressed 토픽을 보존한다. 발행자가 있을 때만 상태를 따른다.
        if self.count_publishers(self._compressed_enable_topic) == 0:
            return True
        return self._compressed_enabled

    def _publish_latest(self) -> None:
        compressed_needed = (
            self._compressed_pub is not None
            and self._compressed_gate_allows_processing()
            and self._compressed_pub.get_subscription_count() > 0
        )
        if (
                self._latest is None
                or not (self._server.has_viewers or compressed_needed)):
            return
        try:
            frame = self._bridge.imgmsg_to_cv2(
                self._latest, desired_encoding='bgr8')
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warning(f'Image 변환 실패: {exc}')
            return
        if compressed_needed:
            ok, encoded = cv2.imencode(
                '.jpg', frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), self._compressed_quality],
            )
            if ok:
                message = CompressedImage()
                message.header = self._latest.header
                message.format = 'jpeg'
                message.data = encoded.tobytes()
                self._compressed_pub.publish(message)
        if self._server.has_viewers:
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
