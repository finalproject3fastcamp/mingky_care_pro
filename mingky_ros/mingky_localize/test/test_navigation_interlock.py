"""자동 재탐색과 환자 안내 사이의 안전 잠금."""

from mingky_interfaces.msg import GuideState
from mingky_localize.auto_localize_node import _guide_session_active
import pytest


@pytest.mark.parametrize('state', [
    GuideState.SESSION_CONFIRMED,
    GuideState.SESSION_GUIDING,
    GuideState.SESSION_ARRIVED,
    GuideState.SESSION_IN_ROOM,
])
def test_entire_active_guidance_session_blocks_localization(state):
    assert _guide_session_active(42, state) is True


@pytest.mark.parametrize('state', [
    GuideState.SESSION_NONE,
    GuideState.SESSION_COMPLETED,
])
def test_inactive_guidance_session_does_not_block_localization(state):
    assert _guide_session_active(0, state) is False
