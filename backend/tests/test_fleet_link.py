"""로봇 링크 — 목표를 받고 판정을 돌려주는 층.

여기서 지키는 계약은 넷이다.

  1. 목표가 있어야 판정이 선다 (없으면 조정 대상이 아니다)
  2. 오래된 목표는 버린다 — 조용해진 로봇이 남의 길을 계속 막으면 안 된다
  3. **판정을 계속 밀어낸다** — 침묵은 '서 있어라' 가 아니라 '조정이 없다' 다
  4. 이 경로는 control_audit 에 아무것도 남기지 않는다

3번이 로봇 쪽 데드맨과 짝이다. 서버가 죽으면 갱신이 끊기고, 로봇은 ttl 이
지나면 스스로 푼다. 그 ttl 을 서버가 매 프레임에 실어 보내는지를 여기서 본다.
"""

import asyncio

import pytest

from app import control_audit, fleet_link, fleet_map, fleet_pose, fleet_reserve
from app.routers import fleet_agent


def make_map():
    """zone-A ─ seg-1 ─ zone-B. 좌표 조회까지 되도록 래스터도 넣는다.

    래스터는 3x1 이다 — 왼쪽 칸이 zone-A, 가운데가 seg-1, 오른쪽이 zone-B.
    해상도 1 m 라 x=0.5/1.5/2.5 가 각각의 중심이다.
    """
    return fleet_map.parse({
        "map": "test", "map_sha256": "0" * 16, "robot_width": 0.12,
        "zones": {
            "zone-A": {"area_m2": 1.0, "connects": ["seg-1"], "waypoints": ["a"]},
            "zone-B": {"area_m2": 1.0, "connects": ["seg-1"], "waypoints": ["b"]},
        },
        "segments": {
            "seg-1": {"area_m2": 0.05, "connects": ["zone-A", "zone-B"],
                      "waypoints": ["room"]},
        },
        "grid": {
            "width": 3, "height": 1, "resolution": 1.0, "origin": [0.0, 0.0],
            "legend": {1: "zone-A", 2: "zone-B", 3: "seg-1"},
            "rows": ["1:1 1:3 1:2"],
        },
    })


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    fleet_pose.reset()
    fleet_link.reset()
    monkeypatch.setattr(fleet_map, "_loaded", make_map())
    yield
    fleet_pose.reset()
    fleet_link.reset()
    fleet_map.reset()


class FakeSocket:
    """fleet 링크가 쓰는 만큼만 흉내낸다."""

    def __init__(self, incoming=()):
        self._incoming = list(incoming)
        self.sent = []
        self.closed = False

    async def accept(self):
        pass

    async def receive_json(self):
        if not self._incoming:
            raise fleet_agent.WebSocketDisconnect(code=1000)
        return self._incoming.pop(0)

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True


# ------------------------------------------------------------------ 판정

def test_without_a_goal_nothing_is_reserved():
    """계약 1 — 목표를 모르면 조정 대상이 아니다."""
    fleet_pose.update("pinky-01", 0.5, 0.5, 0.0)      # zone-A
    plan = fleet_link.current_plan()

    decision = plan.decisions["pinky-01"]
    assert decision.proceed is True
    assert decision.holds == frozenset()


def test_goal_turns_into_a_reservation():
    fleet_pose.update("pinky-01", 0.5, 0.5, 0.0)      # zone-A
    fleet_link.report("pinky-01", goal_waypoint="b", guiding=True)

    decision = fleet_link.current_plan().decisions["pinky-01"]
    assert decision.proceed is True
    assert "seg-1" in decision.holds


def test_second_robot_waits_for_the_corridor():
    fleet_pose.update("pinky-01", 0.5, 0.5, 0.0)      # zone-A
    fleet_pose.update("pinky-02", 2.5, 0.5, 0.0)      # zone-B
    fleet_link.report("pinky-01", goal_waypoint="b", guiding=True)
    fleet_link.report("pinky-02", goal_waypoint="a", guiding=True)

    plan = fleet_link.current_plan()
    assert plan.decisions["pinky-01"].proceed is True
    second = plan.decisions["pinky-02"]
    assert second.proceed is False
    assert second.blocked_by == "pinky-01"


def test_stale_intent_is_dropped():
    """계약 2 — 조용해진 로봇이 옛 목표로 남의 길을 막으면 되돌릴 근거가 없다."""
    from datetime import timedelta

    fleet_pose.update("pinky-01", 0.5, 0.5, 0.0)
    fleet_pose.update("pinky-02", 2.5, 0.5, 0.0)
    fleet_link.report("pinky-01", goal_waypoint="b", guiding=True)
    fleet_link.report("pinky-02", goal_waypoint="a", guiding=True)

    later = fleet_link._now() + fleet_link.INTENT_STALE + timedelta(seconds=1)
    plan = fleet_link.current_plan(now=later)

    # 둘 다 목표가 낡았으니 아무도 아무것도 안 잡는다.
    assert all(not d.holds for d in plan.decisions.values())
    assert all(d.proceed for d in plan.decisions.values())


def test_unknown_position_is_not_coordinated():
    """위치를 모르면 조정에서 뺀다. 지도 밖 좌표도 마찬가지다."""
    fleet_pose.update("pinky-01", 99.0, 99.0, 0.0)    # 맵 밖
    fleet_link.report("pinky-01", goal_waypoint="b", guiding=True)

    assert fleet_link.current_plan().decisions["pinky-01"].holds == frozenset()


