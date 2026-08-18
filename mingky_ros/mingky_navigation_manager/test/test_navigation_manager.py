"""비임상 Waypoint 시험 주행의 중재와 안전 차단 회귀 테스트."""

import json
import math
from types import SimpleNamespace

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from mingky_guide_manager.low_obstacle import SidestepOutcome
from mingky_interfaces.msg import GuideState
from mingky_navigation_manager.navigation_manager_node import (
    BUSY_ERROR,
    CLINICAL_ERROR,
    LOCALIZATION_ERROR,
    NavigationManager,
    RECOVERY_ERROR,
    SAFETY_ERROR,
)
import pytest
import rclpy
from rclpy.parameter import Parameter
from sensor_msgs.msg import LaserScan, Range
from std_msgs.msg import Bool, String


class PendingFuture:

    def __init__(self):
        self.callback = None

    def add_done_callback(self, callback):
        self.callback = callback


class ImmediateFuture:

    def __init__(self, value):
        self.value = value

    def result(self):
        return self.value

    def add_done_callback(self, callback):
        callback(self)


class FakeActionHandle:

    def __init__(self, *, accepted=True):
        self.accepted = accepted
        self.result_future = PendingFuture()
        self.cancelled = 0

    def get_result_async(self):
        return self.result_future

    def cancel_goal_async(self):
        self.cancelled += 1
        return ImmediateFuture(SimpleNamespace(goals_canceling=[object()]))


class FakeNav:

    def __init__(self):
        self.sent = []
        self.futures = []
        self.feedback_callbacks = []

    def wait_for_server(self, timeout_sec):
        return True

    def send_goal_async(self, goal, feedback_callback=None):
        self.sent.append(goal)
        self.feedback_callbacks.append(feedback_callback)
        future = PendingFuture()
        self.futures.append(future)
        return future


class FakePathPlanner(FakeNav):
    pass


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
    node = NavigationManager()
    node.nav = FakeNav()
    published = []
    node.result_pub.publish = lambda msg: published.append(json.loads(msg.data))
    yield node, published
    node.destroy_node()


@pytest.fixture
def adaptive_manager():
    node = NavigationManager(parameter_overrides=[
        Parameter('recovery_mode', value='adaptive'),
        Parameter('recovery_retry_delay_sec', value=0.1),
    ])
    node.nav = FakeNav()
    node.path_planner = FakePathPlanner()
    published = []
    node.result_pub.publish = lambda msg: published.append(json.loads(msg.data))
    yield node, published
    node.destroy_node()


def _pose(name='draft') -> String:
    return String(data=json.dumps({
        'name': name,
        'x': 1.25,
        'y': -0.5,
        'yaw': 1.2,
    }))


def _set_fresh_recovery_inputs(node: NavigationManager) -> None:
    scan = LaserScan()
    scan.angle_min = -math.pi
    scan.angle_increment = math.radians(1.0)
    scan.range_min = 0.05
    scan.range_max = 5.0
    scan.ranges = [1.0] * 361
    node._on_scan(scan)

    pose = PoseStamped()
    pose.pose.orientation.w = 1.0
    node._latest_nav_pose = pose
    node._latest_nav_pose_received_ns = node.get_clock().now().nanoseconds


def test_test_metadata_does_not_replace_ros_context(manager):
    node, _ = manager

    assert node.context is not None
    assert node.context.ok()
    assert node._test_context is None


def test_temporary_pose_starts_one_nav2_goal(manager):
    node, published = manager

    node._on_goto_pose(_pose('hall_corner'))

    assert len(node.nav.sent) == 1
    assert node.nav.sent[0].pose.pose.position.x == pytest.approx(1.25)
    assert node.nav.sent[0].behavior_tree == ''
    assert node._active is True
    assert published == [{
        'status': 'started',
        'waypoint_name': 'hall_corner',
        'x': 1.25,
        'y': -0.5,
        'yaw': 1.2,
    }]


