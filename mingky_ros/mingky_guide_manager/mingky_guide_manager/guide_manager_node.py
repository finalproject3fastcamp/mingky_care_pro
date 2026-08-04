"""안내 상태를 소유하는 노드.

프로젝트에서 상태를 소유하는 노드는 이것 하나뿐이다.
다른 노드는 관측만 발행하고, 그 관측이 무엇을 뜻하는지는 여기서 판정한다.

예) 마커가 안 보인다는 관측은
      안내 중(moving)  이면  환자를 놓친 것    → patient.lost
      대기 중(waiting) 이면  환자가 입실한 것  → 정상
    같은 관측이라도 상태에 따라 의미가 반대다. 그래서 검출 노드가 아니라
    상태를 아는 여기서 해석한다.

현재 범위
    - 상태 소유와 GuideState 발행
    - 배터리 감시 (히스테리시스 포함) 와 관련 이벤트
    - Nav2 NavigateToPose 액션 클라이언트, waypoint 로드
    - 세션 시작/단계 완료를 토픽으로 수동 트리거 (QR·마커 노드가 붙기 전까지)
"""

import math
from pathlib import Path

import rclpy
import yaml
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Float32, String

from mingky_interfaces.msg import GuideState

from .event_publisher import EventPublisher


def _yaw_to_quat(yaw: float):
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


