"""관제 명령을 로봇 책임 경로로 보내는 계약."""

import json
import threading
from types import SimpleNamespace

import pytest

from mingky_event_gateway.gateway_node import (
    ACTIVE_GUIDE_SESSION_STATES,
    HeartbeatFailureGuard,
    IntervalGate,
    BATTERY_STALE_AFTER_SEC,
    EventGateway,
    RejectBudget,
    SEND_OK,
    SEND_REJECT,
    SEND_RETRY,
    SYSTEM_COMMANDS,
    battery_is_stale,
    isolate_rejected,
    matches_guided_patient,
    parse_patient_follow_status,
    parse_navigation_speed,
    parse_low_obstacle_mode,
    parse_low_obstacle_state,
    send_outcome,
)
from mingky_interfaces.msg import GuideState, QrObservation
from std_msgs.msg import Bool, String


def test_system_commands_only_target_fixed_systemd_actions():
    assert SYSTEM_COMMANDS == {
        'system_start': 'start',
        'system_stop': 'stop',
        'system_restart': 'restart',
    }


def test_navigation_speed_accepts_configured_safe_range():
    assert parse_navigation_speed('0.05') == 0.05
    assert parse_navigation_speed('0.20') == 0.20
    assert parse_navigation_speed('0.25') == 0.25


def test_navigation_speed_rejects_out_of_range_or_off_step_values():
    assert parse_navigation_speed('0.04') is None
    assert parse_navigation_speed('0.251') is None
    assert parse_navigation_speed('0.26') is None
    assert parse_navigation_speed('nan') is None
    assert parse_navigation_speed('fast') is None


def test_low_obstacle_mode_only_accepts_retired_mode_off_switch():
    assert parse_low_obstacle_mode('disabled') == 'disabled'
    assert parse_low_obstacle_mode('sidestep') is None
    assert parse_low_obstacle_mode('enabled') is None
    assert parse_low_obstacle_mode('range_layer') is None


def test_low_obstacle_state_only_accepts_fusion_state_machine_values():
    assert parse_low_obstacle_state(' slow ') == 'SLOW'
    assert parse_low_obstacle_state('FORWARD_BLOCKED') == 'FORWARD_BLOCKED'
    assert parse_low_obstacle_state('blocked') is None


def test_low_obstacle_state_is_reported_in_heartbeat():
    gateway = object.__new__(EventGateway)
    gateway._low_obstacle_state = None

    gateway._on_low_obstacle_state(String(data='CONFIRMED'))

    assert gateway._low_obstacle_state == 'CONFIRMED'


class _Logger:
    def info(self, message):
        pass

    def error(self, message):
        pass


class _Future:
    def __init__(self, successful):
        result = type('Result', (), {'successful': successful, 'reason': ''})()
        self.response = type('Response', (), {'results': [result]})()

    def result(self):
        return self.response


class _ParameterClient:
    def __init__(self):
        self.requests = []

    def set_parameters(self, parameters):
        self.requests.append(parameters)
        return SimpleNamespace(add_done_callback=lambda callback: None)


def test_navigation_speed_updates_reported_value_after_parameter_success():
    gateway = object.__new__(EventGateway)
    gateway._navigation_speed_mps = 0.20
    gateway.get_logger = lambda: _Logger()

    gateway._on_navigation_speed_response(_Future(True), 0.24)

    assert gateway._navigation_speed_mps == 0.24


def test_navigation_speed_keeps_previous_value_after_parameter_rejection():
    gateway = object.__new__(EventGateway)
    gateway._navigation_speed_mps = 0.20
    gateway.get_logger = lambda: _Logger()

    gateway._on_navigation_speed_response(_Future(False), 0.24)

    assert gateway._navigation_speed_mps == 0.20


def test_low_obstacle_mode_reports_disabled_after_parameter_success():
    gateway = object.__new__(EventGateway)
    gateway._low_obstacle_mode = 'disabled'
    gateway.get_logger = lambda: _Logger()

    gateway._on_low_obstacle_mode_response(_Future(True), 'disabled')

    assert gateway._low_obstacle_mode == 'disabled'