def test_second_goal_is_rejected_while_test_is_active(manager):
    node, published = manager
    node._on_goto_pose(_pose('first'))

    node._on_goto_pose(_pose('second'))

    assert len(node.nav.sent) == 1
    assert published[-1]['status'] == 'rejected'
    assert published[-1]['waypoint_name'] == 'second'
    assert published[-1]['error_code'] == BUSY_ERROR


def test_patient_session_blocks_waypoint_test(manager):
    node, published = manager
    node._on_guide_state(GuideState(
        session_id=42,
        session_state=GuideState.SESSION_GUIDING,
    ))

    node._on_goto_pose(_pose())

    assert node.nav.sent == []
    assert published[-1]['status'] == 'rejected'
    assert published[-1]['error_code'] == CLINICAL_ERROR


def test_patient_session_cancels_running_waypoint_test(manager):
    node, published = manager
    node._on_goto_pose(_pose())

    node._on_guide_state(GuideState(
        session_id=42,
        session_state=GuideState.SESSION_CONFIRMED,
    ))

    assert node._active is False
    assert published[-1]['status'] == 'failed'
    assert published[-1]['error_code'] == CLINICAL_ERROR


def test_localization_blocks_waypoint_test(manager):
    node, published = manager
    node._on_localization(Bool(data=True))

    node._on_goto_pose(_pose())

    assert node.nav.sent == []
    assert published[-1]['status'] == 'rejected'
    assert published[-1]['error_code'] == LOCALIZATION_ERROR


def test_localization_cancels_running_waypoint_test(manager):
    node, published = manager
    node._on_goto_pose(_pose())

    node._on_localization(Bool(data=True))

    assert node._active is False
    assert published[-1]['status'] == 'failed'
    assert published[-1]['error_code'] == LOCALIZATION_ERROR


@pytest.mark.parametrize('callback', ['battery', 'emergency'])
def test_safety_state_cancels_active_test(manager, callback):
    node, published = manager
    node._on_goto_pose(_pose())

    if callback == 'battery':
        node._on_battery(Bool(data=True))
    else:
        node._on_emergency(Bool(data=True))

    assert node._active is False
    assert published[-1]['status'] == 'failed'
    assert published[-1]['error_code'] == SAFETY_ERROR


def test_invalid_pose_never_reaches_nav2(manager):
    node, published = manager

    node._on_goto_pose(String(data='{"x": "not-a-number"}'))

    assert node.nav.sent == []
    assert published[-1]['status'] == 'failed'
    assert published[-1]['error_code'] == -2


def test_invalid_saved_waypoint_never_reaches_nav2(manager):
    node, published = manager
    node.waypoints['broken'] = {'x': 'not-a-number', 'y': 0.0, 'yaw': 0.0}

    node._on_goto(String(data='broken'))

    assert node.nav.sent == []
    assert published[-1]['status'] == 'failed'
    assert published[-1]['error_code'] == -2


def test_adaptive_mode_uses_motion_free_behavior_tree(adaptive_manager):
    node, _ = adaptive_manager

    node._on_goto_pose(_pose('adaptive_target'))

    assert len(node.nav.sent) == 1
    assert node.nav.sent[0].behavior_tree.endswith(
        'navigate_no_recovery_navfn.xml')


