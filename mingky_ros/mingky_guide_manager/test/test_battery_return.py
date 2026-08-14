"""저전압 세션 종료와 충전소 복귀 흐름의 회귀 테스트."""

import json
from types import SimpleNamespace

from mingky_guide_manager.guide_manager_node import GuideManager
from mingky_interfaces.msg import GuideState, SessionStart
import pytest
import rclpy
from rclpy.parameter import Parameter
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool


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
    node = GuideManager(parameter_overrides=[
        Parameter('robot_id', value='pinky-01'),
        Parameter('use_arrival_chime', value=False),
    ])
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


def test_long_communication_failure_ends_and_clears_active_session(manager):
    node, published = manager
    node.session_id = 43
    node.session_state = GuideState.SESSION_GUIDING
    node.patient_id = 'patient-1'
    node.current_step_order = 1
    node.current_visit = 'X-ray'
    node.session_visits = ['X-ray']

    node._on_cancel_session(String(data='robot_offline'))

    assert published == [
        ('session.ended', {'end_reason': 'robot_offline'}, 43),
    ]
    assert node.session_id == 0
    assert node.session_state == GuideState.SESSION_NONE
    assert node.patient_id == ''
    assert node.session_visits == []
    assert node.robot_state == GuideState.ROBOT_PAUSED


def test_medical_cancel_stops_navigation_and_returns_to_dock(manager):
    node, published = manager
    node.session_id = 45
    node.session_state = GuideState.SESSION_GUIDING
    node.robot_state = GuideState.ROBOT_MOVING
    node.patient_id = 'patient-1'
    node.current_step_order = 1
    node.current_visit = 'X-ray'
    node.session_visits = ['X-ray']
    cancelled = []
    node.navigation_cancel_pub = SimpleNamespace(
        publish=lambda message: cancelled.append(message.data))

    node._on_cancel_session(String(data=json.dumps({
        'reason': 'aborted',
        'session_id': 45,
    })))

    assert cancelled == [True]
    assert published == [
        ('session.ended', {'end_reason': 'aborted'}, 45),
        ('dock.return_started', {'station_name': 'charging_station_1'}, 0),
    ]
    assert node.session_id == 0
    assert node.session_state == GuideState.SESSION_NONE
    assert node.patient_id == ''
    assert node.session_visits == []
    assert node.robot_state == GuideState.ROBOT_RETURNING_TO_DOCK
    assert node._dock_reason == 'guidance_canceled'
    assert len(node.nav.sent) == 1

    result = SimpleNamespace(result=lambda: SimpleNamespace(status=4))
    node._on_goal_result(
        result, node._nav_generation, 'charging_station_1', True, 0)

    assert node.robot_state == GuideState.ROBOT_IDLE
    assert node._dock_reason is None
    assert published[-1] == (
        'dock.return_succeeded', {'station_name': 'charging_station_1'}, 0)


def test_medical_cancel_for_an_old_session_does_not_stop_current_session(manager):
    node, published = manager
    node.session_id = 46
    node.session_state = GuideState.SESSION_GUIDING
    node.robot_state = GuideState.ROBOT_MOVING
    cancelled = []
    node.navigation_cancel_pub = SimpleNamespace(
        publish=lambda message: cancelled.append(message.data))

    node._on_cancel_session(String(data=json.dumps({
        'reason': 'aborted',
        'session_id': 45,
    })))

    assert cancelled == []
    assert published == []
    assert node.session_id == 46
    assert node.session_state == GuideState.SESSION_GUIDING


def test_fire_evacuation_ends_session_and_invalidates_navigation(manager):
    node, published = manager
    node.session_id = 44
    node.session_state = GuideState.SESSION_GUIDING
    node.patient_id = 'patient-fire'
    node.current_step_order = 1
    node.current_visit = 'X-ray'
    node.session_visits = ['X-ray', 'CT']
    generation = node._nav_generation
    response = SetBool.Response()

    node._on_fire_evacuation(SetBool.Request(data=True), response)

    assert response.success is True
    assert published == [('session.ended', {'end_reason': 'fire'}, 44)]
    assert node._nav_generation == generation + 1
    assert node._fire_evacuating is True
    assert node.session_id == 0
    assert node.session_state == GuideState.SESSION_NONE
    assert node.session_visits == []
    assert node.robot_state == GuideState.ROBOT_PAUSED


