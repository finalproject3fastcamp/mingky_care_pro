"""비임상 Waypoint 시험 주행의 중재와 안전 차단 회귀 테스트."""

import json

from mingky_interfaces.msg import GuideState
from mingky_navigation_manager.navigation_manager_node import (
    BUSY_ERROR,
    CLINICAL_ERROR,
    LOCALIZATION_ERROR,
    NavigationManager,
    SAFETY_ERROR,
)
import pytest
import rclpy
from std_msgs.msg import Bool, String


class PendingFuture:

    def __init__(self):
        self.callback = None

    def add_done_callback(self, callback):
        self.callback = callback


class FakeNav:

    def __init__(self):
        self.sent = []

    def wait_for_server(self, timeout_sec):
        return True

    def send_goal_async(self, goal):
        self.sent.append(goal)
        return PendingFuture()


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


def _pose(name='draft') -> String:
    return String(data=json.dumps({
        'name': name,
        'x': 1.25,
        'y': -0.5,
        'yaw': 1.2,
    }))


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
