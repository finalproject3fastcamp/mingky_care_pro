"""미등록 event_code 집계 API 검증."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.event_codes import UNKNOWN_CODE
from app.routers import events


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.call = None

    async def fetch(self, query, *args):
        self.call = (query, args)
        return self.rows


class AcquireContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, rows):
        self.connection = FakeConnection(rows)

    def acquire(self):
        return AcquireContext(self.connection)


def _row(code, robot_id, count, first_seen, last_seen):
    return {
        "event_code": code,
        "robot_id": robot_id,
        "count": count,
        "first_seen": first_seen,
        "last_seen": last_seen,
    }


def test_unknown_codes_are_aggregated_from_the_marker_events(monkeypatch):
    now = datetime.now(timezone.utc)
    pool = FakePool([
        _row("robot.estop_engaged", "pinky-01", 12, now - timedelta(hours=2), now),
    ])
    monkeypatch.setattr(events, "get_pool", lambda: pool)

    result = asyncio.run(events.list_unknown_codes(registry=FakeRegistry([])))

    # 별도 수집 테이블이 아니라 ingest 가 남긴 마커를 집계한다.
    query, _ = pool.connection.call
    assert UNKNOWN_CODE in query
    assert "received_code" in query

    assert len(result) == 1
    assert result[0].event_code == "robot.estop_engaged"
    assert result[0].robot_id == "pinky-01"
    assert result[0].count == 12


def test_since_and_limit_are_passed_through(monkeypatch):
    pool = FakePool([])
    monkeypatch.setattr(events, "get_pool", lambda: pool)
    since = datetime(2026, 8, 13, tzinfo=timezone.utc)

    asyncio.run(events.list_unknown_codes(since=since, limit=25,
                                          registry=FakeRegistry([])))

    _, args = pool.connection.call
    assert args == (since, 25)


def test_marker_without_received_code_does_not_break_the_screen(monkeypatch):
    now = datetime.now(timezone.utc)
    # payload 에 received_code 가 없으면 SQL 이 NULL 을 돌려준다.
    # 화면이 죽는 대신 표시되어야 한다 — 그 자체가 조사 단서다.
    pool = FakePool([_row(None, None, 1, now, now)])
    monkeypatch.setattr(events, "get_pool", lambda: pool)

    result = asyncio.run(events.list_unknown_codes(registry=FakeRegistry([])))

    assert result[0].event_code == "(코드 없음)"
    assert result[0].robot_id is None


class FakeRegistry:
    def __init__(self, known):
        self.known = set(known)

    def is_known(self, code):
        return code in self.known


def test_codes_registered_since_are_dropped_from_the_warning(monkeypatch):
    """yaml 을 갱신해 해결한 코드는 목록에서 빠진다.

    events 의 마커는 append-only 라 영원히 남는다. 해결한 뒤에도 계속
    보이면 경고가 절대 꺼지지 않고, 항상 켜져 있는 경고는 아무도 안 본다 —
    그러면 진짜 미등록 코드가 새로 들어와도 묻힌다.
    """
    now = datetime.now(timezone.utc)
    pool = FakePool([
        _row("robot.estop_engaged", "pinky-01", 4, now, now),   # 이제 등록됨
        _row("robot.unknown_thing", "pinky-02", 2, now, now),   # 여전히 미등록
    ])
    monkeypatch.setattr(events, "get_pool", lambda: pool)

    result = asyncio.run(events.list_unknown_codes(
        registry=FakeRegistry({"robot.estop_engaged"})))

    assert [r.event_code for r in result] == ["robot.unknown_thing"]


def test_history_is_still_reachable_with_include_resolved(monkeypatch):
    now = datetime.now(timezone.utc)
    pool = FakePool([_row("robot.estop_engaged", "pinky-01", 4, now, now)])
    monkeypatch.setattr(events, "get_pool", lambda: pool)

    result = asyncio.run(events.list_unknown_codes(
        include_resolved=True, registry=FakeRegistry({"robot.estop_engaged"})))

    assert [r.event_code for r in result] == ["robot.estop_engaged"]
