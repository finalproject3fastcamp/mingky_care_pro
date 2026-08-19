"""자동 재탐색과 환자 안내 사이의 안전 잠금."""

import math

from mingky_interfaces.msg import GuideState
from mingky_localize.auto_localize_node import (
    _guide_session_active,
    AUTO_MODE,
    AutoLocalizeNode,
)
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


def test_emergency_stop_aborts_localization_even_in_auto_mode():
    node = AutoLocalizeNode.__new__(AutoLocalizeNode)
    node.mode = AUTO_MODE
    node._emergency_stopped = True
    node._nav_goal_active = lambda: False

    reason = node._localization_abort_reason()

    assert reason[0] == 'emergency_stop'


def test_relative_odom_is_expressed_in_previous_robot_frame():
    previous = (1.0, 2.0, math.pi / 2.0)
    current = (1.0, 2.10, math.pi / 2.0)

    dx, dy, dyaw = AutoLocalizeNode._relative_odom(previous, current)

    assert dx == pytest.approx(0.10)
    assert dy == pytest.approx(0.0, abs=1e-9)
    assert dyaw == pytest.approx(0.0)
