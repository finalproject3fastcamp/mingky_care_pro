"""저전압 세션 종료와 충전소 복귀 흐름의 회귀 테스트."""

from types import SimpleNamespace

from mingky_guide_manager.guide_manager_node import GuideManager
from mingky_interfaces.msg import GuideState, SessionStart
import pytest
import rclpy
from rclpy.parameter import Parameter
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
    node = GuideManager(parameter_overrides=[Parameter('robot_id', value='pinky-01')])
    node.nav = FakeNav()
    published = []
    node.events.publish = lambda code, payload=None, session_id=0, level=None: (
        published.append((code, payload or {}, session_id)))
    yield node, published
    node.destroy_node()


def test_low_battery_ends_active_session_before_dock_return(manager):
    node, published = manager
    node.session_id = 42
    node.session_state = GuideState.SESSION_GUIDING
    node.patient_id = 'patient-1'
    node.percent = 35

    node._on_battery_low(Bool(data=True))

    assert [event[0] for event in published] == [
        'robot.battery_low', 'session.ended', 'dock.return_started']
    assert published[0][2] == 42
    assert published[1] == ('session.ended', {'end_reason': 'battery'}, 42)
    assert published[2][1] == {'station_name': 'charging_station_1'}
    assert node.session_id == 0
    assert node.session_state == GuideState.SESSION_NONE
    assert node.patient_id == ''
    assert len(node.nav.sent) == 1


def test_dock_success_never_emits_clinical_nav_success(manager):
    node, published = manager
    node._nav_generation = 7
    result = SimpleNamespace(result=lambda: SimpleNamespace(status=4))

    node._on_goal_result(result, 7, 'charging_station_1', True, 0)

    assert published == [
        ('dock.return_succeeded', {'station_name': 'charging_station_1'}, 0)]
    assert node.robot_state == GuideState.ROBOT_WAITING


def test_robot_number_selects_matching_charging_station(manager):
    node, _ = manager
    node.robot_id = 'pinky-02'
    assert node._default_charging_waypoint() == 'charging_station_2'


def test_session_created_while_low_is_closed_immediately(manager):
    node, published = manager
    node._battery_alarm = True
    message = SessionStart(session_id=51, patient_id='patient-2')

    node._on_session_start(message)

    assert published == [('session.ended', {'end_reason': 'battery'}, 51)]
    assert node.session_id == 0


def test_confirmed_session_starts_only_with_matching_session_id(manager):
    node, published = manager
    node.waypoints['xray_room_goal'] = {'x': 1.0, 'y': 2.0, 'yaw': 0.0}
    node.visit_waypoints['X-ray'] = 'xray_room_goal'
    message = SessionStart(
        session_id=71,
        patient_id='patient-3',
        current_step_order=1,
        visit_names=['X-ray', '임상병리실'],
    )
    node._on_session_start(message)

    node._on_start_guidance(String(data='70'))
    assert node.nav.sent == []
    assert node.session_state == GuideState.SESSION_CONFIRMED

    node._on_start_guidance(String(data='71'))

    assert len(node.nav.sent) == 1
    assert node.nav.sent[0].pose.pose.position.x == 1.0
    assert node.robot_state == GuideState.ROBOT_MOVING
    assert node.session_state == GuideState.SESSION_GUIDING
    assert node.current_visit == 'X-ray'
    assert published == [
        ('nav.goal_sent', {'visit_name': 'X-ray'}, 71)]


def test_start_guidance_rejects_unknown_visit_mapping(manager):
    node, _ = manager
    node.session_id = 72
    node.session_state = GuideState.SESSION_CONFIRMED
    node.current_visit = '등록되지 않은 검사실'

    node._on_start_guidance(String(data='72'))

    assert node.nav.sent == []
    assert node.session_state == GuideState.SESSION_CONFIRMED


def test_completed_schedule_does_not_restart_first_visit(manager):
    node, _ = manager
    message = SessionStart(
        session_id=73,
        patient_id='patient-4',
        current_step_order=0,
        visit_names=['X-ray'],
    )

    node._on_session_start(message)
    node._on_start_guidance(String(data='73'))

    assert node.current_visit == ''
    assert node.nav.sent == []


def test_emergency_state_is_mirrored_with_recovery_event(manager):
    node, published = manager
    node.session_id = 63
    node._on_emergency_reason(String(data='obstacle'))

    node._on_emergency_state(Bool(data=True))
    node._on_emergency_reason(String(data='operator_release'))
    node._on_emergency_state(Bool(data=False))

    assert published == [
        ('robot.paused', {'reason': 'obstacle'}, 63),
        ('robot.resumed', {'reason': 'operator_release'}, 63),
    ]
    assert node.robot_state == GuideState.ROBOT_IDLE


def test_dock_failure_retries_before_final_event(manager):
    node, published = manager
    node._battery_alarm = True
    node._dock_attempt = 1

    node._dock_failed('charging_station_1', 6, retryable=True)

    assert published == []
    assert node._dock_retry_timer is not None
    node._cancel_dock_retry()

    node._dock_attempt = node.dock_max_attempts
    node._dock_failed('charging_station_1', 6, retryable=True)
    assert published == [
        ('dock.return_failed', {
            'station_name': 'charging_station_1', 'error_code': 6}, 0)]


def test_default_navigation_keeps_nav2_default_behavior_tree(manager):
    node, _ = manager
    node.waypoints['target'] = {'x': 1.0, 'y': 2.0, 'yaw': 0.0}

    node.send_goal('target')

    assert node.nav.sent[-1].behavior_tree == ''


def test_adaptive_smac_failure_waits_and_keeps_original_goal():
    node = GuideManager(parameter_overrides=[
        Parameter('robot_id', value='pinky-01'),
        Parameter('recovery_mode', value='adaptive'),
        Parameter('planner_mode', value='smac2d'),
    ])
    node.nav = FakeNav()
    node.waypoints['target'] = {'x': 1.0, 'y': 2.0, 'yaw': 0.0}
    published = []
    node.events.publish = lambda code, payload=None, session_id=0, level=None: (
        published.append((code, payload or {}, session_id)))
    try:
        node.send_goal('target')
        first_generation = node._nav_generation

        assert node.nav.sent[-1].behavior_tree.endswith(
            'navigate_no_recovery_smac2d.xml')

        aborted = SimpleNamespace(result=lambda: SimpleNamespace(status=6))
        node._on_goal_result(aborted, first_generation, 'target', False, 0)

        assert len(node.nav.sent) == 1
        assert node._adaptive_retry_timer is not None
        assert node.robot_state == GuideState.ROBOT_WAITING
        assert not any(code == 'nav.goal_aborted' for code, _, _ in published)

        retry_generation = node._nav_generation
        node._retry_adaptive_goal(
            retry_generation, 'target', 0, 1, {'forward': 1})

        assert len(node.nav.sent) == 2
        assert node.nav.sent[-1].behavior_tree.endswith(
            'navigate_no_recovery_smac2d.xml')
        assert node._adaptive_retry_timer is None
    finally:
        node._cancel_adaptive_retry()
        node.destroy_node()