# ------------------------------------------------------------------ 밀어내기

def test_every_frame_carries_the_deadman_ttl():
    """계약 3 — 로봇이 스스로 풀 수 있으려면 매번 ttl 이 실려야 한다."""
    decision = fleet_reserve.Decision("pinky-01", proceed=False,
                                      reason="peer_in_segment")
    message = fleet_link.decision_message(decision)

    assert message["type"] == "decision"
    assert message["proceed"] is False
    assert message["ttl_sec"] == fleet_link.DECISION_TTL_SEC
    assert message["ttl_sec"] > fleet_link.TICK_SEC, (
        "ttl 이 tick 보다 짧으면 한 프레임만 늦어도 로봇이 풀려 버린다")


def test_push_sends_to_every_linked_robot():
    async def scenario():
        one, two = FakeSocket(), FakeSocket()
        fleet_link.attach("pinky-01", one)
        fleet_link.attach("pinky-02", two)
        fleet_pose.update("pinky-01", 0.5, 0.5, 0.0)
        fleet_pose.update("pinky-02", 2.5, 0.5, 0.0)
        fleet_link.report("pinky-01", goal_waypoint="b", guiding=True)
        fleet_link.report("pinky-02", goal_waypoint="a", guiding=True)

        assert await fleet_link.push() == 2
        assert one.sent[-1]["proceed"] is True
        assert two.sent[-1]["proceed"] is False
        assert two.sent[-1]["blocked_by"] == "pinky-01"

    asyncio.run(scenario())


def test_robot_with_no_plan_entry_still_gets_a_frame():
    """조정 대상이 아니라는 사실도 보내야 한다.

    안 보내면 로봇은 침묵을 데드맨으로 읽고, 걸리지도 않은 hold 를 푸는
    경고를 계속 남긴다.
    """
    async def scenario():
        socket = FakeSocket()
        fleet_link.attach("pinky-09", socket)     # 위치도 목표도 없다
        assert await fleet_link.push() == 1
        assert socket.sent[-1]["proceed"] is True

    asyncio.run(scenario())


def test_reattach_closes_the_old_socket():
    """옛 소켓이 남으면 판정이 그리로 새고 로봇은 계속 데드맨으로 풀린다."""
    async def scenario():
        old, new = FakeSocket(), FakeSocket()
        assert fleet_link.attach("pinky-01", old) is None
        assert fleet_link.attach("pinky-01", new) is old

    asyncio.run(scenario())


def test_detach_forgets_the_goal():
    """끊긴 로봇의 목표가 남으면 없는 로봇이 길을 막는다."""
    async def scenario():
        socket = FakeSocket()
        fleet_link.attach("pinky-01", socket)
        fleet_link.report("pinky-01", goal_waypoint="b", guiding=True)
        fleet_link.detach("pinky-01", socket)

        assert fleet_link.linked() == []
        assert "pinky-01" not in fleet_link.snapshot()

    asyncio.run(scenario())


# ------------------------------------------------------------------ 소켓

def test_socket_records_intent_and_replies():
    async def scenario():
        socket = FakeSocket([
            {"type": "intent", "goal_waypoint": "b", "guiding": True},
        ])
        fleet_pose.update("pinky-01", 0.5, 0.5, 0.0)
        await fleet_agent.fleet_socket(socket, "pinky-01")

        # 붙자마자 한 번, 목표를 받고 한 번.
        assert len(socket.sent) >= 2
        assert "seg-1" in socket.sent[-1]["segments"]
        # 끊기면 정리된다.
        assert fleet_link.linked() == []

    asyncio.run(scenario())


def test_socket_ignores_anything_that_is_not_an_intent():
    """이 경로로는 로봇을 몰 수 없다는 계약."""
    async def scenario():
        socket = FakeSocket([
            {"type": "cmd_vel", "linear": 1.0},
            {"type": "intent", "goal_waypoint": None, "guiding": False},
            "문자열",
        ])
        await fleet_agent.fleet_socket(socket, "pinky-01")

        intent = fleet_link.snapshot().get("pinky-01")
        assert intent is None or intent.goal_waypoint is None

    asyncio.run(scenario())


def test_fleet_socket_records_no_control_audit(monkeypatch):
    """계약 4 — 기계의 조정은 사람의 개입이 아니다."""
    recorded = []

    async def spy(*args, **kwargs):
        recorded.append(args)
        return True

    monkeypatch.setattr(control_audit, "record", spy)

    async def scenario():
        await fleet_agent.fleet_socket(FakeSocket(), "pinky-01")

    asyncio.run(scenario())
    assert recorded == []


def test_yield_is_not_an_slo_intervention():
    """양보가 개입으로 잡히면 조정이 잘 될수록 완주율이 떨어진다."""
    assert "fleet_yield" not in control_audit.INTERVENTION_ACTIONS
    assert not any("fleet" in action
                   for action in control_audit.INTERVENTION_ACTIONS)


def test_event_codes_exist():
    """왜 멈췄는지가 타임라인에 안 남으면 과잉 대기를 측정할 수 없다."""
    from app.event_codes import EventCodeRegistry

    codes = EventCodeRegistry.load()
    assert codes.is_known("fleet.yield_started")
    assert codes.is_known("fleet.yield_ended")
    # mobile 전용이다. 팔이 낼 수 있는 코드로 열어두면 ingest 검증이 헐거워진다.
    assert codes.allowed_robot_types("fleet.yield_started") == ["mobile"]
