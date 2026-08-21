"""군집 조정 양보 — 관제가 세우고 풀 때 로봇이 무엇을 하는가.

여기서 지키는 계약은 넷이다.

  1. hold 를 받으면 목표를 취소하고 **컨텍스트를 보관**한다 (재개하면 그 자리로)
  2. **데드맨** — 판정이 끊기면 스스로 풀고 주행을 재개한다
  3. 환자 대기와 겹치면 **둘 다 풀려야** 출발한다
  4. 비상정지·저전압·화재대피가 군집 조정을 이긴다

2번이 이 기능에서 가장 중요한 시험이다. 없으면 서버 버그 하나가 안내 중인
로봇을 영구히 세워 놓는다. 조정층은 안전장치가 아니라 교착 예방층이므로,
조정이 사라지면 이 기능을 붙이기 전 동작으로 돌아가야 한다.
"""

import json
import time
from types import SimpleNamespace

from mingky_guide_manager.guide_manager_node import GuideManager
from mingky_interfaces.msg import GuideState
import pytest
import rclpy
from rclpy.parameter import Parameter
from std_msgs.msg import String


class PendingFuture:

    def __init__(self):
        self.callback = None

    def add_done_callback(self, callback):
        self.callback = callback


class ImmediateFuture:

    def __init__(self, value):
        self.value = value

    def result(self):
        return self.value

    def add_done_callback(self, callback):
        callback(self)


class FakeGoalHandle:

    accepted = True

    def __init__(self):
        self.result_future = PendingFuture()
        self.cancelled = 0

    def get_result_async(self):
        return self.result_future

    def cancel_goal_async(self):
        self.cancelled += 1
        return ImmediateFuture(SimpleNamespace(goals_canceling=[object()]))


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
        Parameter('patient_follow_enabled', value=True),
        Parameter('patient_follow_timeout_sec', value=0.5),
        Parameter('patient_follow_wait_limit_sec', value=30.0),
        Parameter('use_arrival_chime', value=False),
    ])
    node.nav = FakeNav()
    events = []
    node.events.publish = lambda code, payload=None, session_id=0, level=None: (
        events.append((code, payload or {}, session_id)))
    node.session_id = 42
    node.patient_id = 'patient-001'
    node.session_state = GuideState.SESSION_GUIDING
    node.robot_state = GuideState.ROBOT_MOVING
    yield node, events
    node.destroy_node()


def _active_goal(node: GuideManager, waypoint='xray_room_goal'):
    handle = FakeGoalHandle()
    node._active_nav_goal_handle = handle
    node._active_nav_result_future = handle.result_future
    node._active_nav_context = {
        'waypoint_name': waypoint,
        'is_dock': False,
        'session_id': 42,
        'is_waiting': False,
        'recovery_attempt': 0,
        'recovery_failures': {},
        'low_obstacle_attempts': 0,
        'low_obstacle_side': None,
    }
    return handle


def _decision(proceed: bool, *, reason='peer_in_segment', peer='pinky-02',
              segments=('seg-4',), ttl=5.0) -> String:
    return String(data=json.dumps({
        'type': 'decision',
        'proceed': proceed,
        'reason': reason,
        'blocked_by': peer,
        'segments': list(segments),
        'ttl_sec': ttl,
    }))


def _codes(events):
    return [code for code, _, _ in events]


# ------------------------------------------------------------------ 계약 1

def test_hold_cancels_goal_and_keeps_the_context(manager):
    node, events = manager
    handle = _active_goal(node)

    node._on_fleet_decision(_decision(False))

    assert handle.cancelled == 1
    assert node.robot_state == GuideState.ROBOT_PAUSED
    assert node._hold_sources == {'fleet'}
    # 재개할 자리를 그대로 들고 있어야 한다.
    assert node._patient_wait_context['waypoint_name'] == 'xray_room_goal'
    assert 'fleet.yield_started' in _codes(events)
    payload = next(p for c, p, _ in events if c == 'fleet.yield_started')
    assert payload['segment'] == 'seg-4'
    assert payload['peer'] == 'pinky-02'


def test_release_resends_the_original_goal(manager):
    node, events = manager
    handle = _active_goal(node)
    node._on_fleet_decision(_decision(False))
    handle.result_future.callback(ImmediateFuture(
        SimpleNamespace(status=5, result=SimpleNamespace(error_code=0))))

    node._on_fleet_decision(_decision(True, reason='cleared'))

    assert node._hold_sources == set()
    assert node.nav.sent, '원래 목표를 다시 보내지 않았다'
    assert 'fleet.yield_ended' in _codes(events)


# ------------------------------------------------------------------ 계약 2