def test_low_obstacle_mode_keeps_previous_value_after_parameter_rejection():
    gateway = object.__new__(EventGateway)
    gateway._low_obstacle_mode = 'disabled'
    gateway.get_logger = lambda: _Logger()

    gateway._on_low_obstacle_mode_response(_Future(False), 'sidestep')

    assert gateway._low_obstacle_mode == 'disabled'


def test_waypoint_mode_is_applied_before_guide_mode():
    gateway = object.__new__(EventGateway)
    gateway._guide_parameters = _ParameterClient()
    gateway.get_logger = lambda: _Logger()

    gateway._on_navigation_low_obstacle_mode_response(
        _Future(True), 'disabled', 'disabled')

    assert len(gateway._guide_parameters.requests) == 1
    parameter = gateway._guide_parameters.requests[0][0]
    assert parameter.name == 'low_obstacle_mode'
    assert parameter.value == 'disabled'


def test_guide_mode_rejection_rolls_waypoint_mode_back():
    gateway = object.__new__(EventGateway)
    gateway._low_obstacle_mode = 'disabled'
    gateway._navigation_parameters = _ParameterClient()
    gateway.get_logger = lambda: SimpleNamespace(
        info=lambda message: None,
        warn=lambda message: None,
        error=lambda message: None,
    )

    gateway._on_low_obstacle_mode_response(
        _Future(False), 'sidestep', 'disabled')

    assert gateway._low_obstacle_mode == 'disabled'
    parameter = gateway._navigation_parameters.requests[0][0]
    assert parameter.value == 'disabled'


def test_active_guidance_states_cover_confirmation_through_room_waiting():
    assert ACTIVE_GUIDE_SESSION_STATES == (
        GuideState.SESSION_CONFIRMED,
        GuideState.SESSION_GUIDING,
        GuideState.SESSION_ARRIVED,
        GuideState.SESSION_IN_ROOM,
    )


def test_guide_state_adds_authoritative_wait_time_to_qr_payload():
    gateway = SimpleNamespace(
        _qr_lock=threading.Lock(),
        _qr_wake=threading.Event(),
        _qr_observation=None,
    )
    state = GuideState(
        session_id=42,
        session_state=GuideState.SESSION_GUIDING,
        patient_id='patient-001',
        returning_to_dock=False,
        patient_wait_remaining_sec=12.5,
    )

    EventGateway._on_guide_state(gateway, state)

    assert gateway._qr_observation['patient_wait_remaining_sec'] == pytest.approx(12.5)
    assert gateway._qr_wake.is_set()


def test_guide_state_marks_wait_time_inactive_in_qr_payload():
    gateway = SimpleNamespace(
        _qr_lock=threading.Lock(),
        _qr_wake=threading.Event(),
        _qr_observation={'patient_wait_remaining_sec': 5.0},
    )
    state = GuideState(patient_wait_remaining_sec=-1.0)

    EventGateway._on_guide_state(gateway, state)

    assert gateway._qr_observation['patient_wait_remaining_sec'] is None


def test_cancel_guidance_dispatches_session_scoped_abort():
    published = []
    gateway = SimpleNamespace(
        _guide_session_id=42,
        _session_cancel_pub=SimpleNamespace(
            publish=lambda message: published.append(message.data)),
        get_logger=lambda: SimpleNamespace(
            info=lambda message: None,
            warn=lambda message: None,
            error=lambda message: None,
        ),
    )

    handled = EventGateway._dispatch(gateway, {
        'order_id': 'order-1',
        'command': 'cancel_guidance',
        'argument': '42',
    })

    assert handled is True
    assert json.loads(published[0]) == {
        'reason': 'aborted',
        'session_id': 42,
    }


def test_cancel_guidance_never_cancels_a_different_current_session():
    published = []
    gateway = SimpleNamespace(
        _guide_session_id=43,
        _session_cancel_pub=SimpleNamespace(
            publish=lambda message: published.append(message.data)),
        get_logger=lambda: SimpleNamespace(
            info=lambda message: None,
            warn=lambda message: None,
            error=lambda message: None,
        ),
    )

    handled = EventGateway._dispatch(gateway, {
        'order_id': 'old-order',
        'command': 'cancel_guidance',
        'argument': '42',
    })

    assert handled is True
    assert published == []


