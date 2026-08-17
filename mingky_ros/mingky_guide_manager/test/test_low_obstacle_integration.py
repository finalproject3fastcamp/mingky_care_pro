"""guide_manager의 선택형 저상 장애물 회피 흐름 테스트."""

from types import SimpleNamespace

import pytest
import rclpy
from action_msgs.msg import GoalStatus
from mingky_guide_manager.guide_manager_node import GuideManager
from mingky_guide_manager.low_obstacle import SidestepOutcome
from mingky_interfaces.msg import GuideState
from rclpy.parameter import Parameter
from sensor_msgs.msg import LaserScan, Range


class ImmediateFuture:
    def __init__(self, value):
        self.value = value

    def result(self):
        return self.value

    def add_done_callback(self, callback):
        callback(self)


class PendingFuture:
    def add_done_callback(self, callback):
        self.callback = callback


class FakeNav:
    def __init__(self):
        self.sent = []

    def wait_for_server(self, timeout_sec):
        return True

    def send_goal_async(self, goal, feedback_callback=None):
        self.sent.append(goal)
        return PendingFuture()


class FakeGoalHandle:
    def __init__(self):
        self.cancelled = 0

    def cancel_goal_async(self):
        self.cancelled += 1
        return ImmediateFuture(SimpleNamespace(goals_canceling=[object()]))


class FakeSidestepDriver:
    def __init__(self):
        self.active = False
        self.ranges = []
        self.started = []

    def update_range(self, value):
        self.ranges.append(value)

    def start(self, preferred_side=None):
        self.active = True
        self.started.append(preferred_side)
        return True

    def cancel(self):
        self.active = False


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def manager():
    node = GuideManager(parameter_overrides=[
        Parameter('robot_id', value='pinky-01'),
        Parameter('use_arrival_chime', value=False),
        Parameter('low_obstacle_mode', value='sidestep'),
    ])
    node.nav = FakeNav()
    node.low_obstacle_driver = FakeSidestepDriver()
    node.events.publish = lambda *args, **kwargs: None
    yield node
    node.destroy_node()


def _clear_scan() -> LaserScan:
    scan = LaserScan()
    scan.angle_min = -3.141592
    scan.angle_increment = 0.017453
    scan.range_min = 0.05
    scan.range_max = 5.0
    scan.ranges = [1.0] * 361
    return scan


def _set_active_goal(node: GuideManager):
    handle = FakeGoalHandle()
    node._active_nav_goal_handle = handle
    node._active_nav_result_future = ImmediateFuture(
        SimpleNamespace(status=GoalStatus.STATUS_CANCELED))
    node._active_nav_context = {
        'waypoint_name': 'xray_room_goal',
        'is_dock': False,
        'session_id': 71,
        'is_waiting': False,
        'recovery_attempt': 0,
        'recovery_failures': {},
        'low_obstacle_attempts': 0,
        'low_obstacle_side': None,
    }
    node.robot_state = GuideState.ROBOT_MOVING
    return handle


def test_confirmed_low_obstacle_cancels_goal_before_sidestep(manager):
    handle = _set_active_goal(manager)
    manager._on_scan(_clear_scan())

    manager._on_low_obstacle_range(Range(range=0.20))
    assert handle.cancelled == 0
    manager._on_low_obstacle_range(Range(range=0.20))

    assert handle.cancelled == 1
    assert manager.low_obstacle_driver.started == [None]


def test_disabled_mode_keeps_current_navigation(manager):
    manager.low_obstacle_mode = 'disabled'
    handle = _set_active_goal(manager)
    manager._on_scan(_clear_scan())

    manager._on_low_obstacle_range(Range(range=0.20))
    manager._on_low_obstacle_range(Range(range=0.20))

    assert handle.cancelled == 0


def test_mode_change_is_rejected_during_navigation(manager):
    _set_active_goal(manager)

    result = manager._on_set_parameters([
        Parameter('low_obstacle_mode', value='disabled'),
    ])

    assert result.successful is False
    assert manager.low_obstacle_mode == 'sidestep'


def test_successful_sidestep_resends_original_goal(manager):
    manager.waypoints['xray_room_goal'] = {'x': 1.0, 'y': 2.0, 'yaw': 0.0}
    manager._pending_low_obstacle_context = _set_active_goal(
        manager) and dict(manager._active_nav_context)
    manager._active_nav_goal_handle = None
    manager._active_nav_context = None

    manager._on_low_obstacle_sidestep_complete(
        SidestepOutcome(True, 1, 'sidestep completed'))

    assert len(manager.nav.sent) == 1
    assert manager._active_nav_context['low_obstacle_attempts'] == 1
    assert manager._active_nav_context['low_obstacle_side'] == 1
