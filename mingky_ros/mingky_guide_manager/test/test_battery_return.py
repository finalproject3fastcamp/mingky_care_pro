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
