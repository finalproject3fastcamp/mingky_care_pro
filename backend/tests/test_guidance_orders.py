"""의료진 안내 시작 명령의 세션 검증."""

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.routers import orders as orders_router
from app import orders
from app.actor import ANONYMOUS
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

    async def require_session(robot_id, argument, command):
        calls.append(('session', robot_id, argument, command))

    queued = SimpleNamespace(command='start_guidance', argument='42')
    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(orders_router, '_require_active_session', require_session)
    monkeypatch.setattr(orders_router.orders, 'put', lambda *args, **kwargs: queued)

    result = asyncio.run(orders_router.create_order(
        'pinky-01', OrderIn(command='start_guidance', argument='42'), ANONYMOUS))

    assert result is queued
    assert calls == [
        ('robot', 'pinky-01'),
        ('session', 'pinky-01', '42', 'start_guidance'),
    ]


def test_cancel_guidance_command_is_part_of_the_contract():
    command = OrderIn(command='cancel_guidance', argument='42')

    assert command.command == 'cancel_guidance'
    assert command.argument == '42'


def test_cancel_guidance_validates_session_before_queueing(monkeypatch):
    calls = []

    async def require_robot(robot_id):
        calls.append(('robot', robot_id))

    async def require_session(robot_id, argument, command):
        calls.append(('session', robot_id, argument, command))

    queued = SimpleNamespace(command='cancel_guidance', argument='42')
    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(orders_router, '_require_active_session', require_session)
    monkeypatch.setattr(orders_router.orders, 'put', lambda *args, **kwargs: queued)
    monkeypatch.setattr(
        orders_router.robot_runtime,
        'snapshot',
        lambda: {
            'pinky-01': type('Runtime', (), {'system_state': 'active'})(),
        },
    )

    result = asyncio.run(orders_router.create_order(
        'pinky-01', OrderIn(command='cancel_guidance', argument='42'), ANONYMOUS))

    assert result is queued
    assert calls == [
        ('robot', 'pinky-01'),
        ('session', 'pinky-01', '42', 'cancel_guidance'),
    ]


def test_cancel_guidance_has_an_independent_priority_slot():
    assert orders._slot('cancel_guidance') is not orders._slot('set_mode')
    assert orders._slot('cancel_guidance') is not orders._slot('goto')
    assert orders._slot('cancel_guidance') is not orders._slot('system_stop')


def test_cancel_guidance_is_delivered_before_motion():
    orders.reset()
    motion = orders.put('pinky-01', 'goto', 'xray_room_goal')
    cancel = orders.put('pinky-01', 'cancel_guidance', '42')

    assert orders.peek('pinky-01') == cancel
    assert orders.ack('pinky-01', cancel.order_id) is True
    assert orders.peek('pinky-01') == motion
    orders.reset()


def test_create_cancel_discards_an_undelivered_motion_command(monkeypatch):
    async def require_robot(robot_id):
        return None

    async def require_session(robot_id, argument, command):
        return None

    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(orders_router, '_require_active_session', require_session)
    monkeypatch.setattr(
        orders_router.robot_runtime,
        'snapshot',
        lambda: {
            'pinky-01': type('Runtime', (), {'system_state': 'active'})(),
        },
    )
    orders.reset()
    motion = orders.put('pinky-01', 'start_guidance', '42')

    cancel = asyncio.run(orders_router.create_order(
        'pinky-01', OrderIn(command='cancel_guidance', argument='42'), ANONYMOUS))

    assert orders.peek('pinky-01') == cancel
    assert orders.ack('pinky-01', cancel.order_id) is True
    assert orders.ack('pinky-01', motion.order_id) is False
    assert orders.peek('pinky-01') is None
    orders.reset()


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


def test_navigation_speed_command_is_part_of_the_contract():
    command = OrderIn(command='set_navigation_speed', argument='0.20')

    assert command.command == 'set_navigation_speed'
    assert command.argument == '0.20'


def test_navigation_speed_uses_a_separate_order_slot():
    assert orders._slot('set_navigation_speed') is not orders._slot('goto')
    assert orders._slot('set_navigation_speed') is not orders._slot('set_mode')


