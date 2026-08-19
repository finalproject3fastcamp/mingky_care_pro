#!/usr/bin/env python3
"""
초음파로 낮은 장애물을 감지하면: 정지 -> 좌우 살펴보기 -> 열린 쪽으로 옆걸음 ->
원래 목표 재전송. 코스트맵 전역 재계획에 기대지 않고, Nav2 액션(Spin,
DriveOnHeading, NavigateToPose)만으로 직접 조작한다.

일회성 라이브 테스트용 스크립트. 패키지 빌드 없이 바로 실행한다:
  python3 sidestep_node.py --ros-args \
    -p target_x:=0.9 -p target_y:=1.0 -p target_yaw_deg:=90.0
"""
import math
import sys
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy

from action_msgs.msg import GoalStatus
from builtin_interfaces.msg import Duration
from geometry_msgs.msg import Point, PoseStamped
from nav2_msgs.action import DriveOnHeading, NavigateToPose, Spin
from sensor_msgs.msg import LaserScan, Range


def yaw_to_quat(yaw):
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


class SidestepNode(Node):
    def __init__(self):
        super().__init__('low_obstacle_sidestep')

        self.declare_parameter('target_x', 0.0)
        self.declare_parameter('target_y', 0.0)
        self.declare_parameter('target_yaw_deg', 0.0)
        self.declare_parameter('trigger_distance', 0.25)
        self.declare_parameter('side_scan_angle_deg', 40.0)
        self.declare_parameter('sidestep_forward_m', 0.35)
        self.declare_parameter('max_sidesteps', 3)
        self.declare_parameter('lidar_margin', 0.15)
        self.declare_parameter('lidar_half_width_deg', 15.0)

        self.target_x = self.get_parameter('target_x').value
        self.target_y = self.get_parameter('target_y').value
        self.target_yaw = math.radians(self.get_parameter('target_yaw_deg').value)
        self.trigger_distance = self.get_parameter('trigger_distance').value
        self.side_scan_angle = math.radians(self.get_parameter('side_scan_angle_deg').value)
        self.sidestep_forward_m = self.get_parameter('sidestep_forward_m').value
        self.max_sidesteps = self.get_parameter('max_sidesteps').value
        self.lidar_margin = self.get_parameter('lidar_margin').value
        self.lidar_half_width_deg = self.get_parameter('lidar_half_width_deg').value

        qos = QoSProfile(depth=5)
        qos.reliability = QoSReliabilityPolicy.BEST_EFFORT
        self.latest_range = None
        self.create_subscription(Range, '/us_sensor/range', self._range_cb, qos)

        self.latest_scan = None
        self.create_subscription(LaserScan, '/scan', self._scan_cb, qos)

        self.nav_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.spin_client = ActionClient(self, Spin, 'spin')
        self.drive_client = ActionClient(self, DriveOnHeading, 'drive_on_heading')

    def _range_cb(self, msg):
        self.latest_range = msg.range

    def _scan_cb(self, msg):
        self.latest_scan = msg

    def lidar_front_min_range(self):
        """rplidar_link 이 base_link 대비 요(yaw) 180도 돌아 붙어있어서, 로봇
        정면(base_link +x)은 스캔 배열의 각도 0 이 아니라 +-pi 근처, 즉 배열의
        양쪽 끝(인덱스 0 부근과 마지막 부근)에 해당한다."""
        scan = self.latest_scan
        if scan is None or not scan.ranges:
            return None
        n = max(1, int(math.radians(self.lidar_half_width_deg) / scan.angle_increment))
        ranges = scan.ranges
        sector = list(ranges[:n + 1]) + list(ranges[-n:])
        valid = [r for r in sector if scan.range_min <= r <= scan.range_max]
        if not valid:
            return None
        return min(valid)

    def spin_until(self, fut, timeout_sec=None):
        rclpy.spin_until_future_complete(self, fut, timeout_sec=timeout_sec)
        return fut.result()

    def make_pose(self, x, y, yaw):
        p = PoseStamped()
        p.header.frame_id = 'map'
        p.header.stamp = self.get_clock().now().to_msg()
        p.pose.position.x = x
        p.pose.position.y = y
        qx, qy, qz, qw = yaw_to_quat(yaw)
        p.pose.orientation.x = qx
        p.pose.orientation.y = qy
        p.pose.orientation.z = qz
        p.pose.orientation.w = qw
        return p

    def do_spin(self, delta_yaw, time_allowance_sec=8.0):
        self.spin_client.wait_for_server()
        goal = Spin.Goal()
        goal.target_yaw = float(delta_yaw)
        goal.time_allowance = Duration(sec=int(time_allowance_sec))
        self.get_logger().info(f'[spin] delta_yaw={math.degrees(delta_yaw):.1f}deg')
        gh = self.spin_until(self.spin_client.send_goal_async(goal))
        if not gh.accepted:
            self.get_logger().warn('[spin] goal rejected')
            return False
        result = self.spin_until(gh.get_result_async())
        return result.status == GoalStatus.STATUS_SUCCEEDED

    def do_drive(self, distance_m, speed=0.12, time_allowance_sec=10.0):
        self.drive_client.wait_for_server()
        goal = DriveOnHeading.Goal()
        goal.target = Point(x=float(distance_m), y=0.0, z=0.0)
        goal.speed = float(speed)
        goal.time_allowance = Duration(sec=int(time_allowance_sec))
        self.get_logger().info(f'[drive] distance={distance_m:.2f}m speed={speed}')
        gh = self.spin_until(self.drive_client.send_goal_async(goal))
        if not gh.accepted:
            self.get_logger().warn('[drive] goal rejected')
            return False
        result = self.spin_until(gh.get_result_async())
        return result.status == GoalStatus.STATUS_SUCCEEDED

    def read_range_fresh(self, wait_sec=0.6):
        self.latest_range = None
        t0 = time.time()
        while self.latest_range is None and time.time() - t0 < wait_sec:
            rclpy.spin_once(self, timeout_sec=0.05)
        return self.latest_range

    def probe_direction(self, sign, step_deg, max_steps, min_safe):
        """sign 방향으로 step_deg씩 나눠 돌면서 매 스텝마다 초음파를 다시 읽는다.
        min_safe 이상이 나오면 그 자리에서 바로 멈추고 반환한다."""
        cumulative_deg = 0.0
        last_r = None
        for i in range(max_steps):
            self.do_spin(sign * math.radians(step_deg))
            cumulative_deg += step_deg
            last_r = self.read_range_fresh()
            side = 'L' if sign > 0 else 'R'
            self.get_logger().info(
                f'  probe {side} step{i + 1} cum={cumulative_deg:.0f}deg r={last_r}'
            )
            if last_r is not None and last_r >= min_safe:
                return cumulative_deg, last_r
        return cumulative_deg, last_r

    def creep_forward(self, drive_step, max_total, min_safe_drive):
        """조금씩 전진하며 매 스텝 초음파를 재확인한다. 절대 위험거리
        아래로 떨어질 때만 멈춘다 (스쳐가는 다른 사물 때문에 값이 자연히
        줄어드는 것까지 위험으로 보지 않는다)."""
        traveled = 0.0
        while traveled < max_total:
            self.do_drive(drive_step)
            traveled += drive_step
            r = self.read_range_fresh()
            self.get_logger().info(f'  creep traveled={traveled:.2f}m r={r}')
            if r is not None and r < min_safe_drive:
                self.get_logger().warn(f'too close (r={r}), stopping creep early')
                break
        return traveled

    def sidestep_once(self, preferred_sign=None):
        """preferred_sign 이 주어지면 좌우를 새로 재지 않고 그 방향을 그대로
        유지한다 (매번 방향이 왔다갔다 하며 이전 이동을 까먹는 것을 방지)."""
        self.get_logger().info('=== obstacle detected: stop -> incremental probe -> creep ===')

        STEP_DEG = 10.0
        MAX_STEPS = 4
        MIN_SAFE_PROBE = 0.45
        MIN_SAFE_DRIVE = 0.10
        DRIVE_STEP = 0.08
        MAX_DRIVE_TOTAL = self.sidestep_forward_m
        # 센서는 정면만 본다. "이 방향 열림"을 확인한 각도에서 이만큼 더
        # 돌아야 로봇 몸통(폭)이 원래 장애물 옆면을 완전히 벗어난다고 본다.
        BODY_CLEARANCE_MARGIN_DEG = 15.0

        if preferred_sign is not None:
            sign = preferred_sign
            self.get_logger().info(f'keeping previously chosen side: {"LEFT" if sign > 0 else "RIGHT"}')
        else:
            self.do_spin(math.radians(STEP_DEG))
            left_r = self.read_range_fresh()
            self.do_spin(-math.radians(STEP_DEG))
            self.do_spin(-math.radians(STEP_DEG))
            right_r = self.read_range_fresh()
            self.do_spin(math.radians(STEP_DEG))

            left_v = left_r if left_r is not None else -1.0
            right_v = right_r if right_r is not None else -1.0
            sign = 1.0 if left_v >= right_v else -1.0
            self.get_logger().info(
                f'initial look: left={left_r} right={right_r} -> trying '
                f'{"LEFT" if sign > 0 else "RIGHT"} first'
            )

        cum_deg, probe_r = self.probe_direction(sign, STEP_DEG, MAX_STEPS, MIN_SAFE_PROBE)

        if probe_r is None or probe_r < MIN_SAFE_PROBE:
            self.do_spin(-sign * math.radians(cum_deg))
            sign = -sign
            self.get_logger().info(
                f'chosen side blocked, trying {"LEFT" if sign > 0 else "RIGHT"}'
            )
            cum_deg, probe_r = self.probe_direction(sign, STEP_DEG, MAX_STEPS, MIN_SAFE_PROBE)
            if probe_r is None or probe_r < MIN_SAFE_PROBE:
                self.get_logger().warn(
                    'neither side clear enough, backing to center and giving up this attempt'
                )
                self.do_spin(-sign * math.radians(cum_deg))
                return False, None

        self.get_logger().info(
            f'clear heading found at {cum_deg:.0f}deg, adding '
            f'{BODY_CLEARANCE_MARGIN_DEG:.0f}deg body-clearance margin before creeping'
        )
        self.do_spin(sign * math.radians(BODY_CLEARANCE_MARGIN_DEG))
        cum_deg += BODY_CLEARANCE_MARGIN_DEG

        traveled = self.creep_forward(DRIVE_STEP, MAX_DRIVE_TOTAL, MIN_SAFE_DRIVE)
        self.do_spin(-sign * math.radians(cum_deg))
        self.get_logger().info(
            f'=== sidestep complete: turned {cum_deg:.0f}deg, advanced {traveled:.2f}m ==='
        )
        return True, sign

    def run(self):
        self.nav_client.wait_for_server()
        pose = self.make_pose(self.target_x, self.target_y, self.target_yaw)

        sidesteps_done = 0
        committed_sign = None
        while True:
            self.get_logger().info(
                f'sending NavigateToPose goal x={self.target_x:.2f} y={self.target_y:.2f}'
            )
            goal_msg = NavigateToPose.Goal()
            goal_msg.pose = pose
            gh = self.spin_until(self.nav_client.send_goal_async(goal_msg))
            if not gh.accepted:
                self.get_logger().error('goal rejected by server')
                return

            result_fut = gh.get_result_async()
            triggered = False
            while not result_fut.done():
                rclpy.spin_once(self, timeout_sec=0.1)
                r = self.latest_range
                if r is None or r >= self.trigger_distance:
                    continue

                lidar_r = self.lidar_front_min_range()
                lidar_sees_it = (
                    lidar_r is not None and lidar_r < self.trigger_distance + self.lidar_margin
                )
                if lidar_sees_it:
                    # 라이다도 같은 방향에서 잡고 있다 = 낮은 장애물이 아니라
                    # 일반 높이 장애물. Nav2 코스트맵/전역계획에 맡기고
                    # 우리 로직은 끼어들지 않는다.
                    continue

                if sidesteps_done >= self.max_sidesteps:
                    self.get_logger().warn('max sidesteps reached, cancelling and giving up')
                    cancel_fut = gh.cancel_goal_async()
                    self.spin_until(cancel_fut)
                    return
                self.get_logger().info(
                    f'ultrasonic range={r:.2f}m < trigger, lidar sees nothing there '
                    f'(lidar={lidar_r}) -> low obstacle, cancelling goal'
                )
                cancel_fut = gh.cancel_goal_async()
                self.spin_until(cancel_fut)
                triggered = True
                break

            if triggered:
                _, used_sign = self.sidestep_once(preferred_sign=committed_sign)
                if used_sign is not None:
                    committed_sign = used_sign
                sidesteps_done += 1
                continue

            result = result_fut.result()
            status = result.status
            self.get_logger().info(f'NavigateToPose finished with status={status}')
            return


def main():
    rclpy.init()
    node = SidestepNode()
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
