"""관제 화면이 "복귀까지 몇 초" 를 정확히 표시하는 계약을 못박는다.

로봇이 단조 시계로 계산한 실제 남은 시간을 `GuideState`로 보낸다.
관제는 이 값을 쓰므로 새로고침해도 20초로 돌아가지 않는다. 대기 시계와
`ROBOT_PAUSED` 상태의 쌍도 계속 검증한다.

    환자 대기 시계가 돌기 시작한다  <->  robot_state 가 ROBOT_PAUSED 가 된다

`_pause_for_patient` 안에서 시계를 켜는 두 갈래는 모두 그 뒤에 상태를
PAUSED 로 바꾸고, 도중에 빠져나가는 갈래는 둘 다 건드리지 않는다. 이 짝이
깨지면 화면은 아무 일도 안 일어날 시간을 예고하거나(막대가 다 비고 로봇은
그대로), 실제로 벌어지는 복귀를 예고하지 못한다.

리팩터링으로 조용히 깨지기 쉬운 짝이라 따로 둔다.
"""

import json
from types import SimpleNamespace

from mingky_guide_manager.guide_manager_node import GuideManager
from mingky_interfaces.msg import GuideState
import pytest
import rclpy
from rclpy.parameter import Parameter
from std_msgs.msg import String


class _PendingFuture:

    def add_done_callback(self, callback):
        self.callback = callback


class _GoalHandle:

    accepted = True

    def __init__(self):
        self.result_future = _PendingFuture()
        self.cancelled = 0

    def get_result_async(self):
        return self.result_future

    def cancel_goal_async(self):
        self.cancelled += 1
        return SimpleNamespace(
            add_done_callback=lambda cb: None,
            result=lambda: SimpleNamespace(goals_canceling=[object()]),
        )


class _Nav:

    def __init__(self):
        self.sent = []

    def wait_for_server(self, timeout_sec):
        return True

    def send_goal_async(self, goal, feedback_callback=None):
        self.sent.append(goal)
        return _PendingFuture()


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def node():
    guide = GuideManager(parameter_overrides=[
        Parameter('patient_follow_enabled', value=True),
        Parameter('patient_follow_timeout_sec', value=0.5),
        Parameter('patient_follow_wait_limit_sec', value=0.5),
        Parameter('use_arrival_chime', value=False),
    ])
    guide.nav = _Nav()
    guide.events.publish = lambda *args, **kwargs: None
    guide.session_id = 42
    guide.patient_id = 'patient-001'
    guide.session_state = GuideState.SESSION_GUIDING
    guide.robot_state = GuideState.ROBOT_MOVING
    yield guide
    guide.destroy_node()


def _lost() -> String:
    return String(data=json.dumps({
        'state': 'waiting',
        'session_id': 42,
        'patient_id': 'patient-001',
        'distance': 3.0,
        'source': 'qr',
    }))


def _goal(guide: GuideManager, **context_overrides) -> None:
    handle = _GoalHandle()
    context = {
        'waypoint_name': 'xray_room_goal',
        'is_dock': False,
        'is_waiting': False,
        'session_id': 42,
        'recovery_attempt': 0,
        'recovery_failures': {},
        'low_obstacle_attempts': 0,
        'low_obstacle_side': None,
    }
    context.update(context_overrides)
    guide._active_nav_goal_handle = handle
    guide._active_nav_result_future = handle.result_future
    guide._active_nav_context = context


def _clock_running(guide: GuideManager) -> bool:
    return guide._patient_wait_started_at > 0.0


def _paused(guide: GuideManager) -> bool:
    return guide.robot_state == GuideState.ROBOT_PAUSED


def test_moving_then_lost_starts_clock_and_shows_paused(node) -> None:
    """가던 중에 놓치면 둘 다 켜진다. 화면이 세도 되는 유일한 경우다."""
    _goal(node)

    node._on_patient_follow_state(_lost())

    assert _clock_running(node)
    assert _paused(node)


@pytest.mark.parametrize('setup,label', [
    (lambda guide: None, '이미 도착해 목표가 없다'),
    (lambda guide: _goal(guide, is_waiting=True), '대기 지점으로 가던 중이다'),
    (lambda guide: _goal(guide, is_dock=True), '충전소로 가던 중이다'),
])
def test_clock_never_runs_without_paused(node, setup, label) -> None:
    """시계를 안 돌리는 갈래는 상태도 안 바꾼다.

    화면은 `paused` 를 보고 셀지 말지 정하므로, 이 짝이 어긋나면 0 까지
    세고도 아무 일이 안 일어난다.
    """
    setup(node)

    node._on_patient_follow_state(_lost())

    assert not _clock_running(node), label
    assert not _paused(node), label


def test_patient_return_stops_the_clock(node) -> None:
    """환자가 돌아오면 시계는 처음부터 다시다.

    짧게 여러 번 놓친 것을 합산하면 잘 따라오는데도 안내를 접는다. 화면도
    `waiting` 이 풀리는 순간 숫자를 지우고 다음에 새로 센다.
    """
    _goal(node)
    node._on_patient_follow_state(_lost())
    assert _clock_running(node)

    node._on_patient_follow_state(String(data=json.dumps({
        'state': 'normal',
        'session_id': 42,
        'patient_id': 'patient-001',
        'distance': 1.0,
        'source': 'qr',
    })))

    assert not _clock_running(node)


def test_wait_limit_default_matches_the_dashboard(node) -> None:
    """화면이 세는 길이와 로봇이 세는 길이는 같은 값에서 와야 한다.

    화면 기본값(`waitLimitSec = 20`)은 이 선언 기본값을 따라간다. 여기를
    바꾸면 `HospitalMap3D.tsx` 도 같이 바꿔야 한다. 다르면 0 이 됐는데
    안 가거나, 아직 남았는데 가 버리는 것으로 보인다.
    """
    fresh = GuideManager(parameter_overrides=[
        Parameter('use_arrival_chime', value=False),
    ])
    try:
        assert fresh.patient_follow_wait_limit_sec == 20.0
    finally:
        fresh.destroy_node()


def test_state_reports_authoritative_wait_remaining(node, monkeypatch) -> None:
    """로봇의 시계를 실제 남은 시간으로 바꿔 보낸다."""
    published = []
    node.state_pub.publish = published.append
    node.patient_follow_wait_limit_sec = 20.0
    node._patient_wait_started_at = 100.0
    monkeypatch.setattr(
        'mingky_guide_manager.guide_manager_node.time.monotonic',
        lambda: 107.5,
    )

    node._publish_state()

    assert published[-1].patient_wait_remaining_sec == pytest.approx(12.5)


def test_state_marks_wait_clock_inactive(node) -> None:
    """대기 시계가 없으면 -1을 보내 구버전/null과 구분한다."""
    published = []
    node.state_pub.publish = published.append
    node._patient_wait_started_at = 0.0

    node._publish_state()

    assert published[-1].patient_wait_remaining_sec == pytest.approx(-1.0)
