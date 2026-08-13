"""세션 종료 인과 조회 검증."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest

from app.routers import sessions


class FakeConnection:
    def __init__(self, session_row, event_rows=()):
        self.session_row = session_row
        self.event_rows = list(event_rows)
        self.fetch_args = None

    async def fetchrow(self, query, *args):
        return self.session_row

    async def fetch(self, query, *args):
        self.fetch_args = args
        return self.event_rows


class AcquireContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return AcquireContext(self.connection)


def _event(code, level, occurred_at):
    return {
        "event_id": uuid.uuid4(),
        "robot_id": "pinky-01",
        "session_id": 7,
        "occurred_at": occurred_at,
        "received_at": occurred_at,
        "level": level,
        "event_code": code,
        "source_node": "mingky_battery_guard",
        "payload": None,
    }


def _run(pool, monkeypatch, session_id=7):
    monkeypatch.setattr(sessions, "get_pool", lambda: pool)
    return asyncio.run(sessions.get_ending_context(session_id))


def test_lead_warning_is_the_earliest_non_info_event(monkeypatch):
    ended = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    rows = [
        # 정상 진행 로그가 원인으로 지목되면 판단이 흐려진다.
        _event("nav.goal_sent", "info", ended - timedelta(seconds=55)),
        _event("robot.battery_low", "warning", ended - timedelta(seconds=40)),
        _event("session.ended", "error", ended),
    ]
    pool = FakePool(FakeConnection(
        {"session_id": 7, "ended_at": ended, "end_reason": "battery_low"}, rows))

    result = _run(pool, monkeypatch)

    assert result.lead_event_code == "robot.battery_low"
    assert result.lead_sec == 40
    # 인과는 시간 순으로 읽어야 "A 다음에 B" 가 보인다.
    assert [e.event_code for e in result.events] == [
        "nav.goal_sent", "robot.battery_low", "session.ended"]


def test_window_is_bounded_to_the_minute_before_the_end(monkeypatch):
    ended = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    pool = FakePool(FakeConnection(
        {"session_id": 7, "ended_at": ended, "end_reason": "robot_offline"}))

    _run(pool, monkeypatch)

    assert pool.connection.fetch_args == (7, ended, "60")


def test_running_session_returns_empty_context_not_404(monkeypatch):
    # 아직 안 끝난 세션에는 창이 없다. 404 는 "세션이 없다" 와 구분이 안 된다.
    pool = FakePool(FakeConnection(
        {"session_id": 7, "ended_at": None, "end_reason": None}))

    result = _run(pool, monkeypatch)

    assert result.session_id == 7
    assert result.ended_at is None
    assert result.events == []
    assert result.lead_event_code is None


def test_missing_session_is_404(monkeypatch):
    pool = FakePool(FakeConnection(None))
    monkeypatch.setattr(sessions, "get_pool", lambda: pool)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(sessions.get_ending_context(999))

    assert exc_info.value.status_code == 404


def test_no_warning_in_window_leaves_lead_empty(monkeypatch):
    ended = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    rows = [_event("nav.goal_succeeded", "info", ended - timedelta(seconds=5))]
    pool = FakePool(FakeConnection(
        {"session_id": 7, "ended_at": ended, "end_reason": "completed"}, rows))

    result = _run(pool, monkeypatch)

    assert result.lead_event_code is None
    assert result.lead_sec is None
    assert len(result.events) == 1