def test_cancel_navigation_dispatches_stop_signal():
    published = []
    gateway = SimpleNamespace(
        _navigation_cancel_pub=SimpleNamespace(
            publish=lambda message: published.append(message)),
        get_logger=lambda: SimpleNamespace(
            info=lambda message: None,
            warn=lambda message: None,
            error=lambda message: None,
        ),
    )

    handled = EventGateway._dispatch(gateway, {
        'order_id': 'cancel-nav',
        'command': 'cancel_navigation',
        'argument': 'run',
    })

    assert handled is True
    assert len(published) == 1
    assert isinstance(published[0], Bool)
    assert published[0].data is True


def test_heartbeat_guard_triggers_once_after_sustained_failure():
    guard = HeartbeatFailureGuard(30.0)

    assert guard.failure(100.0, clinical_active=True) is False
    assert guard.failure(129.9, clinical_active=True) is False
    assert guard.failure(130.0, clinical_active=True) is True
    assert guard.failure(135.0, clinical_active=True) is False


def test_heartbeat_guard_resets_after_success_and_ignores_idle_robot():
    guard = HeartbeatFailureGuard(30.0)

    assert guard.failure(100.0, clinical_active=False) is False
    assert guard.failure(140.0, clinical_active=False) is False
    assert guard.failure(200.0, clinical_active=True) is False
    assert guard.failure(229.0, clinical_active=True) is False
    assert guard.failure(230.0, clinical_active=True) is True


def test_qr_distance_only_accepts_current_patient_while_guiding():
    observation = QrObservation(visible=True, data='patient-001')

    assert matches_guided_patient(
        observation, GuideState.SESSION_GUIDING, 'patient-001') is True
    assert matches_guided_patient(
        observation, GuideState.SESSION_GUIDING, 'patient-002') is False
    assert matches_guided_patient(
        observation, GuideState.SESSION_IN_ROOM, 'patient-001') is False


def test_patient_follow_status_accepts_current_session_patient():
    status = parse_patient_follow_status(json.dumps({
        'state': 'slow',
        'session_id': 42,
        'patient_id': 'patient-001',
        'distance': 0.18,
        'source': 'qr',
        'qr_visible': True,
        'visual_visible': False,
    }), 42, 'patient-001')

    assert status == {
        'follow_state': 'slow',
        'follow_distance': 0.18,
        'follow_source': 'qr',
        'qr_visible': True,
        'visual_visible': False,
    }


def test_patient_follow_status_accepts_acquisition_source():
    status = parse_patient_follow_status(json.dumps({
        'state': 'slow',
        'session_id': 42,
        'patient_id': 'patient-001',
        'distance': None,
        'source': 'acquiring',
        'qr_visible': False,
        'visual_visible': False,
    }), 42, 'patient-001')

    assert status['follow_state'] == 'slow'
    assert status['follow_distance'] is None
    assert status['follow_source'] == 'acquiring'


def test_patient_follow_status_accepts_near_partial_visual_source():
    status = parse_patient_follow_status(json.dumps({
        'state': 'slow',
        'session_id': 42,
        'patient_id': 'patient-001',
        'distance': 0.34,
        'source': 'partial_near',
        'qr_visible': False,
        'visual_visible': True,
    }), 42, 'patient-001')

    assert status['follow_state'] == 'slow'
    assert status['follow_distance'] == pytest.approx(0.34)
    assert status['follow_source'] == 'partial_near'


def test_patient_follow_status_rejects_other_patient_or_invalid_values():
    other_patient = json.dumps({
        'state': 'normal',
        'session_id': 42,
        'patient_id': 'patient-002',
        'distance': 0.10,
        'source': 'qr',
    })
    invalid_distance = json.dumps({
        'state': 'slow',
        'session_id': 42,
        'patient_id': 'patient-001',
        'distance': 'nan',
        'source': 'qr',
    })

    assert parse_patient_follow_status(
        other_patient, 42, 'patient-001') is None
    assert parse_patient_follow_status(
        invalid_distance, 42, 'patient-001') is None


