"""관제 명령을 로봇 책임 경로로 보내는 계약."""

from mingky_event_gateway.gateway_node import (
    ACTIVE_GUIDE_SESSION_STATES,
    HeartbeatFailureGuard,
    IntervalGate,
    SYSTEM_COMMANDS,
    matches_guided_patient,
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
