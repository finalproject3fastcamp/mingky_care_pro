"""전체 위치 관측 채널.

여기서 지키는 계약은 셋이다.

  1. 로봇이 올린 위치를 서버가 **갖는다** (지금까지는 스쳐 지나갔다)
  2. 위치를 가로채도 **조작자 중계는 그대로다**
  3. 관측은 `control_audit` 에 아무것도 남기지 않는다

3번이 이 채널이 따로 있는 이유다. 조작자 소켓은 붙는 순간 `teleop_attach`
를 남기고 그것이 `INTERVENTION_ACTIONS` 라, 관측이 같은 기록을 남기면 화면을
열어둔 것만으로 세션이 SLO 실패로 판정된다 (`app/fleet_pose.py`).
"""

import asyncio
import json

import pytest

from app import control_audit, fleet_pose
from app.routers import fleet_poses, teleop


def setup_function():
    fleet_pose.reset()


def teardown_function():
    fleet_pose.reset()


# ------------------------------------------------------------------- 상태

def test_update_keeps_latest_pose_per_robot():
    fleet_pose.update('pinky-01', 1.0, 2.0, 0.5)
    fleet_pose.update('pinky-02', -3.0, 4.0, -1.5)
    fleet_pose.update('pinky-01', 1.5, 2.5, 0.75)

    snapshot = fleet_pose.snapshot()

    assert set(snapshot) == {'pinky-01', 'pinky-02'}
    assert snapshot['pinky-01'].x == pytest.approx(1.5)
    assert snapshot['pinky-01'].yaw == pytest.approx(0.75)
    # 한 로봇의 갱신이 다른 로봇을 건드리면 두 대를 동시에 못 본다.
    assert snapshot['pinky-02'].x == pytest.approx(-3.0)


def test_unknown_robot_has_no_pose():
    """모르는 것과 (0,0) 은 다르다. AMCL 이 실제로 (0,0) 에서 시작한다."""
    assert fleet_pose.get('pinky-01') is None


def test_snapshot_message_is_sorted_and_serializable():
    fleet_pose.update('pinky-02', 0.1, 0.2, 0.3)
    fleet_pose.update('pinky-01', 0.4, 0.5, 0.6)

    message = fleet_pose.snapshot_message()

    assert message['type'] == 'snapshot'
    assert [p['robot_id'] for p in message['poses']] == ['pinky-01', 'pinky-02']
    # 화면으로 나가는 프레임이므로 그대로 JSON 이 되어야 한다.
    assert json.loads(json.dumps(message))['poses'][0]['x'] == pytest.approx(0.4)


def test_observed_at_is_server_time_not_robot_time():
    """전송 지연을 숨기지 않는다. 화면의 나이 판정이 이 값 위에 선다."""
    sample = fleet_pose.update('pinky-01', 0.0, 0.0, 0.0)
    message = fleet_pose.as_message('pinky-01', sample)

    assert message['observed_at'] == sample.observed_at.isoformat()
    assert sample.observed_at.tzinfo is not None


# ------------------------------------------------------------------- 관전자

def test_subscriber_receives_updates():
    async def scenario():
        queue = fleet_pose.subscribe()
        try:
            fleet_pose.update('pinky-01', 1.0, 2.0, 0.0)
            return await asyncio.wait_for(queue.get(), timeout=1)
        finally:
            fleet_pose.unsubscribe(queue)

    message = asyncio.run(scenario())

    assert message['type'] == 'pose'
    assert message['robot_id'] == 'pinky-01'
    assert message['x'] == pytest.approx(1.0)


def test_slow_watcher_drops_oldest_and_never_blocks():
    """느린 관전자 하나가 로봇 수신을 멈추면 안 된다.

    `update()` 는 동기 함수이고 절대 기다리지 않는다. 큐가 넘치면 오래된
    좌표부터 버린다 — 위치는 마지막 값만 의미가 있다.
    """
    async def scenario():
        queue = fleet_pose.subscribe()
        try:
            overflow = fleet_pose.WATCHER_QUEUE_SIZE + 5
            for step in range(overflow):
                fleet_pose.update('pinky-01', float(step), 0.0, 0.0)

            assert queue.qsize() == fleet_pose.WATCHER_QUEUE_SIZE
            drained = [queue.get_nowait() for _ in range(queue.qsize())]
            # 마지막 좌표는 살아 있어야 한다. 그게 지금 위치다.
            assert drained[-1]['x'] == pytest.approx(float(overflow - 1))
            # 버려진 쪽은 오래된 것이다.
            assert drained[0]['x'] > 0
        finally:
            fleet_pose.unsubscribe(queue)

    asyncio.run(scenario())


def test_unsubscribe_removes_watcher():
    async def scenario():
        queue = fleet_pose.subscribe()
        assert fleet_pose.watcher_count() == 1
        fleet_pose.unsubscribe(queue)
        assert fleet_pose.watcher_count() == 0

        # 끊긴 관전자에게는 더 이상 쌓이지 않는다.
        fleet_pose.update('pinky-01', 1.0, 1.0, 1.0)
        assert queue.empty()

    asyncio.run(scenario())