def test_interval_gate_starts_with_a_full_wait_period():
    gate = IntervalGate(0.5, now=100.0)

    assert gate.remaining(100.0) == 0.5
    assert gate.consume(100.0) is False
    assert gate.consume(100.5) is True


def test_interval_gate_advances_even_when_caller_skips_work():
    gate = IntervalGate(0.5, now=100.0)

    # payload 없음/중복으로 HTTP 전송을 생략하는 경우에도 consume 자체가
    # 다음 실행 시각을 전진시켜 timeout=0 busy loop 를 막는다.
    assert gate.consume(100.5) is True
    assert gate.remaining(100.5) == 0.5
    assert gate.consume(100.5) is False


def test_only_content_rejections_are_discardable():
    assert send_outcome(200) == SEND_OK
    assert send_outcome(422) == SEND_REJECT
    assert send_outcome(400) == SEND_REJECT
    assert send_outcome(413) == SEND_REJECT

    # 서버가 밀리는 중. 다음 주기에 다시 보내면 된다.
    assert send_outcome(429) == SEND_RETRY
    assert send_outcome(503) == SEND_RETRY

    # URL·인증 문제는 본문 탓이 아니다. 버리면 이벤트가 통째로 사라진다.
    assert send_outcome(401) == SEND_RETRY
    assert send_outcome(404) == SEND_RETRY


class FakeServer:
    """지정한 event_id 만 거부하는 서버."""

    def __init__(self, poison, fail_after=None):
        self.poison = set(poison)
        self.fail_after = fail_after
        self.calls = 0

    def send(self, bodies):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            return SEND_RETRY
        if any(body["event_id"] in self.poison for body in bodies):
            return SEND_REJECT
        return SEND_OK


def _batch(n):
    return [(i, {"event_id": f"e{i}", "event_code": "nav.goal_reached"})
            for i in range(n)]


def test_only_the_rejected_event_is_dropped():
    batch = _batch(8)
    server = FakeServer(poison={"e5"})
    dropped, rejected = [], []

    progressed, exhausted = isolate_rejected(
        batch, server.send, dropped.extend, rejected.append, RejectBudget())

    assert (progressed, exhausted) == (True, False)
    # 8건 중 1건만 문제인데 나머지 7건이 같이 사라지면 안 된다.
    assert sorted(dropped) == list(range(8))
    assert [body["event_id"] for body in rejected] == ["e5"]


def test_multiple_rejected_events_are_each_isolated():
    batch = _batch(8)
    server = FakeServer(poison={"e0", "e7"})
    dropped, rejected = [], []

    isolate_rejected(
        batch, server.send, dropped.extend, rejected.append, RejectBudget())

    assert sorted(dropped) == list(range(8))
    assert sorted(body["event_id"] for body in rejected) == ["e0", "e7"]


def test_transient_failure_mid_isolation_keeps_the_rest_queued():
    batch = _batch(8)
    # 첫 절반은 통과하고, 그 뒤 서버가 죽는다.
    server = FakeServer(poison={"e5"}, fail_after=1)
    dropped, rejected = [], []

    progressed, exhausted = isolate_rejected(
        batch, server.send, dropped.extend, rejected.append, RejectBudget())

    # 통과한 절반만 지워지고, 나머지는 큐에 남아 다음 주기를 기다린다.
    assert (progressed, exhausted) == (True, False)
    assert sorted(dropped) == [0, 1, 2, 3]
    assert rejected == []


def test_isolation_makes_no_progress_when_the_server_is_down():
    batch = _batch(4)
    server = FakeServer(poison=set(), fail_after=0)
    dropped, rejected = [], []

    # 아무것도 못 지웠다 → 호출자가 백오프해야 한다.
    assert isolate_rejected(
        batch, server.send, dropped.extend, rejected.append,
        RejectBudget()) == (False, False)
    assert dropped == []


