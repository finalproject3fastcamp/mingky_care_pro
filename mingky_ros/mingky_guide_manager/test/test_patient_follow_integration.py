"""환자 거리 대기가 Nav2 실패·Adaptive Recovery와 구분되는지 검증한다."""

import json
import time
from types import SimpleNamespace

from action_msgs.msg import GoalStatus
from mingky_guide_manager.guide_manager_node import GuideManager
from mingky_interfaces.msg import GuideState
import pytest
import rclpy
from rclpy.parameter import Parameter
from std_msgs.msg import String


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


class FakeGoalHandle:

    accepted = True

    def __init__(self):
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

    def wait_for_server(self, timeout_sec):
        return True

    def send_goal_async(self, goal, feedback_callback=None):
        self.sent.append(goal)
        return PendingFuture()


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def manager():
    node = GuideManager(parameter_overrides=[
        Parameter('patient_follow_enabled', value=True),
        Parameter('patient_follow_timeout_sec', value=0.5),
        Parameter('patient_follow_wait_limit_sec', value=0.5),
        Parameter('use_arrival_chime', value=False),
    ])
    node.nav = FakeNav()
    events = []
    node.events.publish = lambda code, payload=None, session_id=0, level=None: (
        events.append((code, payload or {}, session_id)))
    node.session_id = 42
    node.patient_id = 'patient-001'
    node.session_state = GuideState.SESSION_GUIDING
    node.robot_state = GuideState.ROBOT_MOVING
    yield node, events
    node.destroy_node()


def _state(state: str, *, session_id=42, patient_id='patient-001') -> String:
    return String(data=json.dumps({
        'state': state,
        'session_id': session_id,
        'patient_id': patient_id,
        'distance': 3.0 if state == 'waiting' else 1.0,
        'source': 'qr',
    }))


def _active_goal(node: GuideManager):
    handle = FakeGoalHandle()
    context = {
        'waypoint_name': 'xray_room_goal',
        'is_dock': False,
        'session_id': 42,
        'is_waiting': False,
        'recovery_attempt': 0,
        'recovery_failures': {},
        'low_obstacle_attempts': 0,
        'low_obstacle_side': None,
    }
    node._active_nav_goal_handle = handle
    node._active_nav_result_future = handle.result_future
    node._active_nav_context = context
    return handle, context


def test_waiting_cancels_goal_then_patient_return_resends_it(manager) -> None:
    node, events = manager
    handle, _ = _active_goal(node)

    node._on_patient_follow_state(_state('waiting'))

    assert handle.cancelled == 1
    assert node._patient_wait_started_at > 0.0
    assert node.robot_state == GuideState.ROBOT_PAUSED
    assert node._patient_wait_context['waypoint_name'] == 'xray_room_goal'
    assert events[-1][0] == 'patient.follow_wait_started'

    handle.result_future.callback(ImmediateFuture(SimpleNamespace(
        status=GoalStatus.STATUS_CANCELED,
    )))
    node._on_patient_follow_state(_state('normal'))

    assert len(node.nav.sent) == 1
    assert node.robot_state == GuideState.ROBOT_MOVING
    assert node._patient_wait_context is None
    assert node._patient_wait_started_at == 0.0
    assert events[-1][0] == 'patient.follow_wait_ended'


def test_waiting_limit_ends_session_and_returns_to_dock(manager) -> None:
    node, events = manager
    now = time.monotonic()
    node._patient_follow_state = 'waiting'
    node._patient_follow_last_at = now
    node._patient_wait_started_at = (
        now - node.patient_follow_wait_limit_sec - 0.1)
    dock_reasons = []
    node._request_dock_return = dock_reasons.append

    node._check_patient_follow_timeout()

    assert node.session_id == 0
    assert node.session_state == GuideState.SESSION_NONE
    assert dock_reasons == ['patient_wait_timeout']
    assert events[-1] == (
        'session.ended',
        {'end_reason': 'aborted', 'source': 'patient_wait_timeout'},
        42,
    )


def test_mismatched_session_cannot_pause_current_guidance(manager) -> None:
    node, events = manager
    handle, _ = _active_goal(node)

    node._on_patient_follow_state(_state('waiting', session_id=99))

    assert handle.cancelled == 0
    assert node.robot_state == GuideState.ROBOT_MOVING
    assert events == []


def test_waiting_spot_navigation_does_not_start_patient_wait_limit(manager) -> None:
    node, events = manager
    handle, context = _active_goal(node)
    context['is_waiting'] = True

    node._on_patient_follow_state(_state('waiting'))

    assert handle.cancelled == 0
    assert node._patient_wait_started_at == 0.0
    assert events == []


def test_missing_heartbeat_fails_safe_to_patient_wait(manager) -> None:
    node, _ = manager
    handle, _ = _active_goal(node)
    node._patient_follow_last_at = (
        time.monotonic() - node.patient_follow_timeout_sec - 0.1)

    node._check_patient_follow_timeout()

    assert handle.cancelled == 1
    assert node.robot_state == GuideState.ROBOT_PAUSED


def test_aborted_goal_while_waiting_never_starts_adaptive_recovery(
        manager) -> None:
    node, events = manager
    _, context = _active_goal(node)
    node._patient_follow_state = 'waiting'
    adaptive_calls = []
    node._start_adaptive_recovery = (
        lambda *args, **kwargs: adaptive_calls.append((args, kwargs)) or True)

    node._on_goal_result(
        ImmediateFuture(SimpleNamespace(status=GoalStatus.STATUS_ABORTED)),
        node._nav_generation,
        'xray_room_goal',
        False,
        42,
    )

    assert adaptive_calls == []
    assert node._patient_wait_context == context
    assert node.robot_state == GuideState.ROBOT_PAUSED
    assert events[-1][0] == 'patient.follow_wait_started'


def test_safety_reset_discards_patient_resume_context(manager) -> None:
    node, _ = manager
    handle, _ = _active_goal(node)
    node._on_patient_follow_state(_state('waiting'))
    assert handle.cancelled == 1

    node._cancel_patient_wait_state()
    node._on_patient_follow_state(_state('normal'))

    assert node._patient_wait_context is None
    assert node.nav.sent == []
