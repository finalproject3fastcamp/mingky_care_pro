"""세션 판정 회귀 테스트.

시간 판정은 `test_dwell_policy.py` 가 덮는다. 여기서 보는 것은 **언제
개입하면 안 되는가** 다. 잘못 끼어들면 멀쩡한 안내가 끊기고, 그건 로봇이
멈춰 서 있는 것보다 나쁘다.
"""

import json

from mingky_interfaces.msg import GuideState
from mingky_dwell_guard.dwell_guard_node import DwellGuardNode
import pytest
import rclpy
from std_msgs.msg import String


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def node():
    """취소 발행을 가로채고, 시계를 손으로 돌릴 수 있게 만든 노드."""
    instance = DwellGuardNode()
    sent = []
    instance._cancel_pub.publish = lambda msg: sent.append(json.loads(msg.data))

    clock = {'t': 1000.0}
    instance._now = lambda: clock['t']

    yield instance, sent, clock
    instance.destroy_node()


def guiding(session_id=42):
    return GuideState(
        session_id=session_id,
        session_state=GuideState.SESSION_GUIDING,
        robot_state=GuideState.ROBOT_MOVING,
    )


def ended():
    return GuideState(
        session_id=0,
        session_state=GuideState.SESSION_NONE,
        robot_state=GuideState.ROBOT_IDLE,
    )


def arrived(session_id=42):
    return GuideState(
        session_id=session_id,
        session_state=GuideState.SESSION_ARRIVED,
        robot_state=GuideState.ROBOT_WAITING,
    )


def wait_for(node, clock, seconds, step=0.5):
    """`seconds` 만큼 시간을 흘리며 tick 을 돈다."""
    end = clock['t'] + seconds
    while clock['t'] < end:
        clock['t'] += step
        node._tick()


def test_안내_중에_오래_기다리면_취소한다(node):
    n, sent, clock = node
    n._on_guide_state(guiding())
    n._on_follow_state(String(data='waiting'))
    wait_for(n, clock, n._timer_state._policy.timeout_sec + 2)
    assert len(sent) == 1
    assert sent[0] == {'reason': 'aborted', 'session_id': 42}


def test_시간이_지나도_한_번만_보낸다(node):
    """상태는 계속 waiting 이다. 매번 보내면 초당 수십 번 나간다."""
    n, sent, clock = node
    n._on_guide_state(guiding())
    n._on_follow_state(String(data='waiting'))
    wait_for(n, clock, n._timer_state._policy.timeout_sec + 60)
    assert len(sent) == 1


def test_안내_중이_아니면_끼어들지_않는다(node):
    """도착해서 검사실에 있는 동안에도 환자는 로봇 뒤에 없다.

    그때 waiting 이 뜬다고 안내를 취소하면 멀쩡한 세션이 끊긴다.
    """
    n, sent, clock = node
    n._on_guide_state(arrived())
    n._on_follow_state(String(data='waiting'))
    wait_for(n, clock, n._timer_state._policy.timeout_sec + 30)
    assert sent == []


def test_세션이_없으면_끼어들지_않는다(node):
    n, sent, clock = node
    n._on_guide_state(ended())
    n._on_follow_state(String(data='waiting'))
    wait_for(n, clock, n._timer_state._policy.timeout_sec + 30)
    assert sent == []


def test_상태를_한_번도_못_받으면_아무것도_안_한다(node):
    """guide_manager 가 떠 있지 않은 상태. 모르면 개입하지 않는다."""
    n, sent, clock = node
    n._on_follow_state(String(data='waiting'))
    wait_for(n, clock, n._timer_state._policy.timeout_sec + 30)
    assert sent == []


def test_기다리는_중에_세션이_끝나면_취소가_안_나간다(node):
    """이미 끝난 세션을 다시 취소할 이유가 없다."""
    n, sent, clock = node
    n._on_guide_state(guiding())
    n._on_follow_state(String(data='waiting'))
    wait_for(n, clock, n._timer_state._policy.timeout_sec - 2)
    n._on_guide_state(ended())
    wait_for(n, clock, 60)
    assert sent == []


def test_새_세션은_이전_대기를_물려받지_않는다(node):
    """물려받으면 새 안내가 시작하자마자 취소된다.

    이전 세션에서 거의 다 채운 상태로 끝나고 새 세션이 바로 시작되는 것은
    실제로 일어나는 흐름이다 -- 환자가 안내를 포기하고 다음 환자가 오는 경우다.
    """
    n, sent, clock = node
    timeout = n._timer_state._policy.timeout_sec

    n._on_guide_state(guiding(5))
    n._on_follow_state(String(data='waiting'))
    wait_for(n, clock, timeout - 2)      # 거의 다 채웠다
    n._on_guide_state(ended())
    wait_for(n, clock, 2)
    n._on_guide_state(guiding(6))        # 새 세션, 여전히 waiting

    wait_for(n, clock, timeout - 2)      # 새 세션 기준으로는 아직 모자라다
    assert sent == [], '새 세션이 이전 대기를 물려받았다'

    wait_for(n, clock, 4)
    assert len(sent) == 1
    assert sent[0]['session_id'] == 6


def test_환자가_돌아오면_처음부터_다시_센다(node):
    n, sent, clock = node
    timeout = n._timer_state._policy.timeout_sec

    n._on_guide_state(guiding())
    n._on_follow_state(String(data='waiting'))
    wait_for(n, clock, timeout - 2)
    n._on_follow_state(String(data='normal'))
    wait_for(n, clock, 2)
    n._on_follow_state(String(data='waiting'))

    wait_for(n, clock, timeout - 2)
    assert sent == []
    wait_for(n, clock, 4)
    assert len(sent) == 1


def test_꺼두면_아무것도_안_한다():
    n = DwellGuardNode()
    try:
        sent = []
        n._cancel_pub.publish = lambda msg: sent.append(msg)
        n._enabled = False
        clock = {'t': 1000.0}
        n._now = lambda: clock['t']
        n._on_guide_state(guiding())
        n._on_follow_state(String(data='waiting'))
        wait_for(n, clock, n._timer_state._policy.timeout_sec + 60)
        assert sent == []
    finally:
        n.destroy_node()
