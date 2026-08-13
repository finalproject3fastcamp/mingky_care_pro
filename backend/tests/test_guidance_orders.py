"""의료진 안내 시작 명령의 세션 검증."""

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.routers import orders as orders_router
from app import orders
from app.schemas import OrderIn


class AcquireContext:

    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class SessionConnection:

    def __init__(self, matches):
        self.matches = matches
        self.call = None

    async def fetchval(self, query, *args):
        self.call = (query, args)
        return self.matches


class SessionPool:

    def __init__(self, matches):
        self.connection = SessionConnection(matches)

    def acquire(self):
        return AcquireContext(self.connection)


def test_start_guidance_command_is_part_of_the_contract():
    command = OrderIn(command='start_guidance', argument='42')

    assert command.command == 'start_guidance'
    assert command.argument == '42'


@pytest.mark.parametrize('argument', ['not-a-number', '0', '-1'])
def test_start_guidance_requires_positive_session_id(monkeypatch, argument):
    monkeypatch.setattr(
        orders_router, 'get_pool', lambda: SessionPool(matches=True))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(orders_router._require_active_session('pinky-01', argument))

    assert exc_info.value.status_code == 422


def test_start_guidance_rejects_session_owned_by_another_robot(monkeypatch):
    pool = SessionPool(matches=None)
    monkeypatch.setattr(orders_router, 'get_pool', lambda: pool)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(orders_router._require_active_session('pinky-02', '42'))

    assert exc_info.value.status_code == 409
    assert pool.connection.call[1] == (42, 'pinky-02')


def test_start_guidance_accepts_active_session_for_robot(monkeypatch):
    pool = SessionPool(matches=True)
    monkeypatch.setattr(orders_router, 'get_pool', lambda: pool)

    asyncio.run(orders_router._require_active_session('pinky-01', '42'))

    assert pool.connection.call[1] == (42, 'pinky-01')


def test_create_order_validates_session_before_queueing(monkeypatch):
    calls = []

    async def require_robot(robot_id):
        calls.append(('robot', robot_id))

    async def require_session(robot_id, argument):
        calls.append(('session', robot_id, argument))

    queued = SimpleNamespace(command='start_guidance', argument='42')
    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(orders_router, '_require_active_session', require_session)
    monkeypatch.setattr(orders_router.orders, 'put', lambda *args: queued)

    result = asyncio.run(orders_router.create_order(
        'pinky-01', OrderIn(command='start_guidance', argument='42')))

    assert result is queued
    assert calls == [
        ('robot', 'pinky-01'),
        ('session', 'pinky-01', '42'),
    ]


def test_unknown_guidance_command_remains_rejected():
    with pytest.raises(ValidationError):
        OrderIn(command='begin_guidance', argument='42')


def test_localize_command_is_part_of_the_contract():
    command = OrderIn(command='localize', argument='run')

    assert command.command == 'localize'
    assert command.argument == 'run'


def test_fire_alarm_reset_command_is_part_of_the_contract():
    command = OrderIn(command='fire_alarm_reset', argument='run')

    assert command.command == 'fire_alarm_reset'
    assert command.argument == 'run'


def test_fire_alarm_reset_does_not_overwrite_safety_or_motion_command():
    assert orders._slot('fire_alarm_reset') is not orders._slot('set_mode')
    assert orders._slot('fire_alarm_reset') is not orders._slot('goto')


def test_fire_alarm_reset_rejects_unknown_argument(monkeypatch):
    async def require_robot(robot_id):
        return None

    monkeypatch.setattr(orders_router, '_require_robot', require_robot)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(orders_router.create_order(
            'pinky-01', OrderIn(command='fire_alarm_reset', argument='reset')))

    assert raised.value.status_code == 422


def test_fire_alarm_reset_rejects_when_alarm_is_not_active(monkeypatch):
    async def require_robot(robot_id):
        return None

    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(
        orders_router.robot_runtime,
        'snapshot',
        lambda: {
            'pinky-01': type('Runtime', (), {
                'system_state': 'active',
                'fire_alarm_active': False,
            })(),
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(orders_router.create_order(
            'pinky-01', OrderIn(command='fire_alarm_reset', argument='run')))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == 'fire alarm is not active'

def test_localize_rejects_unknown_argument(monkeypatch):
    async def require_robot(robot_id):
        return None

    monkeypatch.setattr(orders_router, '_require_robot', require_robot)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(orders_router.create_order(
            'pinky-01', OrderIn(command='localize', argument='start')))

    assert raised.value.status_code == 422


def test_system_stop_rejects_active_session(monkeypatch):
    async def require_robot(robot_id):
        return None

    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(
        orders_router, 'get_pool', lambda: SessionPool(matches=77))

    with pytest.raises(HTTPException) as raised:
        asyncio.run(orders_router.create_order(
            'pinky-01', OrderIn(command='system_stop', argument='run')))

    assert raised.value.status_code == 409


def test_system_start_is_allowed_without_session_query(monkeypatch):
    async def require_robot(robot_id):
        return None

    queued = SimpleNamespace(command='system_start', argument='run')
    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(orders_router.orders, 'put', lambda *args: queued)

    result = asyncio.run(orders_router.create_order(
        'pinky-01', OrderIn(command='system_start', argument='run')))

    assert result is queued
