"""관제 명령을 로봇 책임 경로로 보내는 계약."""

from mingky_event_gateway.gateway_node import (
    ACTIVE_GUIDE_SESSION_STATES,
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
