"""후방 ROS Image에서 QR 내용과 보정된 거리를 발행한다."""

import time

import cv2
from cv_bridge import CvBridge, CvBridgeError
from mingky_aruco_detector.detector import (
    CameraCalibration,
    load_ros_calibration,
)
from mingky_interfaces.msg import GuideState, QrObservation
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_sensor_data,
)
from sensor_msgs.msg import CameraInfo, Image

from .detector import QrPoseEstimator


class QrDistanceNode(Node):
    """Detect one rear QR and publish its current observation."""

    def __init__(self) -> None:
        super().__init__('rear_qr_distance')
        self.declare_parameter('image_topic', '/rear_camera/image_raw')
        self.declare_parameter('camera_info_topic', '/rear_camera/camera_info')
        self.declare_parameter('calibration_file', '')
        self.declare_parameter('qr_size', 0.028)
        self.declare_parameter('process_every_n', 2)
        self.declare_parameter('max_process_fps', 5.0)
        self.declare_parameter('process_only_while_guiding', False)

        get = self.get_parameter
        self._process_every_n = max(1, int(get('process_every_n').value))
        max_process_fps = max(0.1, float(get('max_process_fps').value))
        self._min_process_interval = 1.0 / max_process_fps
        self._last_process_at = 0.0
        self._frame_count = 0
        self._process_only_while_guiding = bool(
            get('process_only_while_guiding').value)
        self._guiding = not self._process_only_while_guiding
        self._bridge = CvBridge()
        self._estimator = QrPoseEstimator(float(get('qr_size').value))
        calibration_file = str(get('calibration_file').value).strip()
        self._calibration = (
            load_ros_calibration(calibration_file)
            if calibration_file else None
        )
        self._warned_calibration = False
        self._warned_size = False

        self._observation_pub = self.create_publisher(
            QrObservation, 'observation', 10)
        self.create_subscription(
            Image, str(get('image_topic').value), self._on_image,
            qos_profile_sensor_data)
        if self._calibration is None:
            self.create_subscription(
                CameraInfo, str(get('camera_info_topic').value),
                self._on_camera_info, qos_profile_sensor_data)
        if self._process_only_while_guiding:
            state_qos = QoSProfile(
                depth=1,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                reliability=ReliabilityPolicy.RELIABLE,
            )
            self.create_subscription(
                GuideState,
                '/guide_manager/state',
                self._on_guide_state,
                state_qos,
            )

        source = calibration_file or str(get('camera_info_topic').value)
        self.get_logger().info(
            f'후방 QR 거리 측정 시작 | calibration={source} '
            f'| qr_size={get("qr_size").value}m')

    def _on_guide_state(self, msg: GuideState) -> None:
        self._guiding = (
            msg.session_state == GuideState.SESSION_GUIDING
            and msg.session_id > 0
        )

    def _on_camera_info(self, msg: CameraInfo) -> None:
        matrix = np.asarray(msg.k, dtype=np.float64).reshape(3, 3)
        distortion = np.asarray(msg.d, dtype=np.float64).reshape(-1, 1)
        if (msg.width <= 0 or msg.height <= 0 or distortion.size < 4
                or matrix[0, 0] <= 0 or matrix[1, 1] <= 0):
            return
        self._calibration = CameraCalibration(
            int(msg.width), int(msg.height), matrix, distortion)
        self._warned_calibration = False

    def _on_image(self, msg: Image) -> None:
        if not self._guiding:
            return
        self._frame_count += 1
        if self._frame_count % self._process_every_n:
            return
        now = time.monotonic()
        if now - self._last_process_at < self._min_process_interval:
            return
        self._last_process_at = now
        if self._calibration is None:
            if not self._warned_calibration:
                self.get_logger().warn('캘리브레이션을 기다리는 중입니다.')
                self._warned_calibration = True
            return
        if (msg.width != self._calibration.width
                or msg.height != self._calibration.height):
            if not self._warned_size:
                self.get_logger().error(
                    f'입력 {msg.width}x{msg.height}와 캘리브레이션 '
                    f'{self._calibration.width}x{self._calibration.height}가 다릅니다.')
                self._warned_size = True
            return
        try:
            image = self._bridge.imgmsg_to_cv2(msg, desired_encoding='mono8')
            detection = self._estimator.detect(image, self._calibration)
        except (CvBridgeError, cv2.error, ValueError) as exc:
            self.get_logger().error(f'QR 거리 측정 실패: {exc}')
            return

        observation = QrObservation()
        observation.header = msg.header
        observation.visible = detection is not None
        if detection is not None:
            observation.data = detection.data
            observation.distance = detection.distance
            observation.center_x = detection.image_center[0]
            observation.center_y = detection.image_center[1]
        self._observation_pub.publish(observation)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = QrDistanceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
