#!/usr/bin/env python3
"""Start the rear V4L2 capture only while guidance or preview needs it."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time

from ament_index_python.packages import get_package_share_directory
from mingky_interfaces.msg import GuideState
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool


CAMERA_PROFILES = {
    'pinky-01': 'pinky_6294',
    'pinky-02': 'pinky_15e2',
}


def rear_camera_needed(session_state: str, preview_active: bool) -> bool:
    """Rear camera is needed for patient tracking or an explicit viewer."""
    return preview_active or session_state == GuideState.SESSION_GUIDING


class CameraPowerManager(Node):
    """Own the rear camera process without stopping its MJPEG endpoint."""

    def __init__(self) -> None:
        super().__init__('camera_power_manager')
        self.declare_parameter('robot_id', 'pinky-01')
        self.declare_parameter('camera_profile', '')
        self.declare_parameter('idle_timeout_seconds', 15.0)
        self.declare_parameter('restart_backoff_seconds', 5.0)
        self.robot_id = str(self.get_parameter('robot_id').value)
        profile = str(self.get_parameter('camera_profile').value).strip()
        self._profile = profile or CAMERA_PROFILES.get(self.robot_id, '')
        self._idle_timeout = max(
            0.0, float(self.get_parameter('idle_timeout_seconds').value))
        self._restart_backoff = max(
            1.0, float(self.get_parameter('restart_backoff_seconds').value))
        self._session_state = GuideState.SESSION_NONE
        self._preview_active = False
        self._last_demand_at = time.monotonic()
        self._camera: subprocess.Popen | None = None
        self._last_start_attempt = float('-inf')
        self._active: bool | None = None

        qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(
            GuideState, '/guide_manager/state', self._on_guide_state, qos)
        self.create_subscription(
            Bool, '/rear_camera/preview_active', self._on_preview, qos)
        self._active_pub = self.create_publisher(
            Bool, '/rear_camera/power_active', qos)
        self.create_timer(0.5, self._reconcile)
        self._publish_active(False)
        self.get_logger().info(
            f'후방 카메라 절전 관리자 시작 (idle_timeout={self._idle_timeout:.1f}s)')

    def _on_guide_state(self, msg: GuideState) -> None:
        if msg.robot_id != self.robot_id:
            return
        self._session_state = msg.session_state
        self._reconcile()

    def _on_preview(self, msg: Bool) -> None:
        self._preview_active = bool(msg.data)
        self._reconcile()

    def _reconcile(self) -> None:
        now = time.monotonic()
        if self._camera is not None and self._camera.poll() is not None:
            self.get_logger().warning('후방 카메라 프로세스 종료 감지')
            self._camera = None
            self._publish_active(False)
        demanded = rear_camera_needed(
            self._session_state, self._preview_active)
        if demanded:
            self._last_demand_at = now
            if (self._camera is None
                    and now - self._last_start_attempt >= self._restart_backoff):
                self._start_camera()
            return
        if (self._camera is not None and self._camera.poll() is None
                and now - self._last_demand_at >= self._idle_timeout):
            self._stop_camera()

    def _camera_info_url(self) -> str:
        if not self._profile:
            return ''
        path = (
            Path(get_package_share_directory('mingky_bringup'))
            / 'config' / 'camera' / self._profile / 'rear_camera.yaml'
        )
        return path.as_uri() if path.is_file() else ''

    def _start_camera(self) -> None:
        self._last_start_attempt = time.monotonic()
        command = [
            'ros2', 'launch', 'mingky_bringup', 'rear_camera.launch.py',
            f'camera_info_url:={self._camera_info_url()}',
        ]
        try:
            self._camera = subprocess.Popen(command, start_new_session=True)
        except OSError as exc:
            self.get_logger().error(f'후방 카메라 시작 실패: {exc}')
            self._camera = None
            self._publish_active(False)
            return
        self._publish_active(True)
        self.get_logger().info('후방 카메라 절전 해제')

    def _stop_camera(self) -> None:
        process = self._camera
        self._camera = None
        if process is None or process.poll() is not None:
            self._publish_active(False)
            return
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2.0)
        except ProcessLookupError:
            pass
        self._publish_active(False)
        self.get_logger().info('후방 카메라 절전 진입')

    def _publish_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._active_pub.publish(Bool(data=active))

    def destroy_node(self):
        self._stop_camera()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = CameraPowerManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
