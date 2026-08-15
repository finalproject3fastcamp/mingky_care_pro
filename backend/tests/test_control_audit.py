"""제어 개입 적재 — 기록 순서와 실패 시 동작.

여기서 확인하는 것은 "INSERT 가 나가는가" 가 아니다. 그건 e2e 가 진짜 DB 로
본다. 단위에서 지켜야 하는 계약은 둘이고, 둘 다 SLO 의 정직성에 직결된다.

  1. **감사가 명령보다 먼저 나간다.** 뒤집히면 실행됐는데 기록이 없는 창이
     생기고, 개입한 세션이 성공으로 집계돼 SLO 가 실제보다 좋아 보인다.
  2. **감사 실패가 제어를 막지 않는다.** DB 블립 하나로 조작자가 비상정지를
     못 누르는 구조가 되면 안 된다.

pytest-asyncio 를 쓰지 않는다. 저장소의 다른 테스트와 같이 asyncio.run() 으로
감싼다(test_worker_guard.py 참고).
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import control_audit
from app import orders
from app.actor import ANONYMOUS, Actor
from app.routers import orders as orders_router
from app.schemas import OrderIn

NURSE = Actor(name="nurse-02", source="header")


class RecordingConnection:

    def __init__(self):
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))


class RecordingPool:

    def __init__(self):
        self.connection = RecordingConnection()

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                return pool.connection

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        return Ctx()


class BrokenPool:
    """acquire 조차 못 하는 DB. 블립·재시작 중의 상태다."""

    def acquire(self):
        raise RuntimeError("connection refused")


def test_record_writes_actor_and_source_as_a_pair(monkeypatch):
    """011 의 짝 제약에 맞는 값이 그대로 나간다."""
    pool = RecordingPool()
    monkeypatch.setattr(control_audit, "get_pool", lambda: pool)

    assert asyncio.run(control_audit.record(
        "pinky-01", "system_stop", NURSE, argument="run")) is True

    query, args = pool.connection.calls[0]
    assert "INSERT INTO control_audit" in query
    assert args[0] == "pinky-01"
    assert args[2:6] == ("system_stop", "run", "nurse-02", "header")


def test_record_snapshots_the_active_session_in_the_same_statement(monkeypatch):
    """세션을 따로 SELECT 하지 않는다.

    조회와 적재 사이에 세션이 끝나면 이미 끝난 세션에 개입이 달린다. 한 문장
    안에서 잡으면 그 틈이 없다.
    """
    pool = RecordingPool()
    monkeypatch.setattr(control_audit, "get_pool", lambda: pool)

    asyncio.run(control_audit.record("pinky-01", "localize", ANONYMOUS))

    query, args = pool.connection.calls[0]
    assert "SELECT session_id FROM guidance_sessions" in query
    assert "ended_at IS NULL" in query
    # 익명 행은 (NULL, 'absent') 로 들어간다. 센티널 문자열이 아니다.
    assert args[4] is None and args[5] == "absent"


def test_record_never_raises_when_the_database_is_gone(monkeypatch):
    """기록 실패는 False 로 알린다. 예외로 올리면 제어가 막힌다."""
    monkeypatch.setattr(control_audit, "get_pool", lambda: BrokenPool())

    assert asyncio.run(control_audit.record(
        "pinky-01", "system_stop", NURSE)) is False


def test_intervention_set_is_narrower_than_what_is_recorded():
    """기록은 넓게, 판정은 좁게.

    둘을 한 집합으로 합치면 "감사에 남기려고 넓혔더니 완주율이 떨어지는"
    사고가 난다. goto 는 정상 주행이지 개입이 아니다.
    """
    assert control_audit.TELEOP_ATTACH in control_audit.INTERVENTION_ACTIONS
    assert "system_stop" in control_audit.INTERVENTION_ACTIONS
    assert "goto" not in control_audit.INTERVENTION_ACTIONS
    assert control_audit.TELEOP_DETACH not in control_audit.INTERVENTION_ACTIONS


def _stub_validation(monkeypatch):
    async def require_robot(robot_id):
        return None

    monkeypatch.setattr(orders_router, "_require_robot", require_robot)


def test_order_is_audited_before_it_is_queued(monkeypatch):
    """감사가 명령보다 먼저다.

    §1.1 은 "order 없음" 으로 판정한다. 감사 로그의 주어는 로봇의 행동이
    아니라 사람의 판단이므로, 눌린 순간이 기록의 기준점이다. 뒤로 옮기면
    SLO 가 실제보다 좋아 보이는 방향으로 틀린다.
    """
    sequence = []
    _stub_validation(monkeypatch)

    async def record(robot_id, action, actor, **kwargs):
        sequence.append(("audit", action, actor.name, kwargs.get("order_id")))
        return True

    def put(robot_id, command, argument, order_id=None):
        sequence.append(("queued", command, order_id))
        return SimpleNamespace(command=command, order_id=order_id)

    monkeypatch.setattr(orders_router.control_audit, "record", record)
    monkeypatch.setattr(orders_router.orders, "put", put)

    asyncio.run(orders_router.create_order(
        "pinky-01", OrderIn(command="system_start", argument="run"), NURSE))

    assert [step[0] for step in sequence] == ["audit", "queued"]
    # 같은 order_id 를 가리켜야 감사 행과 로봇 ack 를 나중에 이을 수 있다.
    assert sequence[0][3] == sequence[1][2] is not None
    assert sequence[0][2] == "nurse-02"


def test_rejected_order_is_not_audited(monkeypatch):
    """거부된 명령은 개입이 아니다.

    422·409 로 튕긴 것은 로봇에 아무 영향이 없다. 이걸 남기면 눌러보지도
    못한 명령 때문에 세션이 실패로 집계된다.
    """
    audited = []
    _stub_validation(monkeypatch)

    async def record(robot_id, action, actor, **kwargs):
        audited.append(action)
        return True

    monkeypatch.setattr(orders_router.control_audit, "record", record)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(orders_router.create_order(
            "pinky-01",
            OrderIn(command="localize", argument="start"),
            NURSE))

    assert raised.value.status_code == 422
    assert audited == []


def test_command_still_goes_through_when_the_audit_write_fails(monkeypatch):
    """감사가 제어를 막지 않는다.

    이 테스트가 깨지면 DB 장애가 곧 제어 불가가 된다 — 감사 로그를 붙이면서
    잃을 수 있는 가장 비싼 것이다.
    """
    _stub_validation(monkeypatch)
    monkeypatch.setattr(control_audit, "get_pool", lambda: BrokenPool())
    orders.reset()

    order = asyncio.run(orders_router.create_order(
        "pinky-01", OrderIn(command="system_start", argument="run"), NURSE))

    assert orders.peek("pinky-01") == order
    orders.reset()