def test_deadman_releases_when_decisions_stop(manager):
    """서버가 죽어도 로봇이 영구히 서 있으면 안 된다."""
    node, events = manager
    handle = _active_goal(node)
    node._on_fleet_decision(_decision(False, ttl=0.2))
    handle.result_future.callback(ImmediateFuture(
        SimpleNamespace(status=5, result=SimpleNamespace(error_code=0))))
    assert node._hold_sources == {'fleet'}

    # 아직 ttl 안이다 — 풀리면 안 된다.
    node._check_fleet_deadman()
    assert node._hold_sources == {'fleet'}

    time.sleep(0.25)
    node._check_fleet_deadman()

    assert node._hold_sources == set()
    assert node.nav.sent, '데드맨으로 풀렸는데 주행을 재개하지 않았다'
    ended = next(p for c, p, _ in events if c == 'fleet.yield_ended')
    # 사유가 남아야 "조정이 사실상 꺼져 있다" 를 나중에 셀 수 있다.
    assert ended['reason'] == 'deadman'


def test_no_ttl_means_no_deadman(manager):
    """조정 꺼짐 통지(ttl 없음)에는 데드맨을 걸지 않는다."""
    node, _ = manager
    _active_goal(node)
    node._on_fleet_decision(_decision(True, ttl=None))

    assert node._fleet_ttl_sec == 0.0
    node._check_fleet_deadman()          # 아무 일도 없어야 한다
    assert node._hold_sources == set()


# ------------------------------------------------------------------ 계약 3

def test_patient_wait_and_fleet_hold_must_both_clear(manager):
    """한쪽만 풀렸다고 출발하면 다른 쪽 이유가 그대로인데 움직인다."""
    node, _ = manager
    handle = _active_goal(node)

    node._pause_for_patient(
        reason='patient_distance', distance=0.4, source='qr')
    handle.result_future.callback(ImmediateFuture(
        SimpleNamespace(status=5, result=SimpleNamespace(error_code=0))))
    node._on_fleet_decision(_decision(False))
    assert node._hold_sources == {'patient', 'fleet'}

    node._on_fleet_decision(_decision(True))
    assert node._hold_sources == {'patient'}
    assert not node.nav.sent, '환자가 아직 안 왔는데 출발했다'

    node._resume_for_patient(source='patient_returned')
    assert node._hold_sources == set()
    assert node.nav.sent, '둘 다 풀렸는데 출발하지 않았다'


# ------------------------------------------------------------------ 계약 4

@pytest.mark.parametrize('flag', [
    '_battery_alarm', '_emergency_engaged', '_fire_evacuating'])
def test_safety_states_outrank_fleet_hold(manager, flag):
    node, _ = manager
    _active_goal(node)
    setattr(node, flag, True)

    node._on_fleet_decision(_decision(False))

    assert node._hold_sources == set(), f'{flag} 중에 군집 조정이 끼어들었다'


def test_session_end_clears_the_hold(manager):
    """다음 세션이 이전 세션의 이유 때문에 못 나가면 안 된다."""
    node, _ = manager
    _active_goal(node)
    node._on_fleet_decision(_decision(False))
    assert node._hold_sources == {'fleet'}

    node._cancel_patient_wait_state()

    assert node._hold_sources == set()
    assert node._fleet_ttl_sec == 0.0


# ------------------------------------------------------------------ 목표 보고

def test_intent_is_published_and_deduplicated(manager):
    node, _ = manager
    sent = []
    node.fleet_intent_pub.publish = lambda msg: sent.append(json.loads(msg.data))
    _active_goal(node)

    node._publish_fleet_intent()
    node._publish_fleet_intent()          # 같은 값은 다시 안 보낸다

    assert len(sent) == 1
    assert sent[0] == {'goal_waypoint': 'xray_room_goal', 'guiding': True}


def test_intent_keeps_the_goal_while_held(manager):
    """세워 둔 동안에도 목표를 계속 말해야 구간을 뺏기지 않는다."""
    node, _ = manager
    sent = []
    node.fleet_intent_pub.publish = lambda msg: sent.append(json.loads(msg.data))
    handle = _active_goal(node)

    node._on_fleet_decision(_decision(False))
    handle.result_future.callback(ImmediateFuture(
        SimpleNamespace(status=5, result=SimpleNamespace(error_code=0))))
    node._publish_fleet_intent()

    assert sent[-1]['goal_waypoint'] == 'xray_room_goal'


def test_malformed_decision_is_ignored(manager):
    """깨진 프레임에 로봇이 멈추거나 죽으면 안 된다."""
    node, _ = manager
    _active_goal(node)

    node._on_fleet_decision(String(data='{}'))
    node._on_fleet_decision(String(data='json 아님'))

    assert node._hold_sources == set()