def test_guidance_goal_is_blocked_during_fire_evacuation(manager):
    node, _ = manager
    node._fire_evacuating = True

    node.send_goal('xray_room_goal')

    assert node.nav.sent == []


def test_dock_success_never_emits_clinical_nav_success(manager):
    node, published = manager
    node._nav_generation = 7
    node._dock_reason = 'battery'
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


def test_session_created_while_cancel_return_is_closed_immediately(manager):
    node, published = manager
    node._dock_reason = 'guidance_canceled'
    message = SessionStart(session_id=52, patient_id='patient-2')

    node._on_session_start(message)

    assert published == [
        ('session.ended', {'end_reason': 'aborted'}, 52)]
    assert node.session_id == 0


def test_confirmed_session_starts_only_with_matching_session_id(manager):
    node, published = manager
    node.waypoints['xray_room_goal'] = {'x': 1.0, 'y': 2.0, 'yaw': 0.0}
    node.visit_waypoints['X-ray'] = {
        'goal': 'xray_room_goal',
        'waiting': 'xray_room_waiting',
    }
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
    assert node.previous_visit == ''
    assert node.current_visit == 'X-ray'
    assert published == [
        ('session.ready', {'current_visit': 'X-ray'}, 71),
        ('session.start_rejected', {'reason': 'session_mismatch'}, 71),
        ('nav.goal_sent', {'visit_name': 'X-ray'}, 71)]


def test_retried_initial_session_is_idempotent(manager):
    node, published = manager
    message = SessionStart(
        session_id=74,
        patient_id='patient-retry',
        current_step_order=1,
        visit_names=['X-ray'],
    )

    node._on_session_start(message)
    node._on_session_start(message)

    assert node.session_id == 74
    assert node.session_state == GuideState.SESSION_CONFIRMED
    assert published == [
        ('session.ready', {'current_visit': 'X-ray'}, 74),
    ]


def test_auto_localization_blocks_confirmed_session_departure(manager):
    node, published = manager
    node.session_id = 72
    node.session_state = GuideState.SESSION_CONFIRMED
    node.current_visit = 'X-ray'
    node.waypoints['xray_room_goal'] = {'x': 1.0, 'y': 2.0, 'yaw': 0.0}
    node.visit_waypoints['X-ray'] = {'goal': 'xray_room_goal'}
    node._on_localization_active(Bool(data=True))

    node._on_start_guidance(String(data='72'))

    assert node.nav.sent == []
    assert published == [
        ('session.start_rejected', {'reason': 'localization_active'}, 72)]


def test_resumed_session_restores_previous_visit_for_display(manager):
    node, _ = manager

    node._on_session_start(SessionStart(
        session_id=73,
        patient_id='patient-resumed',
        current_step_order=2,
        visit_names=['X-ray', 'CT', '물리치료실'],
    ))

    assert node.previous_visit == 'X-ray'
    assert node.current_visit == 'CT'
    assert node.session_state == GuideState.SESSION_CONFIRMED


