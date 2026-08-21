#!/usr/bin/env python3
"""Fuse front ultrasonic/LiDAR data and limit only unsafe forward motion."""

from __future__ import annotations

import math

from geometry_msgs.msg import Twist
import rclpy
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import (
    qos_profile_sensor_data,
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from rclpy.time import Time
from sensor_msgs.msg import LaserScan, Range
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener

from .fusion import (
    FusionConfig,
    FusionDecision,
    limit_forward_velocity,
    LowObstacleFilter,
    nearest_lidar_in_ultrasonic_cone,
)


class LowObstacleSupervisor(Node):
    """Publish a conservative local obstacle cone and gate forward velocity."""

    def __init__(self, **kwargs):
        super().__init__('low_obstacle_supervisor', **kwargs)

        parameters = {
            'enabled': True,
            'range_topic': '/us_sensor/range',
            'scan_topic': '/scan',
            'filtered_range_topic': '/low_obstacle/range',
            'state_topic': '/low_obstacle/state',
            'cmd_vel_input_topic': 'cmd_vel_low_obstacle_input',
            'cmd_vel_output_topic': 'cmd_vel_safety_input',
            'scan_stale_sec': 0.5,
            'range_stale_sec': 0.5,
            'transform_timeout_sec': 0.05,
            'detect_distance_m': 0.30,
            'clear_distance_m': 0.35,
            'slow_distance_m': 0.15,
            'stop_distance_m': 0.07,
            'slow_speed_mps': 0.08,
            'lidar_margin_m': 0.15,
            'median_samples': 3,
            'confirmation_window': 5,
            'confirmations_required': 3,
            'clear_confirmations': 3,
            'near_window': 3,
            'near_confirmations': 2,
        }
        for name, default in parameters.items():
            self.declare_parameter(name, default)
        get = self.get_parameter

        self.enabled = bool(get('enabled').value)
        self.scan_stale_sec = max(0.1, float(get('scan_stale_sec').value))
        self.range_stale_sec = max(0.1, float(get('range_stale_sec').value))
        self.transform_timeout = Duration(
            seconds=max(0.0, float(get('transform_timeout_sec').value)))
        self.filter = LowObstacleFilter(FusionConfig(
            detect_distance_m=float(get('detect_distance_m').value),
            clear_distance_m=float(get('clear_distance_m').value),
            slow_distance_m=float(get('slow_distance_m').value),
            stop_distance_m=float(get('stop_distance_m').value),
            slow_speed_mps=float(get('slow_speed_mps').value),
            lidar_margin_m=float(get('lidar_margin_m').value),
            median_samples=int(get('median_samples').value),
            confirmation_window=int(get('confirmation_window').value),
            confirmations_required=int(get('confirmations_required').value),
            clear_confirmations=int(get('clear_confirmations').value),
            near_window=int(get('near_window').value),
            near_confirmations=int(get('near_confirmations').value),
        ))

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self._latest_lidar_range: float | None = None
        self._latest_lidar_at_ns = 0
        self._latest_range_at_ns = 0
        self._ultrasonic_frame = 'ultrasonic_link'
        self._ultrasonic_fov = 0.26
        self._decision = self.filter.stale_decision()
        self._last_published_state = ''

        latched = QoSProfile(
            depth=1,
            history=QoSHistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.range_pub = self.create_publisher(
            Range, str(get('filtered_range_topic').value),
            qos_profile_sensor_data)
        self.state_pub = self.create_publisher(
            String, str(get('state_topic').value), latched)
        self.cmd_pub = self.create_publisher(
            Twist, str(get('cmd_vel_output_topic').value), 10)
        self.create_subscription(
            Range, str(get('range_topic').value), self._on_range,
            qos_profile_sensor_data)
        self.create_subscription(
            LaserScan, str(get('scan_topic').value), self._on_scan,
            qos_profile_sensor_data)
        self.create_subscription(
            Twist, str(get('cmd_vel_input_topic').value), self._on_cmd, 10)
        self.create_timer(0.1, self._check_stale)

        self._publish_state('STARTING' if self.enabled else 'DISABLED')
        self.get_logger().info(
            '저상 장애물 감독 시작: 초음파/LiDAR 융합, '
            f'감지={self.filter.config.detect_distance_m:.2f}m, '
            f'감속={self.filter.config.slow_distance_m:.2f}m, '
            f'전진차단={self.filter.config.stop_distance_m:.2f}m')

    def _on_scan(self, msg: LaserScan) -> None:
        if not self.enabled:
            return
        target_frame = self._ultrasonic_frame
        try:
            transform = self.tf_buffer.lookup_transform(
                target_frame,
                msg.header.frame_id,
                Time.from_msg(msg.header.stamp),
                timeout=self.transform_timeout,
            )
        except TransformException as exc:
            self.get_logger().warn(
                f'LiDAR→초음파 TF 변환 실패: {exc}',
                throttle_duration_sec=2.0)
            return

        translation = transform.transform.translation
        rotation = transform.transform.rotation
        yaw = math.atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        lidar_range = nearest_lidar_in_ultrasonic_cone(
            msg.ranges,
            angle_min=float(msg.angle_min),
            angle_increment=float(msg.angle_increment),
            range_min=float(msg.range_min),
            range_max=float(msg.range_max),
            scan_to_ultrasonic_x=float(translation.x),
            scan_to_ultrasonic_y=float(translation.y),
            scan_to_ultrasonic_yaw=yaw,
            ultrasonic_fov=self._ultrasonic_fov,
        )
        if lidar_range is not None:
            self._latest_lidar_range = lidar_range
            self._latest_lidar_at_ns = self.get_clock().now().nanoseconds

    def _on_range(self, msg: Range) -> None:
        if not self.enabled:
            return
        self._latest_range_at_ns = self.get_clock().now().nanoseconds
        if msg.header.frame_id:
            self._ultrasonic_frame = msg.header.frame_id
        if 0.0 < float(msg.field_of_view) < math.pi:
            self._ultrasonic_fov = float(msg.field_of_view)
        lidar_fresh = self._is_lidar_fresh()
        decision = self.filter.update(
            float(msg.range),
            min_range_m=float(msg.min_range),
            max_range_m=float(msg.max_range),
            lidar_range_m=self._latest_lidar_range,
            lidar_fresh=lidar_fresh,
        )
        self._set_decision(decision)
        if decision.output_range_m is not None:
            output = Range()
            output.header = msg.header
            output.radiation_type = msg.radiation_type
            output.field_of_view = msg.field_of_view
            output.min_range = msg.min_range
            output.max_range = msg.max_range
            output.range = float(decision.output_range_m)
            self.range_pub.publish(output)

    def _on_cmd(self, msg: Twist) -> None:
        output = Twist()
        output.linear.x = msg.linear.x
        output.linear.y = msg.linear.y
        output.linear.z = msg.linear.z
        output.angular.x = msg.angular.x
        output.angular.y = msg.angular.y
        output.angular.z = msg.angular.z

        limit = (
            self._decision.forward_speed_limit_mps if self.enabled else None)
        output.linear.x = limit_forward_velocity(output.linear.x, limit)
        self.cmd_pub.publish(output)

    def _is_lidar_fresh(self) -> bool:
        if self._latest_lidar_at_ns <= 0:
            return False
        age_ns = self.get_clock().now().nanoseconds - self._latest_lidar_at_ns
        return age_ns <= int(self.scan_stale_sec * 1_000_000_000)

    def _check_stale(self) -> None:
        if not self.enabled:
            return
        now_ns = self.get_clock().now().nanoseconds
        if (
                self._latest_range_at_ns <= 0
                or now_ns - self._latest_range_at_ns
                > int(self.range_stale_sec * 1_000_000_000)):
            self._set_decision(
                self.filter.stale_decision(self._latest_lidar_range))
        elif not self._is_lidar_fresh():
            self._publish_state('STALE_LIDAR')

    def _set_decision(self, decision: FusionDecision) -> None:
        self._decision = decision
        self._publish_state(decision.state)

    def _publish_state(self, state: str) -> None:
        if state == self._last_published_state:
            return
        previous = self._last_published_state
        self._last_published_state = state
        self.state_pub.publish(String(data=state))
        if state in ('FORWARD_BLOCKED', 'STALE_RANGE', 'STALE_LIDAR'):
            self.get_logger().warn(f'저상 장애물 상태: {previous or "-"} → {state}')
        else:
            self.get_logger().info(f'저상 장애물 상태: {previous or "-"} → {state}')


def main(args=None) -> None:
    rclpy.init(args=args)
    node = LowObstacleSupervisor()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
