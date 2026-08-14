"""엔지니어용 단일 Waypoint 시험 주행을 소유하는 노드.

환자 안내 순서와 방문지 전환은 guide_manager가 담당한다. 이 노드는 환자 세션과
무관한 저장 Waypoint 또는 임시 좌표 시험만 Nav2에 전달하며, 동시에 하나의 목표만
허용한다. 환자 안내가 시작되거나 저전압·비상정지가 발생하면 시험 목표를 취소한다.
"""

import json
import math
from pathlib import Path

from ament_index_python.packages import get_package_share_directory, PackageNotFoundError
from geometry_msgs.msg import PoseStamped
from mingky_interfaces.msg import GuideState
from nav2_msgs.action import NavigateToPose
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String
import yaml


BUSY_ERROR = -5
SAFETY_ERROR = -6
CLINICAL_ERROR = -7
LOCALIZATION_ERROR = -8


def _yaw_to_quat(yaw: float) -> tuple[float, float]:
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


class NavigationManager(Node):

    def __init__(self, **kwargs):
        super().__init__('navigation_manager', **kwargs)

        self.declare_parameter('robot_id', 'pinky-01')
        self.declare_parameter('waypoints_file', '')
        self.declare_parameter('map_name', 'yun_map_highres_clean')

        self.robot_id = str(self.get_parameter('robot_id').value)
        self.waypoints = self._load_waypoints(
            str(self.get_parameter('waypoints_file').value),
            str(self.get_parameter('map_name').value),
        )

        self._clinical_active = False
        self._battery_low = False
        self._emergency = False
        self._localization_active = False
        self._active = False
        self._generation = 0
        self._goal_handle = None
        # Node._context 는 rclpy 자체가 ROS Context를 보관하는 내부 속성이다.
        # 시험 목표 메타데이터는 별도 이름으로 두어 ActionClient가 사용하는
        # ROS Context를 덮어쓰지 않는다.
        self._test_context: dict | None = None

        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.result_pub = self.create_publisher(String, '~/result', 10)
        self.active_pub = self.create_publisher(Bool, '~/active', state_qos)
        self.create_subscription(String, '~/goto', self._on_goto, 10)
        self.create_subscription(String, '~/goto_pose', self._on_goto_pose, 10)
        self.create_subscription(Bool, '~/cancel', self._on_cancel, 10)
        self.create_subscription(
            GuideState, '/guide_manager/state', self._on_guide_state, state_qos)
        self.create_subscription(Bool, '/battery/low', self._on_battery, state_qos)
        self.create_subscription(
            Bool, '/emergency_stop/state', self._on_emergency, state_qos)
        self.create_subscription(
            Bool, '/auto_localize/active', self._on_localization, state_qos)

        self.nav = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self._publish_active()
        self.get_logger().info(
            f'navigation_manager 시작 (robot_id={self.robot_id}, '
            f'waypoint {len(self.waypoints)}개)')

    def _waypoint_candidates(self, map_name: str) -> list[Path]:
        relative = Path('config') / 'waypoints' / f'{map_name}_waypoints.yaml'
        candidates: list[Path] = []
        try:
            candidates.append(
                Path(get_package_share_directory('mingky_bringup')) / relative)
        except PackageNotFoundError:
            pass
        for parent in Path(__file__).resolve().parents:
            candidates.append(parent / 'mingky_bringup' / relative)
        return candidates

    def _load_waypoints(self, explicit: str, map_name: str) -> dict:
        if explicit:
            path = Path(explicit).expanduser()
        else:
            path = next(
                (candidate for candidate in self._waypoint_candidates(map_name)
                 if candidate.is_file()),
                None,
            )
        if path is None or not path.is_file():
            self.get_logger().error(
                f'map_name={map_name!r}의 Waypoint 파일을 찾지 못했습니다.')
            return {}
        with path.open(encoding='utf-8') as stream:
            data = yaml.safe_load(stream) or {}
        return data.get('waypoints') or {}

    def _on_guide_state(self, msg: GuideState) -> None:
        active_states = (
            GuideState.SESSION_CONFIRMED,
            GuideState.SESSION_GUIDING,
            GuideState.SESSION_ARRIVED,
            GuideState.SESSION_IN_ROOM,
        )
        clinical_active = msg.session_id > 0 and msg.session_state in active_states
        if clinical_active and not self._clinical_active and self._active:
            self._cancel_active(CLINICAL_ERROR, '환자 안내가 시작되어 시험 주행을 취소했습니다.')
        self._clinical_active = clinical_active

    def _on_battery(self, msg: Bool) -> None:
        self._battery_low = bool(msg.data)
        if self._battery_low and self._active:
            self._cancel_active(SAFETY_ERROR, '저전압으로 시험 주행을 취소했습니다.')

    def _on_emergency(self, msg: Bool) -> None:
        self._emergency = bool(msg.data)
        if self._emergency and self._active:
            self._cancel_active(SAFETY_ERROR, '비상정지로 시험 주행을 취소했습니다.')

    def _on_localization(self, msg: Bool) -> None:
        self._localization_active = bool(msg.data)
        if self._localization_active and self._active:
            self._cancel_active(
                LOCALIZATION_ERROR,
                'AMCL 자동 재탐색이 시작되어 시험 주행을 취소했습니다.')

    def _on_cancel(self, msg: Bool) -> None:
        if msg.data and self._active:
            self._cancel_active(CLINICAL_ERROR, '상위 작업 요청으로 시험 주행을 취소했습니다.')

    def _on_goto(self, msg: String) -> None:
        name = msg.data.strip()
        raw_waypoint = self.waypoints.get(name)
        if raw_waypoint is None:
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -2,
                'message': '시험 Waypoint를 찾을 수 없습니다.',
            })
            return
        try:
            waypoint = {
                key: float(raw_waypoint[key]) for key in ('x', 'y', 'yaw')
            }
        except (KeyError, TypeError, ValueError) as exc:
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -2,
                'message': f'저장 Waypoint 형식이 올바르지 않습니다: {exc}',
            })
            return
        if not all(math.isfinite(value) for value in waypoint.values()):
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -2,
                'message': '저장 Waypoint 좌표는 유한한 수여야 합니다.',
            })
            return
        self._start_test(name, waypoint)

    def _on_goto_pose(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
            name = str(payload.get('name') or 'waypoint_draft').strip()
            waypoint = {
                'x': float(payload['x']),
                'y': float(payload['y']),
                'yaw': float(payload['yaw']),
            }
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._publish_result('failed', {
                'waypoint_name': 'waypoint_draft',
                'error_code': -2,
                'message': f'시험 Waypoint 형식이 올바르지 않습니다: {exc}',
            })
            return
        if not all(math.isfinite(value) for value in waypoint.values()):
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -2,
                'message': '시험 Waypoint 좌표는 유한한 수여야 합니다.',
            })
            return
        self._start_test(name, waypoint)

    def _start_test(self, name: str, waypoint: dict) -> None:
        if self._active:
            self._publish_result('rejected', {
                'waypoint_name': name,
                'error_code': BUSY_ERROR,
                'message': '다른 Waypoint 시험 주행이 진행 중입니다.',
            })
            return
        if self._clinical_active:
            self._publish_result('rejected', {
                'waypoint_name': name,
                'error_code': CLINICAL_ERROR,
                'message': '환자 안내 중에는 Waypoint 시험 주행을 시작할 수 없습니다.',
            })
            return
        if self._localization_active:
            self._publish_result('rejected', {
                'waypoint_name': name,
                'error_code': LOCALIZATION_ERROR,
                'message': 'AMCL 자동 재탐색 중에는 Waypoint 시험 주행을 시작할 수 없습니다.',
            })
            return
        if self._battery_low or self._emergency:
            self._publish_result('rejected', {
                'waypoint_name': name,
                'error_code': SAFETY_ERROR,
                'message': '안전 정지 상태에서는 Waypoint 시험 주행을 시작할 수 없습니다.',
            })
            return
        if not self.nav.wait_for_server(timeout_sec=3.0):
            self._publish_result('failed', {
                'waypoint_name': name,
                'error_code': -3,
                'message': 'Nav2 액션 서버가 없습니다.',
            })
            return

        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(waypoint['x'])
        goal.pose.pose.position.y = float(waypoint['y'])
        z, w = _yaw_to_quat(float(waypoint['yaw']))
        goal.pose.pose.orientation.z = z
        goal.pose.pose.orientation.w = w

        self._generation += 1
        generation = self._generation
        self._active = True
        self._test_context = {
            'waypoint_name': name,
            'x': float(waypoint['x']),
            'y': float(waypoint['y']),
            'yaw': float(waypoint['yaw']),
        }
        self._publish_active()
        self._publish_result('started', self._test_context)

        future = self.nav.send_goal_async(goal)
        future.add_done_callback(
            lambda done: self._on_goal_response(done, generation))

    def _on_goal_response(self, future, generation: int) -> None:
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001
            if generation == self._generation:
                self._finish('failed', -4, f'목표 전송 중 예외: {exc}')
            return
        if generation != self._generation:
            if handle.accepted:
                handle.cancel_goal_async()
            return
        if not handle.accepted:
            self._finish('failed', -1, 'Nav2가 시험 목표를 거부했습니다.')
            return
        self._goal_handle = handle
        result = handle.get_result_async()
        result.add_done_callback(
            lambda done: self._on_goal_result(done, generation))

    def _on_goal_result(self, future, generation: int) -> None:
        if generation != self._generation:
            return
        try:
            status = int(future.result().status)
        except Exception as exc:  # noqa: BLE001
            self._finish('failed', -4, f'결과 수신 중 예외: {exc}')
            return
        if status == 4:
            self._finish('succeeded', 0, 'Waypoint 시험 주행에 도착했습니다.')
        else:
            self._finish('failed', status, f'Waypoint 시험 주행 실패 (status={status})')

    def _cancel_active(self, error_code: int, message: str) -> None:
        context = dict(self._test_context or {})
        self._generation += 1
        if self._goal_handle is not None:
            self._goal_handle.cancel_goal_async()
        self._goal_handle = None
        self._active = False
        self._test_context = None
        self._publish_active()
        self._publish_result('failed', {
            **context,
            'error_code': error_code,
            'message': message,
        })

    def _finish(self, status: str, error_code: int, message: str) -> None:
        context = dict(self._test_context or {})
        self._goal_handle = None
        self._active = False
        self._test_context = None
        self._publish_active()
        self._publish_result(status, {
            **context,
            'error_code': error_code,
            'message': message,
        })

    def _publish_active(self) -> None:
        self.active_pub.publish(Bool(data=self._active))

    def _publish_result(self, status: str, payload: dict) -> None:
        self.result_pub.publish(String(data=json.dumps({
            'status': status,
            **payload,
        }, ensure_ascii=False)))


def main() -> None:
    rclpy.init()
    node = NavigationManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