def test_wholesale_rejection_stops_instead_of_emptying_the_queue():
    batch = _batch(16)
    # 서버 스키마가 바뀌어 전부 거부되는 상황. 끝까지 가려내면 큐가 빈다.
    server = FakeServer(poison={f"e{i}" for i in range(16)})
    dropped, rejected = [], []

    progressed, exhausted = isolate_rejected(
        batch, server.send, dropped.extend, rejected.append, RejectBudget(3))

    assert exhausted is True
    assert len(rejected) == 3
    assert len(dropped) == 3
    # 나머지 13건은 큐에 남아 사람이 볼 때까지 보존된다.
    assert progressed is True


def test_budget_does_not_reset_between_retries_of_a_rejecting_server():
    """리뷰 지적 재현 — 서버가 계속 거부해도 큐가 조금씩 사라지면 안 된다.

    예산을 주기마다 새로 주면 매 주기 몇 건씩 버리게 되고, 백오프 상한이
    60초라 시간당 수백 건이 영구히 사라진다. 느려질 뿐 결국 큐가 비는 것은
    같다. 예산은 전송이 성공했을 때만 되돌아와야 한다.
    """
    budget = RejectBudget(3)
    server = FakeServer(poison={f'e{i}' for i in range(64)})
    dropped, rejected = [], []

    # 게이트웨이가 같은 상황으로 열 주기를 돈다.
    for _ in range(10):
        isolate_rejected(
            _batch(8), server.send, dropped.extend, rejected.append, budget)

    # 첫 주기에 예산만큼만 버리고, 그 뒤로는 한 건도 더 버리지 않는다.
    assert len(dropped) == 3
    assert budget.exhausted is True


def test_a_successful_send_restores_the_budget():
    budget = RejectBudget(3)
    budget.spend()
    budget.spend()
    budget.spend()
    assert budget.exhausted is True

    # 전송이 성공했다는 것은 서버가 정상이고 거부가 진짜 개별 이벤트
    # 문제라는 뜻이다. 그때만 다시 가려낼 수 있어야 한다.
    budget.restore()
    assert budget.exhausted is False
    assert budget.remaining == 3


def test_many_bad_events_are_still_isolated_while_the_server_works():
    # 절반이 통과하면 예산이 되돌아온다. 나쁜 이벤트가 예산보다 많이 섞여
    # 있어도, 서버가 살아 있는 한 끝까지 가려낼 수 있어야 한다.
    budget = RejectBudget(2)
    server = FakeServer(poison={'e0', 'e2', 'e4', 'e6', 'e9', 'e11'})
    dropped, rejected = [], []

    progressed, exhausted = isolate_rejected(
        _batch(16), server.send, dropped.extend, rejected.append, budget)

    assert (progressed, exhausted) == (True, False)
    assert len(rejected) == 6
    assert sorted(dropped) == list(range(16))


def test_battery_sample_goes_stale_when_the_topic_stops():
    """구독이 끊기면 캐시된 마지막 값을 계속 보내지 않는다.

    실제 사고: battery/voltage 구독이 끊긴 뒤 게이트웨이가 9시간 동안 같은
    6.76V 를 재전송했다. 서버는 수신 시각으로 recorded_at 을 찍으므로
    화면에는 '25초 전 최신값' 으로 보였다. 로봇 안에서는 계속 7.23V 였다.
    """
    # 방금 받은 표본은 신선하다.
    assert battery_is_stale(100.0, now=105.0, max_age_sec=20.0) is False
    assert battery_is_stale(100.0, now=119.9, max_age_sec=20.0) is False

    # 발행 주기(5초)를 세 번 넘겨 놓치면 낡은 것으로 본다.
    assert battery_is_stale(100.0, now=120.1, max_age_sec=20.0) is True


def test_battery_never_received_counts_as_stale():
    # 한 번도 못 받은 것과 오래된 것은 화면에서 같은 처리를 받아야 한다 —
    # 둘 다 '이 숫자를 믿지 마라' 다.
    assert battery_is_stale(None, now=1000.0) is True


def test_battery_stale_threshold_covers_three_publish_periods():
    # 발행 주기가 5초다. 한두 번 놓쳤다고 끊겼다고 보면 오탐이 된다.
    assert BATTERY_STALE_AFTER_SEC >= 15.0