def test_low_obstacle_mode_command_is_part_of_the_contract():
    command = OrderIn(command='set_low_obstacle_mode', argument='sidestep')

    assert command.command == 'set_low_obstacle_mode'
    assert command.argument == 'sidestep'


def test_low_obstacle_mode_has_an_independent_config_slot():
    assert orders._slot('set_low_obstacle_mode') is not orders._slot(
        'set_navigation_speed')
    assert orders._slot('set_low_obstacle_mode') is not orders._slot('goto')


def test_low_obstacle_mode_does_not_overwrite_pending_speed_change():
    orders.reset()
    try:
        orders.put('pinky-01', 'set_navigation_speed', '0.15')
        orders.put('pinky-01', 'set_low_obstacle_mode', 'sidestep')

        pending = orders.snapshot()['pinky-01']
        assert [order.command for order in pending] == [
            'set_navigation_speed', 'set_low_obstacle_mode']
    finally:
        orders.reset()


@pytest.mark.parametrize('argument', ['disabled', 'sidestep'])
def test_low_obstacle_mode_accepts_supported_strategies(argument):
    orders_router._validate_low_obstacle_mode(argument)


@pytest.mark.parametrize('argument', ['enabled', 'stop_only', 'range_layer', ''])
def test_low_obstacle_mode_rejects_unknown_strategies(argument):
    with pytest.raises(HTTPException) as raised:
        orders_router._validate_low_obstacle_mode(argument)

    assert raised.value.status_code == 422


@pytest.mark.parametrize('argument', ['0.04', '0.251', '0.26', 'nan', 'fast'])
def test_navigation_speed_rejects_values_outside_safe_steps(argument):
    with pytest.raises(HTTPException) as raised:
        orders_router._validate_navigation_speed(argument)

    assert raised.value.status_code == 422


@pytest.mark.parametrize('argument', ['0.05', '0.1', '0.20', '0.25'])
def test_navigation_speed_accepts_safe_steps(argument):
    orders_router._validate_navigation_speed(argument)


def test_navigation_speed_rejects_active_session(monkeypatch):
    async def require_robot(robot_id):
        return None

    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(
        orders_router, 'get_pool', lambda: SessionPool(matches=77))
    monkeypatch.setattr(orders_router.robot_runtime, 'snapshot', lambda: {})

    with pytest.raises(HTTPException) as raised:
        asyncio.run(orders_router.create_order(
            'pinky-01', OrderIn(
                command='set_navigation_speed', argument='0.15'), ANONYMOUS))

    assert raised.value.status_code == 409


def test_fire_alarm_reset_rejects_unknown_argument(monkeypatch):
    async def require_robot(robot_id):
        return None

    monkeypatch.setattr(orders_router, '_require_robot', require_robot)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(orders_router.create_order(
            'pinky-01',
            OrderIn(command='fire_alarm_reset', argument='reset'),
            ANONYMOUS))

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
            'pinky-01',
            OrderIn(command='fire_alarm_reset', argument='run'),
            ANONYMOUS))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == 'fire alarm is not active'

def test_localize_rejects_unknown_argument(monkeypatch):
    async def require_robot(robot_id):
        return None

    monkeypatch.setattr(orders_router, '_require_robot', require_robot)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(orders_router.create_order(
            'pinky-01', OrderIn(command='localize', argument='start'), ANONYMOUS))

    assert raised.value.status_code == 422


def test_system_stop_rejects_active_session(monkeypatch):
    async def require_robot(robot_id):
        return None

    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(
        orders_router, 'get_pool', lambda: SessionPool(matches=77))

    with pytest.raises(HTTPException) as raised:
        asyncio.run(orders_router.create_order(
            'pinky-01', OrderIn(command='system_stop', argument='run'), ANONYMOUS))

    assert raised.value.status_code == 409


def test_system_start_is_allowed_without_session_query(monkeypatch):
    async def require_robot(robot_id):
        return None

    queued = SimpleNamespace(command='system_start', argument='run')
    monkeypatch.setattr(orders_router, '_require_robot', require_robot)
    monkeypatch.setattr(orders_router.orders, 'put', lambda *args, **kwargs: queued)

    result = asyncio.run(orders_router.create_order(
        'pinky-01', OrderIn(command='system_start', argument='run'), ANONYMOUS))

    assert result is queued
