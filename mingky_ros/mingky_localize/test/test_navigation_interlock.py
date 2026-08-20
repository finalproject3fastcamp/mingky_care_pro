"""자동 재탐색과 환자 안내 사이의 안전 잠금."""

import math
from types import SimpleNamespace

from mingky_interfaces.msg import GuideState
from mingky_localize.auto_localize_node import (
    _guide_session_active,
    AUTO_MODE,
    AutoLocalizeNode,
)
from mingky_localize.global_matcher import PoseHypothesis
from nav2_msgs.srv import ManageLifecycleNodes
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


def test_navigation_lifecycle_is_reset_before_restart_after_initial_pose():
    node = AutoLocalizeNode.__new__(AutoLocalizeNode)
    readiness = iter((False, True))
    checked = []
    commands = []

    def wait_until_active(names, timeout_sec):
        checked.append((names, timeout_sec))
        return next(readiness)

    def call_lifecycle(command, timeout_sec):
        commands.append((command, timeout_sec))
        return True

    node._wait_until_active = wait_until_active
    node._call_navigation_lifecycle = call_lifecycle
    node.get_logger = lambda: type(
        'Logger', (), {'info': lambda self, message: None})()

    assert node._ensure_navigation_active() is True
    assert commands == [
        (ManageLifecycleNodes.Request.RESET, 20.0),
        (ManageLifecycleNodes.Request.STARTUP, 80.0),
    ]
    assert checked[0][0] == ('planner_server', 'bt_navigator')
    assert checked[-1][0] == ('planner_server', 'bt_navigator')


def test_active_navigation_lifecycle_is_not_restarted():
    node = AutoLocalizeNode.__new__(AutoLocalizeNode)
    node._wait_until_active = lambda names, timeout_sec: True

    def unexpected_restart(command, timeout_sec):
        raise AssertionError('active navigation must not be restarted')

    node._call_navigation_lifecycle = unexpected_restart

    assert node._ensure_navigation_active() is True


def test_seed_acceptance_uses_pre_publish_particle_timestamp(monkeypatch):
    node = AutoLocalizeNode.__new__(AutoLocalizeNode)
    hypothesis = PoseHypothesis(1.0, 2.0, 0.1, 0.9)
    node.matcher_seed_timeout_sec = 0.2
    node.matcher_seed_confirmations = 1
    node.matcher_seed_spread_m = 0.15
    node.matcher_seed_yaw_spread_rad = math.radians(20.0)
    node.matcher_seed_pose_tolerance_m = 0.20
    node.matcher_seed_yaw_tolerance_rad = math.radians(20.0)
    node._particles_updated_at = 11.0
    node._particles = [
        (1.02, 2.01, 0.11),
        (0.98, 1.99, 0.09),
    ]
    node._localization_abort_reason = lambda: None
    monkeypatch.setattr(
        'mingky_localize.auto_localize_node.rclpy.ok', lambda: True)

    assert node._wait_for_seed_acceptance(
        hypothesis, updated_after=10.0) is True


def test_inactive_localizer_does_not_expand_particle_cloud():
    node = AutoLocalizeNode.__new__(AutoLocalizeNode)
    node._busy = False
    node._particles = [('existing',)]
    node._particles_updated_at = 10.0
    message = SimpleNamespace()

    node._on_particles(message)

    assert node._particles == [('existing',)]
    assert node._particles_updated_at == 10.0


def test_active_localizer_keeps_particle_updates(monkeypatch):
    node = AutoLocalizeNode.__new__(AutoLocalizeNode)
    node._busy = True
    node._particles = None
    node._particles_updated_at = None
    particle = SimpleNamespace(pose=SimpleNamespace(
        position=SimpleNamespace(x=1.2, y=-0.3),
        orientation=SimpleNamespace(z=0.0, w=1.0),
    ))
    monkeypatch.setattr(
        'mingky_localize.auto_localize_node.time.monotonic', lambda: 20.0)

    node._on_particles(SimpleNamespace(particles=[particle]))

    assert node._particles == [(1.2, -0.3, 0.0)]
    assert node._particles_updated_at == 20.0