def test_clinical_goal_waits_for_notice_before_moving_to_waiting_spot(manager):
    node, published = manager
    node.session_id = 74
    node.current_visit = 'X-ray'
    node.session_state = GuideState.SESSION_GUIDING
    node.visit_waypoints['X-ray'] = {
        'goal': 'xray_room_goal',
        'waiting': 'xray_room_waiting',
    }
    node.waypoints['xray_room_waiting'] = {
        'x': 3.0, 'y': 4.0, 'yaw': 0.0}
    node._nav_generation = 5
    succeeded = SimpleNamespace(result=lambda: SimpleNamespace(status=4))

    node._on_goal_result(
        succeeded, 5, 'xray_room_goal', False, 74)

    assert node.nav.sent == []
    assert node.robot_state == GuideState.ROBOT_WAITING
    assert node.session_state == GuideState.SESSION_ARRIVED
    assert node._arrival_notice_timer is not None
    assert published == [
        ('nav.goal_succeeded', {'visit_name': 'X-ray'}, 74)]

    node._finish_arrival_notice()

    assert len(node.nav.sent) == 1
    assert node.nav.sent[0].pose.pose.position.x == 3.0
    assert node.robot_state == GuideState.ROBOT_MOVING
    assert node.session_state == GuideState.SESSION_ARRIVED
    assert published == [
        ('nav.goal_succeeded', {'visit_name': 'X-ray'}, 74)]

    waiting_generation = node._nav_generation
    node._on_goal_result(
        succeeded, waiting_generation, 'xray_room_waiting', False, 74, True)

    assert node.robot_state == GuideState.ROBOT_WAITING
    assert node.session_state == GuideState.SESSION_IN_ROOM
    assert published == [
        ('nav.goal_succeeded', {'visit_name': 'X-ray'}, 74)]


def test_missing_waiting_spot_pauses_for_operator(manager):
    node, published = manager
    node.session_id = 75
    node.current_visit = 'CT'
    node.session_state = GuideState.SESSION_GUIDING
    node.visit_waypoints['CT'] = {'goal': 'ct_room_goal'}
    node._nav_generation = 8
    succeeded = SimpleNamespace(result=lambda: SimpleNamespace(status=4))

    node._on_goal_result(succeeded, 8, 'ct_room_goal', False, 75)

    assert node.robot_state == GuideState.ROBOT_WAITING
    node._finish_arrival_notice()

    assert node.nav.sent == []
    assert node.robot_state == GuideState.ROBOT_PAUSED
    assert node.session_state == GuideState.SESSION_ARRIVED
    assert published == [
        ('nav.goal_succeeded', {'visit_name': 'CT'}, 75),
        ('nav.waiting_spot_failed', {
            'visit_name': 'CT',
            'waypoint_name': '',
            'error_code': -2,
        }, 75),
    ]


def test_explicit_no_waiting_spot_waits_at_clinical_goal(manager):
    node, published = manager
    session_id = 76
    visit_name = '별도 대기 없는 방문지'
    node.current_visit = visit_name
    node.session_id = session_id
    node.session_state = GuideState.SESSION_GUIDING
    node.visit_waypoints[visit_name] = {
        'goal': 'no_waiting_goal',
        'waiting': None,
    }
    node._nav_generation = 9
    succeeded = SimpleNamespace(result=lambda: SimpleNamespace(status=4))

    node._on_goal_result(
        succeeded,
        9,
        'no_waiting_goal',
        False,
        session_id,
    )

    assert node.session_state == GuideState.SESSION_ARRIVED
    node._finish_arrival_notice()

    assert node.nav.sent == []
    assert node.robot_state == GuideState.ROBOT_WAITING
    assert node.session_state == GuideState.SESSION_IN_ROOM
    assert published == [
        ('nav.goal_succeeded', {'visit_name': visit_name}, session_id),
    ]


def test_emergency_stop_cancels_pending_waiting_move(manager):
    node, _ = manager
    node.session_id = 77
    node.current_visit = 'X-ray'
    node.session_state = GuideState.SESSION_ARRIVED
    node._schedule_waiting_move('X-ray', 77)

    node._on_emergency_state(Bool(data=True))

    assert node._arrival_notice_timer is None
    assert node._pending_waiting_move is None
    assert node.nav.sent == []