def test_aborted_goal_recovers_then_resends_original(adaptive_manager):
    node, _ = adaptive_manager
    _set_fresh_recovery_inputs(node)
    node._on_goto_pose(_pose('adaptive_target'))

    node._on_goal_result(
        ImmediateFuture(SimpleNamespace(status=GoalStatus.STATUS_ABORTED)),
        node._generation,
    )

    assert len(node.path_planner.sent) == 1
    assert node.path_planner.sent[0].planner_id == 'GridBased'
    assert node.path_planner.sent[0].use_start is False
    path_handle = FakeActionHandle()
    node.path_planner.futures[0].callback(ImmediateFuture(path_handle))
    path_handle.result_future.callback(ImmediateFuture(SimpleNamespace(
        status=GoalStatus.STATUS_SUCCEEDED,
        result=SimpleNamespace(
            error_code=0,
            path=SimpleNamespace(poses=[object(), object()]),
        ),
    )))

    assert len(node.nav.sent) == 2
    assert node.nav.sent[1].behavior_tree.endswith(
        'navigate_no_recovery_navfn.xml')
    recovery_handle = FakeActionHandle()
    node.nav.futures[1].callback(ImmediateFuture(recovery_handle))
    recovery_handle.result_future.callback(ImmediateFuture(SimpleNamespace(
        status=GoalStatus.STATUS_SUCCEEDED,
    )))

    assert len(node.nav.sent) == 3
    assert node.nav.sent[2].pose.pose.position.x == pytest.approx(1.25)
    assert node.nav.sent[2].pose.pose.position.y == pytest.approx(-0.5)
    assert node._test_context['recovery_attempt'] == 1


def test_recovery_stops_after_configured_maximum(adaptive_manager):
    node, published = adaptive_manager
    node.recovery_max_attempts = 1
    node._on_goto_pose(_pose('blocked_target'))

    node._on_goal_result(
        ImmediateFuture(SimpleNamespace(status=GoalStatus.STATUS_ABORTED)),
        node._generation,
    )

    assert node._active is False
    assert published[-1]['status'] == 'failed'
    assert published[-1]['error_code'] == RECOVERY_ERROR


def test_emergency_cancels_scheduled_recovery_retry(adaptive_manager):
    node, published = adaptive_manager
    node._on_goto_pose(_pose('blocked_target'))

    node._on_goal_result(
        ImmediateFuture(SimpleNamespace(status=GoalStatus.STATUS_ABORTED)),
        node._generation,
    )
    assert node._recovery_retry_timer is not None

    node._on_emergency(Bool(data=True))

    assert node._active is False
    assert node._recovery_retry_timer is None
    assert published[-1]['error_code'] == SAFETY_ERROR


def test_low_obstacle_sidestep_cancels_and_resumes_waypoint_test(manager):
    node, _ = manager
    node.low_obstacle_mode = 'sidestep'
    node.low_obstacle_driver = FakeSidestepDriver()
    _set_fresh_recovery_inputs(node)
    node._on_goto_pose(_pose('low_obstacle_target'))
    handle = FakeActionHandle()
    node.nav.futures[0].callback(ImmediateFuture(handle))

    node._on_low_obstacle_range(Range(range=0.20))
    node._on_low_obstacle_range(Range(range=0.20))

    assert handle.cancelled == 1
    handle.result_future.callback(ImmediateFuture(SimpleNamespace(
        status=GoalStatus.STATUS_CANCELED,
    )))
    assert node.low_obstacle_driver.started == [None]

    node._on_low_obstacle_sidestep_complete(
        SidestepOutcome(True, 1, 'sidestep completed'))

    assert len(node.nav.sent) == 2
    assert node.nav.sent[1].pose.pose.position.x == pytest.approx(1.25)
    assert node._test_context['low_obstacle_attempts'] == 1
    assert node._test_context['low_obstacle_side'] == 1


def test_disabled_low_obstacle_mode_keeps_waypoint_goal(manager):
    node, _ = manager
    node.low_obstacle_driver = FakeSidestepDriver()
    _set_fresh_recovery_inputs(node)
    node._on_goto_pose(_pose('normal_target'))
    handle = FakeActionHandle()
    node.nav.futures[0].callback(ImmediateFuture(handle))

    node._on_low_obstacle_range(Range(range=0.20))
    node._on_low_obstacle_range(Range(range=0.20))

    assert handle.cancelled == 0
    assert node.low_obstacle_driver.started == []
