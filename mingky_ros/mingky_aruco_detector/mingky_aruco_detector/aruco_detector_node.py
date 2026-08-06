"""ROS Image에서 ArUco 마커 ID와 보정된 3차원 자세를 발행한다."""

import cv2

from cv_bridge import CvBridge, CvBridgeError

from geometry_msgs.msg import PoseStamped

from mingky_interfaces.msg import ArucoDetection, ArucoDetectionArray

import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from sensor_msgs.msg import CameraInfo, Image

from std_msgs.msg import Bool, Float32

from .detector import (
    ArucoPoseEstimator,
    CameraCalibration,
    load_ros_calibration,
)


class ArucoDetectorNode(Node):
    """Detect ArUco markers and publish calibrated camera-frame poses."""

    def __init__(self) -> None:
        """Declare parameters and connect ROS subscriptions and publishers."""
        super().__init__('aruco_detector')

        self.declare_parameter('image_topic', '/rear_camera/image_raw')
        self.declare_parameter('camera_info_topic', '/rear_camera/camera_info')
        self.declare_parameter('calibration_file', '')
        self.declare_parameter('dictionary', 'DICT_4X4_50')
        self.declare_parameter('marker_length', 0.026)
        self.declare_parameter('target_marker_id', -1)
        self.declare_parameter('process_every_n', 1)

        get = self.get_parameter
        self._target_marker_id = int(get('target_marker_id').value)
        self._process_every_n = max(1, int(get('process_every_n').value))
        self._frame_count = 0
        self._reported_missing_calibration = False
        self._reported_size_mismatch = False
        self._bridge = CvBridge()
        self._estimator = ArucoPoseEstimator(
            str(get('dictionary').value), float(get('marker_length').value))

        calibration_file = str(get('calibration_file').value).strip()
        self._calibration = (
            load_ros_calibration(calibration_file)
            if calibration_file else None
        )

        image_topic = str(get('image_topic').value)
        camera_info_topic = str(get('camera_info_topic').value)
        self._image_sub = self.create_subscription(
            Image, image_topic, self._on_image, qos_profile_sensor_data)
        self._camera_info_sub = None
        if self._calibration is None:
            self._camera_info_sub = self.create_subscription(
                CameraInfo, camera_info_topic, self._on_camera_info,
                qos_profile_sensor_data)

        self._detections_pub = self.create_publisher(
            ArucoDetectionArray, 'detections', 10)
        self._target_pose_pub = self.create_publisher(
            PoseStamped, 'target_pose', 10)
        self._target_distance_pub = self.create_publisher(
            Float32, 'target_distance', 10)
        self._target_visible_pub = self.create_publisher(
            Bool, 'target_visible', 10)

        calibration_source = calibration_file or camera_info_topic
        self.get_logger().info(
            f'ArUco 검출 시작 | image={image_topic} '
            f'| calibration={calibration_source} '
            f'| marker={get("marker_length").value}m '
            f'| target_id={self._target_marker_id}')

    def _on_camera_info(self, msg: CameraInfo) -> None:
        camera_matrix = np.asarray(msg.k, dtype=np.float64).reshape(3, 3)
        distortion = np.asarray(msg.d, dtype=np.float64).reshape(-1, 1)
        if msg.width <= 0 or msg.height <= 0 or distortion.size < 4:
            return
        if camera_matrix[0, 0] <= 0.0 or camera_matrix[1, 1] <= 0.0:
            return
        self._calibration = CameraCalibration(
            int(msg.width), int(msg.height), camera_matrix, distortion)
        self._reported_missing_calibration = False

    def _on_image(self, msg: Image) -> None:
        self._frame_count += 1
        if self._frame_count % self._process_every_n:
            return
        if self._calibration is None:
            if not self._reported_missing_calibration:
                self.get_logger().warn('캘리브레이션을 아직 받지 못해 검출을 건너뜁니다.')
                self._reported_missing_calibration = True
            return
        if (msg.width != self._calibration.width
                or msg.height != self._calibration.height):
            if not self._reported_size_mismatch:
                self.get_logger().error(
                    f'입력 {msg.width}x{msg.height}와 캘리브레이션 '
                    f'{self._calibration.width}x'
                    f'{self._calibration.height}가 다릅니다.')
                self._reported_size_mismatch = True
            return

        try:
            image = self._bridge.imgmsg_to_cv2(msg, desired_encoding='mono8')
        except CvBridgeError as exc:
            self.get_logger().error(f'Image 변환 실패: {exc}')
            return

        try:
            detections = self._estimator.detect(image, self._calibration)
        except (cv2.error, ValueError) as exc:
            self.get_logger().error(f'ArUco 검출 실패: {exc}')
            return

        output = ArucoDetectionArray()
        output.header = msg.header
        for detection in detections:
            item = ArucoDetection()
            item.marker_id = detection.marker_id
            item.pose.position.x = float(detection.translation[0])
            item.pose.position.y = float(detection.translation[1])
            item.pose.position.z = float(detection.translation[2])
            item.pose.orientation.x = detection.quaternion[0]
            item.pose.orientation.y = detection.quaternion[1]
            item.pose.orientation.z = detection.quaternion[2]
            item.pose.orientation.w = detection.quaternion[3]
            item.distance = detection.distance
            item.forward_distance = float(detection.translation[2])
            item.lateral_offset = float(detection.translation[0])
            item.reprojection_error = detection.reprojection_error
            output.detections.append(item)
        self._detections_pub.publish(output)

        target = self._select_target(output.detections)
        self._target_visible_pub.publish(Bool(data=target is not None))
        if target is None:
            return

        pose = PoseStamped()
        pose.header = output.header
        pose.pose = target.pose
        self._target_pose_pub.publish(pose)
        self._target_distance_pub.publish(Float32(data=target.distance))

    def _select_target(self, detections):
        if self._target_marker_id >= 0:
            return next((item for item in detections
                         if item.marker_id == self._target_marker_id), None)
        return min(detections, key=lambda item: item.distance, default=None)


def main(args=None) -> None:
    """Run the ArUco detector node."""
    rclpy.init(args=args)
    node = ArucoDetectorNode()
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
