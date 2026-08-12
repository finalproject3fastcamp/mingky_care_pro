"""관제 명령을 로봇 책임 경로로 보내는 계약."""

from mingky_event_gateway.gateway_node import (
    ACTIVE_GUIDE_SESSION_STATES,
    HeartbeatFailureGuard,
    SYSTEM_COMMANDS,
)
from mingky_interfaces.msg import GuideState


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
