#!/usr/bin/env python3
"""Fuse front ultrasonic/LiDAR data and limit only unsafe forward motion."""

from __future__ import annotations

import json
import math

from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from mingky_interfaces.msg import GuideState
from nav2_msgs.srv import ClearEntireCostmap
from nav_msgs.msg import Odometry
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
from std_msgs.msg import Bool, String
from tf2_ros import Buffer, TransformException, TransformListener

from .fusion import (
    CostmapObservationRetention,
    FusionConfig,
    FusionDecision,
    guide_navigation_segment_active,
    limit_forward_velocity,
    LowObstacleFilter,
    matching_observation_cluster,
    NavigationScope,
    nearest_lidar_in_ultrasonic_cones,
    ObstacleCone,
    observation_overlap_estimate,
    observation_pose_distinct,
    observation_pose_expired,
    retained_cone_speed_limit,
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
            'observation_topic': '/low_obstacle/observation',
            'cmd_vel_input_topic': 'cmd_vel_low_obstacle_input',
            'cmd_vel_output_topic': 'cmd_vel_safety_input',
            'guide_state_topic': '/guide_manager/state',
            'waypoint_test_active_topic': '/navigation_manager/active',
            'scan_stale_sec': 0.5,
            'range_stale_sec': 0.5,
            'transform_timeout_sec': 0.05,
            # 초음파 전체 빔보다 좁은 정면 LiDAR 영역만 장애물 확정에
            # 쓴다. 그 밖 90도 영역의 가까운 벽은 코너 반사 억제에 쓴다.
            'lidar_confirmation_fov_rad': 0.2617993877991494,
            'lidar_wall_context_fov_rad': 1.5707963267948966,
            # 1Hz 전역 costmap이 우회 경로를 만들 시간을 확보하도록 40cm에서
            # 먼저 표식하고 25cm부터 감속한다. 센서 10Hz/2-of-3 확인과 LiDAR
            # 불일치 검증은 그대로라 계산 부하와 벽 반사 오판은 늘리지 않는다.
            'detect_distance_m': 0.40,
            'clear_distance_m': 0.45,
            'slow_distance_m': 0.25,
            'stop_distance_m': 0.04,
            # 9.5cm padded footprint + 10cm local inflation 바깥에 endpoint를
            # 두어 시작 자세가 장애물 안에 갇히지 않게 한다.
            'costmap_min_range_m': 0.20,
            'slow_speed_mps': 0.08,
            'lidar_margin_m': 0.15,
            'side_wall_guard_distance_m': 0.22,
            'side_wall_reflection_max_range_m': 0.10,
            'median_samples': 3,
            'confirmation_window': 3,
            'confirmations_required': 2,
            'clear_confirmations': 2,
            'near_window': 3,
            'near_confirmations': 2,
            # 로봇 상대 센서 관측은 회피 이동 뒤 같은 지도 좌표에 남기지 않는다.
            'observation_expiry_distance_m': 0.10,
            'observation_expiry_yaw_rad': 0.3490658503988659,
            # 전역 costmap(1 Hz)이 적어도 한 번은 안정된 장애물을 보고
            # 우회 경로를 만들 수 있도록 즉시 삭제하지 않는다.
            'observation_clear_hold_sec': 1.5,
            # 같은 장애물을 다른 자세에서 본 관측은 하나로 군집화해
            # RangeSensorLayer에 로봇 진행 궤적 모양의 표식이 남지 않게 한다.
            'observation_merge_distance_m': 0.12,
            # URDF ultrasonic_link x와 padded footprint 폭을 사용해, 센서가
            # 잠깐 놓친 보존 장애물이 실제 전방 주행 폭 안에 있으면 계속
            # 감속/정지한다. 회전해 옆으로 비키면 자동으로 제한이 풀린다.
            'ultrasonic_mount_x_m': 0.0267,
            'retained_corridor_half_width_m': 0.08,
        }
        for name, default in parameters.items():
            self.declare_parameter(name, default)
        get = self.get_parameter

        self.enabled = bool(get('enabled').value)
        self.scan_stale_sec = max(0.1, float(get('scan_stale_sec').value))
        self.range_stale_sec = max(0.1, float(get('range_stale_sec').value))
        self.transform_timeout = Duration(
            seconds=max(0.0, float(get('transform_timeout_sec').value)))
        self.lidar_confirmation_fov = min(
            math.pi - 1e-3,
            max(0.05, float(get('lidar_confirmation_fov_rad').value)))
        self.lidar_wall_context_fov = min(
            math.pi - 1e-3,
            max(0.26, float(get('lidar_wall_context_fov_rad').value)))
        self.observation_expiry_distance = max(
            0.01, float(get('observation_expiry_distance_m').value))
        self.observation_expiry_yaw = max(
            0.05, float(get('observation_expiry_yaw_rad').value))
        self.observation_retention = CostmapObservationRetention(
            float(get('observation_clear_hold_sec').value))
        self.observation_merge_distance = max(
            0.02, float(get('observation_merge_distance_m').value))
        self.ultrasonic_mount_x = max(
            0.0, float(get('ultrasonic_mount_x_m').value))
        self.retained_corridor_half_width = max(
            0.01, float(get('retained_corridor_half_width_m').value))
        self.navigation_scope = NavigationScope()
        self.filter = LowObstacleFilter(FusionConfig(
            detect_distance_m=float(get('detect_distance_m').value),
            clear_distance_m=float(get('clear_distance_m').value),
            slow_distance_m=float(get('slow_distance_m').value),
            stop_distance_m=float(get('stop_distance_m').value),
            costmap_min_range_m=float(get('costmap_min_range_m').value),
            slow_speed_mps=float(get('slow_speed_mps').value),
            lidar_margin_m=float(get('lidar_margin_m').value),
            side_wall_guard_distance_m=float(
                get('side_wall_guard_distance_m').value),
            side_wall_reflection_max_range_m=float(
                get('side_wall_reflection_max_range_m').value),
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
        self._latest_wall_context_range: float | None = None
        self._latest_lidar_at_ns = 0
        self._latest_range_at_ns = 0
        self._ultrasonic_frame = 'ultrasonic_link'
        self._ultrasonic_fov = 0.26
        self._decision = self.filter.stale_decision()
        self._last_published_state = ''
        self._latest_odom_pose: tuple[float, float, float] | None = None
        self._latest_map_pose: tuple[float, float, float] | None = None
        self._mark_anchor_pose: tuple[float, float, float] | None = None
        self._marked_observations: list[Range] = []
        self._marked_observation_poses: list[
            tuple[float, float, float] | None] = []
        self._marked_observation_endpoints: list[
            tuple[float, float] | None] = []
        self._marked_observation_map_endpoints: list[
            tuple[float, float] | None] = []
        self._marked_observation_odom_cones: list[list[ObstacleCone]] = []
        self._marked_observation_map_cones: list[list[ObstacleCone]] = []
        self._marked_observation_odom_estimates: list[
            tuple[float, float, float] | None] = []
        self._marked_observation_map_estimates: list[
            tuple[float, float, float] | None] = []
        self._last_active_observation_distance: float | None = None

        latched = QoSProfile(
            depth=1,
            history=QoSHistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        filtered_range_qos = QoSProfile(
            depth=32,
            history=QoSHistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.VOLATILE,
        )
        self.range_pub = self.create_publisher(
            Range, str(get('filtered_range_topic').value), filtered_range_qos)
        self.state_pub = self.create_publisher(
            String, str(get('state_topic').value), latched)
        self.observation_pub = self.create_publisher(
            String, str(get('observation_topic').value), latched)
        self.cmd_pub = self.create_publisher(
            Twist, str(get('cmd_vel_output_topic').value), 10)
        self.local_costmap_clear = self.create_client(
            ClearEntireCostmap,
            '/local_costmap/clear_entirely_local_costmap')
        self.global_costmap_clear = self.create_client(
            ClearEntireCostmap,
            '/global_costmap/clear_entirely_global_costmap')
        self.create_subscription(
            Range, str(get('range_topic').value), self._on_range,
            qos_profile_sensor_data)
        self.create_subscription(
            LaserScan, str(get('scan_topic').value), self._on_scan,
            qos_profile_sensor_data)
        self.create_subscription(
            Odometry, '/odom', self._on_odom, qos_profile_sensor_data)
        self.create_subscription(
            PoseWithCovarianceStamped,
            '/amcl_pose',
            self._on_map_pose,
            qos_profile_sensor_data,
        )
        self.create_subscription(
            Twist, str(get('cmd_vel_input_topic').value), self._on_cmd, 10)
        self.create_subscription(
            GuideState,
            str(get('guide_state_topic').value),
            self._on_guide_state,
            latched,
        )
        self.create_subscription(
            Bool,
            str(get('waypoint_test_active_topic').value),
            self._on_waypoint_test_active,
            latched,
        )
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
        common = {
            'ranges': msg.ranges,
            'angle_min': float(msg.angle_min),
            'angle_increment': float(msg.angle_increment),
            'range_min': float(msg.range_min),
            'range_max': float(msg.range_max),
            'scan_to_ultrasonic_x': float(translation.x),
            'scan_to_ultrasonic_y': float(translation.y),
            'scan_to_ultrasonic_yaw': yaw,
        }
        # 정면 방향 일치도를 높이기 위해 센서 명목 FOV와 15도 중
        # 더 좁은 범위만 저상 장애물 확정에 사용한다.
        lidar_range, wall_context_range = nearest_lidar_in_ultrasonic_cones(
            **common,
            ultrasonic_fovs=(
                min(self._ultrasonic_fov, self.lidar_confirmation_fov),
                max(self._ultrasonic_fov, self.lidar_wall_context_fov),
            ),
        )
        if lidar_range is not None:
            self._latest_lidar_range = lidar_range
            self._latest_lidar_at_ns = self.get_clock().now().nanoseconds
        self._latest_wall_context_range = wall_context_range

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
            wall_context_range_m=self._latest_wall_context_range,
        )
        self.observation_retention.on_detection(
            decision.low_obstacle_confirmed)
        costmap_marking_enabled = (
            self.navigation_scope.active
            and not self.observation_retention.suppress_until_sensor_clear)
        if decision.output_range_m is not None:
            output = Range()
            output.header.stamp = msg.header.stamp
            output.header.frame_id = msg.header.frame_id
            output.radiation_type = msg.radiation_type
            output.field_of_view = msg.field_of_view
            output.min_range = msg.min_range
            output.max_range = msg.max_range
            output.range = float(decision.output_range_m)
            if math.isclose(
                    output.range, output.max_range,
                    rel_tol=0.0, abs_tol=1e-6):
                if (
                        self._marked_observations
                        and not self.navigation_scope.active):
                    self.observation_retention.request_clear(
                        self.get_clock().now().nanoseconds,
                        '센서 해제 확인')
                elif not self._marked_observations:
                    # Preserve the normal RangeSensorLayer clearing stream and
                    # also clean a cone that may predate this node process.
                    self.range_pub.publish(output)
            elif costmap_marking_enabled:
                # RangeSensorLayer uses volatile sensor QoS. Publish throughout
                # confirmation so a costmap activated after this node also
                # receives the obstacle instead of missing a one-shot mark.
                for clear in self._remember_marked_observation(
                        output, measured_range_m=decision.filtered_range_m):
                    self.range_pub.publish(clear)
                self.range_pub.publish(output)
        self._set_decision(decision)

    def _on_guide_state(self, msg: GuideState) -> None:
        active = guide_navigation_segment_active(
            msg.session_state,
            msg.robot_state,
            bool(msg.returning_to_dock),
        )
        self._update_navigation_scope('guidance', active)

    def _on_waypoint_test_active(self, msg: Bool) -> None:
        self._update_navigation_scope('waypoint_test', bool(msg.data))

    def _update_navigation_scope(self, source: str, active: bool) -> None:
        transition = self.navigation_scope.update(source, active)
        if transition == 'started':
            self.observation_retention.cancel_pending_clear()
            self.observation_retention.suppress_until_sensor_clear = False
            self.get_logger().info(
                '저상 장애물 costmap 보존 시작: 현재 주행 작업이 끝날 때까지 유지')
        elif transition == 'finished':
            self._clear_navigation_observations('주행 작업 종료')

    def _clear_navigation_observations(self, reason: str) -> None:
        had_observations = bool(self._marked_observations)
        self._clear_marked_observations(reason)
        self.observation_retention.mark_cleared()
        self.observation_retention.suppress_until_sensor_clear = False
        if had_observations:
            # RangeSensorLayer의 오래된 TF 시각은 개별 max-range 메시지만으로
            # 지워지지 않을 수 있어, 실제 표식이 있었던 작업만 전체 초기화한다.
            self._request_costmap_clear(self.local_costmap_clear, 'local')
            self._request_costmap_clear(self.global_costmap_clear, 'global')
        self._publish_observation(self._decision, force_active=False)

    def _request_costmap_clear(self, client, label: str) -> None:
        if not client.service_is_ready():
            self.get_logger().warn(
                f'{label} costmap 초기화 서비스를 사용할 수 없습니다.',
                throttle_duration_sec=2.0)
            return
        future = client.call_async(ClearEntireCostmap.Request())
        future.add_done_callback(
            lambda done, name=label: self._on_costmap_cleared(done, name))

    def _on_costmap_cleared(self, future, label: str) -> None:
        try:
            future.result()
        except Exception as exc:  # noqa: BLE001 - 서비스 실패는 주행을 막지 않는다.
            self.get_logger().warn(f'{label} costmap 초기화 실패: {exc}')
            return
        self.get_logger().info(f'{label} costmap 저상 장애물 표식 초기화 완료')

    def _on_odom(self, msg: Odometry) -> None:
        orientation = msg.pose.pose.orientation
        yaw = math.atan2(
            2.0 * (
                orientation.w * orientation.z
                + orientation.x * orientation.y),
            1.0 - 2.0 * (
                orientation.y * orientation.y
                + orientation.z * orientation.z),
        )
        position = msg.pose.pose.position
        self._latest_odom_pose = (float(position.x), float(position.y), yaw)
        if self._mark_anchor_pose is None and self._marked_observations:
            self._mark_anchor_pose = self._latest_odom_pose
            return
        if (
                not self.navigation_scope.active
                and self._mark_anchor_pose is not None
                and observation_pose_expired(
                    self._mark_anchor_pose,
                    self._latest_odom_pose,
                    distance_m=self.observation_expiry_distance,
                    yaw_rad=self.observation_expiry_yaw,
                    preserve_forward_approach=(
                        self._decision.low_obstacle_confirmed))):
            self.observation_retention.request_clear(
                self.get_clock().now().nanoseconds,
                '회피 이동 후 과거 관측 만료',
                suppress_until_sensor_clear=True)

    def _on_map_pose(self, msg: PoseWithCovarianceStamped) -> None:
        """Keep a lightweight map-frame pose for fixed 3D obstacle markers."""
        orientation = msg.pose.pose.orientation
        yaw = math.atan2(
            2.0 * (
                orientation.w * orientation.z
                + orientation.x * orientation.y),
            1.0 - 2.0 * (
                orientation.y * orientation.y
                + orientation.z * orientation.z),
        )
        position = msg.pose.pose.position
        self._latest_map_pose = (float(position.x), float(position.y), yaw)

    @staticmethod
    def _clear_message(marked: Range) -> Range:
        clear = Range()
        clear.header.stamp = marked.header.stamp
        clear.header.frame_id = marked.header.frame_id
        clear.radiation_type = marked.radiation_type
        clear.field_of_view = marked.field_of_view
        clear.min_range = marked.min_range
        clear.max_range = marked.max_range
        clear.range = marked.max_range
        return clear

    @staticmethod
    def _observation_endpoint(
            distance_m: float,
            pose: tuple[float, float, float] | None,
    ) -> tuple[float, float] | None:
        if pose is None or not math.isfinite(float(distance_m)):
            return None
        return (
            pose[0] + float(distance_m) * math.cos(pose[2]),
            pose[1] + float(distance_m) * math.sin(pose[2]),
        )

    def _observation_cone(
            self, distance_m: float,
            pose: tuple[float, float, float] | None,
    ) -> ObstacleCone | None:
        if pose is None or not math.isfinite(float(distance_m)):
            return None
        return ObstacleCone(
            origin_x=pose[0] + self.ultrasonic_mount_x * math.cos(pose[2]),
            origin_y=pose[1] + self.ultrasonic_mount_x * math.sin(pose[2]),
            yaw=pose[2],
            range_m=float(distance_m),
            fov_rad=float(self._ultrasonic_fov),
        )

    @staticmethod
    def _remember_cone(
            cones: list[ObstacleCone], cone: ObstacleCone | None) -> bool:
        """Keep at most three observations; report meaningful geometry change."""
        if cone is None:
            return False
        if cones and not observation_pose_distinct(cones[-1], cone):
            changed = abs(cones[-1].range_m - cone.range_m) >= 0.015
            cones[-1] = cone
            return changed
        cones.append(cone)
        del cones[:-3]
        return True

    def _remember_marked_observation(
            self, msg: Range, *, measured_range_m: float | None) -> list[Range]:
        """Store one latest Range cone per spatial obstacle cluster.

        RangeSensorLayer otherwise retains every robot-relative cone. When the
        robot approaches one object, those cones form a long artificial wall.
        Clear the previous cone before replacing it with a nearby observation,
        while keeping distinct obstacle clusters for the whole navigation task.
        """
        if self._mark_anchor_pose is None:
            self._mark_anchor_pose = self._latest_odom_pose
        stored = Range()
        stored.header.stamp = msg.header.stamp
        stored.header.frame_id = msg.header.frame_id
        stored.radiation_type = msg.radiation_type
        stored.field_of_view = msg.field_of_view
        stored.min_range = msg.min_range
        stored.max_range = msg.max_range
        stored.range = msg.range
        pose = self._latest_odom_pose
        measured_range = (
            float(measured_range_m)
            if measured_range_m is not None else float(stored.range))
        endpoint_distance = measured_range + self.ultrasonic_mount_x
        endpoint = self._observation_endpoint(endpoint_distance, pose)
        map_endpoint = self._observation_endpoint(
            endpoint_distance, self._latest_map_pose)
        odom_cone = self._observation_cone(measured_range, pose)
        map_cone = self._observation_cone(
            measured_range, self._latest_map_pose)
        index = matching_observation_cluster(
            self._marked_observation_endpoints,
            endpoint,
            merge_distance_m=self.observation_merge_distance,
        )
        if index is not None:
            clear = self._clear_message(self._marked_observations[index])
            self._marked_observations[index] = stored
            self._marked_observation_poses[index] = pose
            self._marked_observation_endpoints[index] = endpoint
            if map_endpoint is not None:
                self._marked_observation_map_endpoints[index] = map_endpoint
            if self._remember_cone(
                    self._marked_observation_odom_cones[index], odom_cone):
                self._marked_observation_odom_estimates[index] = (
                    observation_overlap_estimate(
                        self._marked_observation_odom_cones[index]))
            if self._remember_cone(
                    self._marked_observation_map_cones[index], map_cone):
                self._marked_observation_map_estimates[index] = (
                    observation_overlap_estimate(
                        self._marked_observation_map_cones[index]))
            return [clear]
        self._marked_observations.append(stored)
        self._marked_observation_poses.append(pose)
        self._marked_observation_endpoints.append(endpoint)
        self._marked_observation_map_endpoints.append(map_endpoint)
        self._marked_observation_odom_cones.append([])
        self._marked_observation_map_cones.append([])
        self._remember_cone(self._marked_observation_odom_cones[-1], odom_cone)
        self._remember_cone(self._marked_observation_map_cones[-1], map_cone)
        self._marked_observation_odom_estimates.append(None)
        self._marked_observation_map_estimates.append(None)
        # 종료 시 costmap 전체 초기화가 누락된 RangeSensorLayer 셀까지 지운다.
        # 여기서는 메모리만 제한하고, 서로 다른 장애물 군집은
        # 현재 주행 작업이 끝날 때까지 유지한다.
        if len(self._marked_observations) > 32:
            self._marked_observations.pop(0)
            self._marked_observation_poses.pop(0)
            self._marked_observation_endpoints.pop(0)
            self._marked_observation_map_endpoints.pop(0)
            self._marked_observation_odom_cones.pop(0)
            self._marked_observation_map_cones.pop(0)
            self._marked_observation_odom_estimates.pop(0)
            self._marked_observation_map_estimates.pop(0)
        return []

    def _clear_marked_observations(self, reason: str) -> None:
        if not self._marked_observations:
            self._mark_anchor_pose = None
            return
        for marked in self._marked_observations:
            self.range_pub.publish(self._clear_message(marked))
        count = len(self._marked_observations)
        self._marked_observations.clear()
        self._marked_observation_poses.clear()
        self._marked_observation_endpoints.clear()
        self._marked_observation_map_endpoints.clear()
        self._marked_observation_odom_cones.clear()
        self._marked_observation_map_cones.clear()
        self._marked_observation_odom_estimates.clear()
        self._marked_observation_map_estimates.clear()
        self._mark_anchor_pose = None
        self.get_logger().info(f'저상 장애물 과거 관측 {count}건 삭제: {reason}')

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
        retained_limit = retained_cone_speed_limit(
            self._latest_odom_pose,
            self._marked_observation_odom_cones,
            overlap_estimates=self._marked_observation_odom_estimates,
            sensor_offset_m=self.ultrasonic_mount_x,
            corridor_half_width_m=self.retained_corridor_half_width,
            slow_distance_m=self.filter.config.slow_distance_m,
            stop_distance_m=self.filter.config.stop_distance_m,
            slow_speed_mps=self.filter.config.slow_speed_mps,
        ) if self.enabled else None
        if retained_limit is not None:
            limit = retained_limit if limit is None else min(limit, retained_limit)
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
        if (
                self._marked_observations
                and self.observation_retention.clear_due(now_ns)):
            reason = self.observation_retention.reason or '관측 유지시간 만료'
            self._clear_marked_observations(reason)
            self.observation_retention.mark_cleared()
            self._publish_observation(self._decision, force_active=False)

    def _set_decision(self, decision: FusionDecision) -> None:
        self._decision = decision
        self._publish_state(decision.state)
        self._publish_observation(decision)

    def _publish_observation(
            self, decision: FusionDecision, *,
            force_active: bool | None = None) -> None:
        directly_observed = decision.state in (
            'CONFIRMED', 'SLOW', 'FORWARD_BLOCKED')
        if directly_observed and decision.filtered_range_m is not None:
            self._last_active_observation_distance = float(
                decision.filtered_range_m)
        active = directly_observed or (
            decision.state == 'UNCERTAIN'
            and decision.low_obstacle_confirmed
            and self._last_active_observation_distance is not None)
        if force_active is not None:
            active = force_active
            if not active:
                self._last_active_observation_distance = None
        distance = (
            float(decision.filtered_range_m)
            if directly_observed and decision.filtered_range_m is not None
            else self._last_active_observation_distance)
        if not decision.low_obstacle_confirmed and force_active is None:
            self._last_active_observation_distance = None
        payload = json.dumps({
            'active': active,
            # 현재 센서에서 잠깐 사라져도 이번 주행의 costmap에 보존된
            # 저상 장애물이 있으면 복구 후보 선택에는 계속 반영한다.
            'retained_active': bool(self._marked_observations),
            # 관제 3D 지도는 이번 주행에서 보존 중인 장애물을 map 좌표에
            # 고정해 표시한다. 주행이 끝나면 위 목록과 함께 빈 배열이 된다.
            'retained_obstacles': [
                {
                    'observations': [
                        {
                            'x': round(cone.origin_x, 3),
                            'y': round(cone.origin_y, 3),
                            'yaw': round(cone.yaw, 4),
                            'range_m': round(cone.range_m, 3),
                            'fov_rad': round(cone.fov_rad, 4),
                        }
                        for cone in cones
                    ],
                    'estimate': (
                        {
                            'x': round(estimate[0], 3),
                            'y': round(estimate[1], 3),
                            'radius_m': round(estimate[2], 3),
                        }
                        if estimate is not None else None
                    ),
                }
                for cones, estimate in zip(
                    self._marked_observation_map_cones,
                    self._marked_observation_map_estimates,
                )
                if cones
            ],
            'distance_m': (
                round(distance, 3)
                if active and distance is not None else None),
            'fov_rad': round(float(self._ultrasonic_fov), 4),
            'forward_confirmation_fov_rad': round(float(min(
                self._ultrasonic_fov,
                self.lidar_confirmation_fov,
            )), 4),
            # 90도 주변 LiDAR 최단 거리. 정면 15도가 비어 있고
            # 주변에만 가까운 벽이 있을 때 코너 반사 판정에 사용한다.
            'wall_context_m': (
                round(float(self._latest_wall_context_range), 3)
                if self._latest_wall_context_range is not None else None),
            'wall_reflection_likely': decision.wall_reflection_likely,
            'state': decision.state,
        }, ensure_ascii=False, separators=(',', ':'))
        self.observation_pub.publish(String(data=payload))

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