# ------------------------------------------------------------------- 중계

class FakeSocket:
    """teleop 중계가 쓰는 만큼만 흉내낸다."""

    def __init__(self, incoming: list[str]):
        self._incoming = list(incoming)
        self.sent: list[str] = []

    async def accept(self):
        pass

    async def receive_text(self) -> str:
        if not self._incoming:
            raise teleop.WebSocketDisconnect(code=1000)
        return self._incoming.pop(0)

    async def send_text(self, message: str):
        self.sent.append(message)

    async def close(self):
        pass


def test_pose_is_recorded_and_still_relayed_to_operators():
    """가로채기가 중계를 대신하지 않는다. 조작자 화면은 그대로 받아야 한다."""
    pose = json.dumps({'type': 'pose', 'x': 1.25, 'y': -0.5, 'yaw': 0.25})
    diag = json.dumps({'type': 'scan', 'points': [[0.0, 1.0]]})
    operator = FakeSocket([])
    robot = FakeSocket([pose, diag])

    teleop._operators.setdefault('pinky-01', set()).add(operator)
    try:
        asyncio.run(teleop.robot_socket(robot, 'pinky-01'))
    finally:
        teleop._operators.pop('pinky-01', None)
        teleop._robots.pop('pinky-01', None)

    # 1. 서버가 위치를 갖는다.
    sample = fleet_pose.get('pinky-01')
    assert sample is not None
    assert sample.x == pytest.approx(1.25)
    assert sample.yaw == pytest.approx(0.25)

    # 2. 조작자는 두 프레임을 원본 그대로 받는다.
    assert operator.sent == [pose, diag]


def test_nan_pose_is_dropped_not_stored():
    """NaN 은 브라우저의 JSON.parse 가 거부한다. 여기서 막지 않으면 그 프레임이
    화면에서 통째로 사라지고 원인이 지도에는 안 보인다."""
    nan = '{"type": "pose", "x": NaN, "y": 1.0, "yaw": 0.0}'
    good = json.dumps({'type': 'pose', 'x': 7.0, 'y': 1.0, 'yaw': 0.0})
    robot = FakeSocket([nan, good])

    try:
        asyncio.run(teleop.robot_socket(robot, 'pinky-01'))
    finally:
        teleop._robots.pop('pinky-01', None)

    sample = fleet_pose.get('pinky-01')
    assert sample is not None and sample.x == pytest.approx(7.0)


def test_malformed_frame_does_not_break_the_relay():
    """깨진 프레임에 중계가 끊기면 그 로봇에 조작이 안 닿는다."""
    broken = '{"type": "pose", "x": "여기"}'
    good = json.dumps({'type': 'pose', 'x': 2.0, 'y': 3.0, 'yaw': 0.0})
    operator = FakeSocket([])
    robot = FakeSocket([broken, 'not json at all', good])

    teleop._operators.setdefault('pinky-01', set()).add(operator)
    try:
        asyncio.run(teleop.robot_socket(robot, 'pinky-01'))
    finally:
        teleop._operators.pop('pinky-01', None)
        teleop._robots.pop('pinky-01', None)

    assert len(operator.sent) == 3
    sample = fleet_pose.get('pinky-01')
    assert sample is not None and sample.x == pytest.approx(2.0)


# ------------------------------------------------------------------- 감사

def test_watching_records_no_control_audit(monkeypatch):
    """이 채널이 따로 있는 이유. 관측은 개입이 아니다.

    조작자 소켓과 달리 여기에는 `actor` 의존성도 감사 기록도 없어야 한다.
    없는 것은 코드를 읽어도 눈에 안 띄므로 테스트가 대신 지킨다.
    """
    recorded = []

    async def spy(*args, **kwargs):
        recorded.append((args, kwargs))
        return True

    monkeypatch.setattr(control_audit, 'record', spy)

    class Watcher(FakeSocket):
        async def receive(self):
            raise teleop.WebSocketDisconnect(code=1000)

        async def send_json(self, payload):
            self.sent.append(payload)

    watcher = Watcher([])
    asyncio.run(fleet_poses.fleet_pose_stream(watcher))

    assert recorded == []
    # 그리고 스냅샷은 실제로 나갔다 — 아무 일도 안 한 것이 아니다.
    assert watcher.sent and watcher.sent[0]['type'] == 'snapshot'
    # 끊긴 관전자가 구독에 남으면 다음 로봇이 붙을 때까지 샌다.
    assert fleet_pose.watcher_count() == 0


def test_teleop_attach_is_still_an_intervention():
    """관측을 분리한 근거가 그대로인지 확인한다.

    이 단언이 깨졌다면 §1.1 의 판정 집합이 바뀐 것이고, 그때는 관측 채널을
    따로 둘 이유부터 다시 따져야 한다.
    """
    assert control_audit.TELEOP_ATTACH in control_audit.INTERVENTION_ACTIONS