class GuideManager(Node):

    def __init__(self):
        super().__init__('guide_manager')

        self.declare_parameter('robot_id', 'pinky-01')
        self.declare_parameter('event_codes_file', '')
        self.declare_parameter('waypoints_file', '')
        # 로봇의 ap/battery_buzzer.py 와 같은 임계값을 쓴다.
        self.declare_parameter('battery_warn_v', 6.9)
        self.declare_parameter('battery_clear_v', 6.95)

        self.robot_id = self.get_parameter('robot_id').value
        self.warn_v = self.get_parameter('battery_warn_v').value
        self.clear_v = self.get_parameter('battery_clear_v').value

        self.events = EventPublisher(
            self, self.robot_id, self.get_parameter('event_codes_file').value)

        self.waypoints = self._load_waypoints(
            self.get_parameter('waypoints_file').value)

        # --- 상태. 이 노드만 쓴다 ---
        self.robot_state = GuideState.ROBOT_IDLE
        self.session_state = GuideState.SESSION_NONE
        self.session_id = 0
        self.patient_id = ''
        self.current_visit = ''
        self.voltage = float('nan')
        self.percent = -1
        self._battery_alarm = False   # 히스테리시스용

        # 상태 토픽은 늦게 뜬 구독자(LCD, 게이트웨이)도 마지막 값을 받아야 한다.
        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.state_pub = self.create_publisher(GuideState, '~/state', state_qos)

        self.create_subscription(BatteryState, '/batt_state', self._on_battery, 10)
        self.create_subscription(Float32, '/battery/percent', self._on_percent, 10)

        # QR·마커 노드가 붙기 전까지 손으로 흘려넣기 위한 입구.
        # 나중에 그 노드들이 대체하면 이 두 개는 지운다.
        self.create_subscription(String, '~/start_session', self._on_start_session, 10)
        self.create_subscription(String, '~/goto', self._on_goto, 10)

        self.nav = ActionClient(self, NavigateToPose, 'navigate_to_pose')

        self.create_timer(1.0, self._publish_state)
        self.get_logger().info(
            f'guide_manager 시작 (robot_id={self.robot_id}, '
            f'waypoint {len(self.waypoints)}개)')

    # ------------------------------------------------------------------ 설정

    def _load_waypoints(self, explicit: str) -> dict:
        """좌표는 맵과 같은 곳(mingky_bringup)에 두고 여기서는 읽기만 한다.

        DB 에 좌표를 넣지 않는 이유는 맵을 다시 만들면 모든 좌표가 무의미해지기
        때문이다. 맵과 waypoint 가 같은 패키지에 있어야 함께 버전 관리된다.
        """
        path = Path(explicit) if explicit else None
        if path is None:
            try:
                share = Path(get_package_share_directory('mingky_bringup'))
                cand = share / 'config' / 'hospital_waypoints.yaml'
                if cand.is_file():
                    path = cand
            except PackageNotFoundError:
                pass
        if path is None:
            for parent in Path(__file__).resolve().parents:
                cand = parent / 'mingky_bringup' / 'config' / 'hospital_waypoints.yaml'
                if cand.is_file():
                    path = cand
                    break
        if path is None or not path.is_file():
            self.get_logger().error(
                'hospital_waypoints.yaml 을 찾지 못했습니다. 주행 명령이 동작하지 않습니다.')
            return {}
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        self.get_logger().info(f'waypoints 로드: {path}')
        return data.get('waypoints', {})

    # ------------------------------------------------------------------ 배터리

    def _on_battery(self, msg: BatteryState):
        """전압만 신뢰한다.

        pinky_sensor_adc 는 percentage 를 NaN 으로 발행한다.
        퍼센트는 별도 변환 노드가 /battery/percent 로 내보낸다.
        """
        if math.isnan(msg.voltage):
            return
        self.voltage = msg.voltage

        # 히스테리시스가 없으면 임계값 근처에서 이벤트가 계속 튄다.
        # 모터가 돌 때 전압이 순간 처지는 것만으로도 왔다갔다 한다.
        if not self._battery_alarm and self.voltage <= self.warn_v:
            self._battery_alarm = True
            self.robot_state = GuideState.ROBOT_BATTERY_LOW
            self.events.publish(
                'robot.battery_low',
                {'percent': self.percent if self.percent >= 0 else None},
                self.session_id)
            self.get_logger().warn(f'배터리 부족 {self.voltage:.2f}V')
        elif self._battery_alarm and self.voltage >= self.clear_v:
            self._battery_alarm = False
            self.robot_state = GuideState.ROBOT_IDLE
            self.get_logger().info(f'배터리 회복 {self.voltage:.2f}V')

    def _on_percent(self, msg: Float32):
        self.percent = int(round(msg.data))

    # ------------------------------------------------------------------ 세션

    def _on_start_session(self, msg: String):
        """임시 입구. 나중에 QR 노드가 대체한다."""
        self.patient_id = msg.data.strip()
        self.session_state = GuideState.SESSION_CONFIRMED
        self.events.publish(
            'session.started',
            {'patient_id': self.patient_id, 'marker_id': None},
            self.session_id)
        self.get_logger().info(f'세션 시작: {self.patient_id}')

    def _on_goto(self, msg: String):
        self.send_goal(msg.data.strip())

    # ------------------------------------------------------------------ 주행

    def send_goal(self, waypoint_name: str) -> None:
        wp = self.waypoints.get(waypoint_name)
        if wp is None:
            self.get_logger().error(f'알 수 없는 waypoint: {waypoint_name}')
            return
        if not self.nav.wait_for_server(timeout_sec=3.0):
            self.get_logger().error('navigate_to_pose 액션 서버가 없습니다.')
            return

        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(wp['x'])
        goal.pose.pose.position.y = float(wp['y'])
        z, w = _yaw_to_quat(float(wp['yaw']))
        goal.pose.pose.orientation.z = z
        goal.pose.pose.orientation.w = w

        self.current_visit = waypoint_name
        self.robot_state = GuideState.ROBOT_MOVING
        self.session_state = GuideState.SESSION_GUIDING
        self.events.publish('nav.goal_sent', {'visit_name': waypoint_name}, self.session_id)

        self.nav.send_goal_async(goal).add_done_callback(self._on_goal_response)

    def _on_goal_response(self, future):
        handle = future.result()
        if not handle.accepted:
            self.get_logger().error('Nav2 가 목표를 거부했습니다.')
            self.events.publish(
                'nav.goal_aborted',
                {'visit_name': self.current_visit, 'error_code': -1},
                self.session_id)
            self.robot_state = GuideState.ROBOT_IDLE
            return
        handle.get_result_async().add_done_callback(self._on_goal_result)

    def _on_goal_result(self, future):
        status = future.result().status
        # action_msgs/GoalStatus.STATUS_SUCCEEDED == 4
        if status == 4:
            self.robot_state = GuideState.ROBOT_WAITING
            self.session_state = GuideState.SESSION_ARRIVED
            self.events.publish(
                'nav.goal_succeeded', {'visit_name': self.current_visit}, self.session_id)
            self.get_logger().info(f'도착: {self.current_visit}')
        else:
            self.robot_state = GuideState.ROBOT_IDLE
            self.events.publish(
                'nav.goal_aborted',
                {'visit_name': self.current_visit, 'error_code': int(status)},
                self.session_id)
            self.get_logger().error(f'주행 실패 (status={status})')

    # ------------------------------------------------------------------ 발행

    def _publish_state(self):
        msg = GuideState()
        msg.robot_id = self.robot_id
        msg.robot_state = self.robot_state
        msg.session_state = self.session_state
        msg.session_id = self.session_id
        msg.patient_id = self.patient_id
        msg.current_visit = self.current_visit
        msg.battery_voltage = float(self.voltage)
        msg.battery_percent = self.percent
        self.state_pub.publish(msg)


def main():
    rclpy.init()
    node = GuideManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