def test_low_battery_cancels_pending_waiting_move(manager):
    node, _ = manager
    node.session_id = 78
    node.current_visit = 'X-ray'
    node.session_state = GuideState.SESSION_ARRIVED
    node.percent = 35
    node._schedule_waiting_move('X-ray', 78)

    node._on_battery_low(Bool(data=True))

    assert node._arrival_notice_timer is None
    assert node._pending_waiting_move is None
    assert all(
        goal.pose.pose.position.x != 3.0
        for goal in node.nav.sent
    )


def test_start_guidance_rejects_unknown_visit_mapping(manager):
    node, published = manager
    node.session_id = 72
    node.session_state = GuideState.SESSION_CONFIRMED
    node.current_visit = '등록되지 않은 검사실'

    node._on_start_guidance(String(data='72'))

    assert node.nav.sent == []
    assert node.session_state == GuideState.SESSION_CONFIRMED
    assert published == [
        ('session.start_rejected', {'reason': 'missing_waypoint'}, 72)]


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


def test_waypoint_result_updates_robot_but_not_session_state(manager):
    node, published = manager
    node.session_state = GuideState.SESSION_NONE

    node._on_navigation_result(String(data=json.dumps({
        'status': 'started',
        'waypoint_name': 'hall_corner',
        'x': 1.25,
        'y': -0.5,
        'yaw': 1.2,
    })))

    assert node.robot_state == GuideState.ROBOT_MOVING
    assert node.session_state == GuideState.SESSION_NONE
    assert published == [('waypoint.test_started', {
        'waypoint_name': 'hall_corner',
        'x': 1.25,
        'y': -0.5,
        'yaw': 1.2,
    }, 0)]

    node._on_navigation_result(String(data=json.dumps({
        'status': 'succeeded',
        'waypoint_name': 'hall_corner',
    })))

    assert node.robot_state == GuideState.ROBOT_WAITING
    assert node.session_state == GuideState.SESSION_NONE
    assert published[-1] == (
        'waypoint.test_succeeded', {'waypoint_name': 'hall_corner'}, 0)


def test_guidance_does_not_start_while_waypoint_test_is_active(manager):
    node, published = manager
    node.session_id = 80
    node.session_state = GuideState.SESSION_CONFIRMED
    node.current_visit = 'X-ray'
    node.visit_waypoints['X-ray'] = {'goal': 'xray_room_goal'}
    node.waypoints['xray_room_goal'] = {'x': 1.0, 'y': 2.0, 'yaw': 0.0}
    node._maintenance_nav_active = True

    node._on_start_guidance(String(data='80'))

    assert node.nav.sent == []
    assert published == [
        ('session.start_rejected', {'reason': 'waypoint_test_active'}, 80)]


def test_nav2_unavailable_returns_session_to_confirmed_for_retry(manager):
    node, published = manager
    node.session_id = 86
    node.session_state = GuideState.SESSION_CONFIRMED
    node.current_visit = 'X-ray'
    node.visit_waypoints['X-ray'] = {'goal': 'xray_room_goal'}
    node.waypoints['xray_room_goal'] = {'x': 1.0, 'y': 2.0, 'yaw': 0.0}
    node.nav.wait_for_server = lambda timeout_sec: False

    node._on_start_guidance(String(data='86'))

    assert node.nav.sent == []
    assert node.robot_state == GuideState.ROBOT_IDLE
    assert node.session_state == GuideState.SESSION_CONFIRMED
    assert published == [('nav.goal_aborted', {
        'visit_name': 'X-ray',
        'error_code': -3,
    }, 86)]


def test_rejected_second_waypoint_does_not_clear_active_robot_state(manager):
    node, published = manager
    node._maintenance_nav_active = True
    node.robot_state = GuideState.ROBOT_MOVING

    node._on_navigation_result(String(data=json.dumps({
        'status': 'rejected',
        'waypoint_name': 'second',
        'error_code': -5,
    })))

    assert node.robot_state == GuideState.ROBOT_MOVING
    assert published == [('waypoint.test_failed', {
        'waypoint_name': 'second',
        'error_code': -5,
    }, 0)]