# ------------------------------------------------------- 검사실 순서 재정렬

def _next_visit(step_order, *, skipped='X-ray', peer='pinky-02',
                reordered=True) -> String:
    return String(data=json.dumps({
        'type': 'decision', 'proceed': True, 'reason': None,
        'blocked_by': None, 'segments': [], 'ttl_sec': 5.0,
        'next_visit': {
            'step_order': step_order, 'visit_name': None,
            'reordered': reordered, 'skipped_visit': skipped,
            'blocked_by': peer,
        },
    }))


def _in_room(node, visits, current):
    """검사실에 도착해 완료 QR 을 기다리는 상태로 만든다."""
    node.session_visits = list(visits)
    node._completed_orders = set(range(1, current))
    node.current_step_order = current
    node.current_visit = visits[current - 1]
    node.session_state = GuideState.SESSION_IN_ROOM
    node.robot_state = GuideState.ROBOT_WAITING


def test_control_can_reorder_the_next_room(manager):
    """시드가 만드는 상황 — 둘 다 X-ray 부터라 뒤 환자가 CT 를 먼저 간다."""
    node, events = manager
    _in_room(node, ['X-ray', 'CT', 'MRI'], current=1)
    node._on_fleet_decision(_next_visit(2))      # 관제: CT 먼저

    node._complete_current_step_from_qr()

    assert node.current_step_order == 2
    assert node.current_visit == 'CT'
    assert node.nav.sent, '다음 목적지로 출발하지 않았다'


def test_reordered_session_still_finishes_every_step(manager):
    """건너뛴 단계로 반드시 돌아와야 한다. 안 그러면 검사를 빠뜨린다."""
    node, _ = manager
    _in_room(node, ['X-ray', 'CT'], current=1)
    node._on_fleet_decision(_next_visit(2))
    node._complete_current_step_from_qr()        # X-ray 완료 → CT 로

    # CT 에 도착해 완료. 이제 남은 것은 건너뛴 X-ray 가 아니라... 없다.
    # (X-ray 는 이미 1번에서 완료됐다)
    node.session_state = GuideState.SESSION_IN_ROOM
    node.robot_state = GuideState.ROBOT_WAITING
    node._on_fleet_decision(_next_visit(0, reordered=False))
    node._complete_current_step_from_qr()

    assert node.session_state == GuideState.SESSION_NONE, '세션이 안 끝났다'


def test_skipping_then_returning_visits_every_room(manager):
    """1번을 건너뛰고 2번을 먼저 간 뒤, 1번으로 돌아온다."""
    node, _ = manager
    # 2번(CT)을 먼저 하는 상황을 만든다: 아직 아무것도 완료 안 됨
    node.session_visits = ['X-ray', 'CT']
    node._completed_orders = set()
    node.current_step_order = 2                  # CT 에 와 있다
    node.current_visit = 'CT'
    node.session_state = GuideState.SESSION_IN_ROOM
    node.robot_state = GuideState.ROBOT_WAITING

    # CT 완료 → 남은 것은 X-ray 뿐이다
    node._on_fleet_decision(_next_visit(0, reordered=False))
    node._complete_current_step_from_qr()

    assert node.current_step_order == 1
    assert node.current_visit == 'X-ray', '건너뛴 검사실로 안 돌아왔다'
    assert node.session_state != GuideState.SESSION_NONE


def test_stale_suggestion_is_ignored(manager):
    """방금 끝낸 단계를 관제가 아직 못 봤을 때 그리로 다시 가면 안 된다."""
    node, _ = manager
    _in_room(node, ['X-ray', 'CT'], current=1)
    node._on_fleet_decision(_next_visit(1))      # 관제: X-ray (이미 끝난 것)

    node._complete_current_step_from_qr()

    assert node.current_step_order == 2
    assert node.current_visit == 'CT'


def test_without_control_the_plan_order_is_kept(manager):
    """관제가 없으면 이 기능을 붙이기 전 동작으로 떨어진다."""
    node, _ = manager
    _in_room(node, ['X-ray', 'CT', 'MRI'], current=1)

    node._complete_current_step_from_qr()        # 판정을 한 번도 안 받았다

    assert node.current_step_order == 2
    assert node.current_visit == 'CT'


def test_out_of_range_suggestion_is_ignored(manager):
    node, _ = manager
    _in_room(node, ['X-ray', 'CT'], current=1)
    node._on_fleet_decision(_next_visit(99))

    node._complete_current_step_from_qr()

    assert node.current_step_order == 2
