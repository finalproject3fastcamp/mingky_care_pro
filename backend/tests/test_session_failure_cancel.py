"""장기 통신·시스템 장애의 안내 세션 취소 판정."""

import asyncio
from datetime import datetime, timedelta, timezone

from app import heartbeat, robot_runtime


NOW = datetime(2026, 8, 12, 7, 0, tzinfo=timezone.utc)


class SessionConnection:
    def __init__(self, rows):
        self.rows = rows
        self.args = None

    async def fetch(self, query, robot_ids):
        self.args = (query, robot_ids)
        return self.rows


def setup_function():
    heartbeat.reset()
    robot_runtime.reset()


def teardown_function():
    heartbeat.reset()
    robot_runtime.reset()


def test_short_heartbeat_gap_does_not_cancel_session(monkeypatch):
    monkeypatch.setattr(heartbeat, "SESSION_FAILURE_CANCEL_AFTER_SEC", 30)
    heartbeat._last_seen["pinky-01"] = NOW - timedelta(seconds=29)

    assert heartbeat.cancellation_reasons(NOW) == {}


def test_long_heartbeat_gap_cancels_as_robot_offline(monkeypatch):
    monkeypatch.setattr(heartbeat, "SESSION_FAILURE_CANCEL_AFTER_SEC", 30)
    heartbeat._last_seen["pinky-01"] = NOW - timedelta(seconds=30)

    assert heartbeat.cancellation_reasons(NOW) == {
        "pinky-01": "robot_offline",
    }


def test_long_failed_system_state_cancels_while_heartbeat_is_alive(monkeypatch):
    monkeypatch.setattr(heartbeat, "SESSION_FAILURE_CANCEL_AFTER_SEC", 30)
    heartbeat._last_seen["pinky-01"] = NOW - timedelta(seconds=1)
    robot_runtime._states["pinky-01"] = robot_runtime.RuntimeState(
        system_state="failed",
        localization_active=False,
        reported_at=NOW - timedelta(seconds=1),
        state_since=NOW - timedelta(seconds=31),
    )

    assert heartbeat.cancellation_reasons(NOW) == {
        "pinky-01": "system_failure",
    }


def test_system_state_timer_resets_when_state_changes(monkeypatch):
    times = iter((NOW - timedelta(seconds=40), NOW))
    monkeypatch.setattr(robot_runtime, "_now", lambda: next(times))

    robot_runtime.update("pinky-01", "failed", False)
    robot_runtime.update("pinky-01", "active", False)

    state = robot_runtime.snapshot()["pinky-01"]
    assert state.system_state == "active"
    assert state.state_since == NOW


def test_cancel_event_targets_only_active_session(monkeypatch):
    monkeypatch.setattr(
        heartbeat,
        "cancellation_reasons",
        lambda now: {"pinky-01": "robot_offline"},
    )
    conn = SessionConnection([
        {"session_id": 42, "robot_id": "pinky-01"},
    ])

    events = asyncio.run(heartbeat._session_cancel_events(conn, NOW))

    assert len(events) == 1
    assert events[0].event_code == "session.ended"
    assert events[0].session_id == 42
    assert events[0].payload == {"end_reason": "robot_offline"}
    assert conn.args[1] == ["pinky-01"]