def test_waiting_qr_completes_step_and_starts_next_visit(manager):
    node, published = manager
    node.session_id = 81
    node.patient_id = 'patient-5'
    node.session_visits = ['X-ray', 'CT']
    node.current_step_order = 1
    node.current_visit = 'X-ray'
    node.session_state = GuideState.SESSION_IN_ROOM
    node.robot_state = GuideState.ROBOT_WAITING
    node.visit_waypoints['CT'] = {
        'goal': 'ct_room_goal',
        'waiting': 'ct_room_waiting',
    }
    node.waypoints['ct_room_goal'] = {'x': 5.0, 'y': 6.0, 'yaw': 0.0}

    node._on_session_start(SessionStart(
        session_id=81,
        patient_id='patient-5',
        current_step_order=1,
        visit_names=['X-ray', 'CT'],
    ))

    assert node.current_step_order == 2
    assert node.previous_visit == 'X-ray'
    assert node.current_visit == 'CT'
    assert node.session_state == GuideState.SESSION_GUIDING
    assert node.robot_state == GuideState.ROBOT_MOVING
    assert len(node.nav.sent) == 1
    assert node.nav.sent[0].pose.pose.position.x == 5.0
    assert published == [
        ('session.step_completed', {'step_order': 1, 'source': 'qr'}, 81),
        ('nav.goal_sent', {'visit_name': 'CT'}, 81),
    ]


def test_waiting_qr_completes_final_step_and_session(manager):
    node, published = manager
    node.session_id = 82
    node.patient_id = 'patient-6'
    node.session_visits = ['X-ray', 'CT']
    node.current_step_order = 2
    node.current_visit = 'CT'
    node.session_state = GuideState.SESSION_IN_ROOM
    node.robot_state = GuideState.ROBOT_WAITING

    node._on_session_start(SessionStart(
        session_id=82,
        patient_id='patient-6',
        current_step_order=2,
        visit_names=['X-ray', 'CT'],
    ))

    assert node.current_step_order == 0
    assert node.previous_visit == 'CT'
    assert node.session_state == GuideState.SESSION_COMPLETED
    assert node.robot_state == GuideState.ROBOT_IDLE
    assert node.nav.sent == []
    assert published == [
        ('session.step_completed', {'step_order': 2, 'source': 'qr'}, 82),
        ('session.ended', {'end_reason': 'completed'}, 82),
    ]


def test_active_session_rejects_other_patient_qr(manager):
    node, published = manager
    node.session_id = 83
    node.patient_id = 'patient-7'
    node.session_visits = ['X-ray']
    node.current_step_order = 1
    node.current_visit = 'X-ray'
    node.session_state = GuideState.SESSION_IN_ROOM
    node.robot_state = GuideState.ROBOT_WAITING

    node._on_session_start(SessionStart(
        session_id=84,
        patient_id='patient-8',
        current_step_order=1,
        visit_names=['CT'],
    ))

    assert node.session_id == 83
    assert node.patient_id == 'patient-7'
    assert node.current_visit == 'X-ray'
    assert node.nav.sent == []
    assert published == []


def test_duplicate_qr_while_moving_does_not_reset_active_session(manager):
    node, published = manager
    node.session_id = 85
    node.patient_id = 'patient-9'
    node.session_visits = ['X-ray', 'CT']
    node.current_step_order = 1
    node.current_visit = 'X-ray'
    node.session_state = GuideState.SESSION_GUIDING
    node.robot_state = GuideState.ROBOT_MOVING

    node._on_session_start(SessionStart(
        session_id=85,
        patient_id='patient-9',
        current_step_order=1,
        visit_names=['X-ray', 'CT'],
    ))

    assert node.current_step_order == 1
    assert node.current_visit == 'X-ray'
    assert node.session_state == GuideState.SESSION_GUIDING
    assert node.robot_state == GuideState.ROBOT_MOVING
    assert node.nav.sent == []
    assert published == []


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
    node._dock_reason = 'battery'
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
