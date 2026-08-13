"""관제 명령을 로봇 책임 경로로 보내는 계약."""

from mingky_event_gateway.gateway_node import (
    ACTIVE_GUIDE_SESSION_STATES,
    HeartbeatFailureGuard,
    IntervalGate,
    SEND_OK,
    SEND_REJECT,
    SEND_RETRY,
    SYSTEM_COMMANDS,
    isolate_rejected,
    matches_guided_patient,
    send_outcome,
)
from mingky_interfaces.msg import GuideState, QrObservation


def test_system_commands_only_target_fixed_systemd_actions():
    assert SYSTEM_COMMANDS == {
        'system_start': 'start',
        'system_stop': 'stop',
        'system_restart': 'restart',
    }


def test_active_guidance_states_cover_confirmation_through_room_waiting():
    assert ACTIVE_GUIDE_SESSION_STATES == (
        GuideState.SESSION_CONFIRMED,
        GuideState.SESSION_GUIDING,
        GuideState.SESSION_ARRIVED,
        GuideState.SESSION_IN_ROOM,
    )


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
        batch, server.send, dropped.extend, rejected.append)

    assert (progressed, exhausted) == (True, False)
    # 8건 중 1건만 문제인데 나머지 7건이 같이 사라지면 안 된다.
    assert sorted(dropped) == list(range(8))
    assert [body["event_id"] for body in rejected] == ["e5"]


def test_multiple_rejected_events_are_each_isolated():
    batch = _batch(8)
    server = FakeServer(poison={"e0", "e7"})
    dropped, rejected = [], []

    isolate_rejected(batch, server.send, dropped.extend, rejected.append)

    assert sorted(dropped) == list(range(8))
    assert sorted(body["event_id"] for body in rejected) == ["e0", "e7"]


def test_transient_failure_mid_isolation_keeps_the_rest_queued():
    batch = _batch(8)
    # 첫 절반은 통과하고, 그 뒤 서버가 죽는다.
    server = FakeServer(poison={"e5"}, fail_after=1)
    dropped, rejected = [], []

    progressed, exhausted = isolate_rejected(
        batch, server.send, dropped.extend, rejected.append)

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
        batch, server.send, dropped.extend, rejected.append) == (False, False)
    assert dropped == []


def test_wholesale_rejection_stops_instead_of_emptying_the_queue():
    batch = _batch(16)
    # 서버 스키마가 바뀌어 전부 거부되는 상황. 끝까지 가려내면 큐가 빈다.
    server = FakeServer(poison={f"e{i}" for i in range(16)})
    dropped, rejected = [], []

    progressed, exhausted = isolate_rejected(
        batch, server.send, dropped.extend, rejected.append, max_rejects=3)

    assert exhausted is True
    assert len(rejected) == 3
    assert len(dropped) == 3
    # 나머지 13건은 큐에 남아 사람이 볼 때까지 보존된다.
    assert progressed is True
