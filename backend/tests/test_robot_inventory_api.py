"""heartbeat 층 1 필드와 인벤토리 엔드포인트 검증."""

import asyncio
import json
from datetime import datetime, timezone

from fastapi import HTTPException
import pytest

from app import heartbeat, robot_runtime
from app.routers import robots
from app.schemas import RobotHeartbeatIn, RobotInventoryIn


class FakeConnection:
    def __init__(self, value=1, row=None):
        self.value = value
        self.row = row
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        return self.value

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.row


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


def _setup(monkeypatch, connection):
    monkeypatch.setattr(robots, "get_pool", lambda: FakePool(connection))
    heartbeat.reset()
    robot_runtime.reset()


def test_heartbeat_keeps_resource_fields_in_memory_only(monkeypatch):
    connection = FakeConnection(value=1)
    _setup(monkeypatch, connection)
    heartbeat.touch("pinky-01")

    body = RobotHeartbeatIn(
        system_state="active", localization_active=True,
        guide_robot_state="returning_to_dock",
        inventory_hash="a1b2c3d4", cpu_total_pct=23.4, queue_pending=1204,
        max_node_cpu_pct=99.9, max_node_cpu_name="event_gateway")

    asyncio.run(robots.post_heartbeat("pinky-01", body))

    state = robot_runtime.snapshot()["pinky-01"]
    assert state.queue_pending == 1204
    assert state.guide_robot_state == "returning_to_dock"
    assert state.max_node_cpu_name == "event_gateway"
    # 3~5초마다 덮어쓰는 값은 DB 에 쌓지 않는다. 저장 쿼리가 없어야 한다.
    assert all("INSERT" not in query for query, _ in connection.calls)


def test_heartbeat_asks_for_inventory_when_the_hash_is_new(monkeypatch):
    # 서버가 아는 해시와 다르면 본문을 요구한다.
    connection = FakeConnection(value="oldhash")
    _setup(monkeypatch, connection)
    heartbeat.touch("pinky-01")

    result = asyncio.run(robots.post_heartbeat(
        "pinky-01", RobotHeartbeatIn(inventory_hash="newhash")))

    assert result.need_inventory is True


def test_heartbeat_is_quiet_when_the_hash_already_matches(monkeypatch):
    connection = FakeConnection(value="samehash")
    _setup(monkeypatch, connection)
    heartbeat.touch("pinky-01")

    result = asyncio.run(robots.post_heartbeat(
        "pinky-01", RobotHeartbeatIn(inventory_hash="samehash")))

    assert result.need_inventory is False


def test_gateway_without_inventory_is_not_nagged_every_five_seconds(monkeypatch):
    # 인벤토리를 끈 게이트웨이에 매번 요구해도 오지 않고, 그 요구가
    # 5초마다 DB 조회를 만든다.
    connection = FakeConnection(value=None)
    _setup(monkeypatch, connection)
    heartbeat.touch("pinky-01")

    result = asyncio.run(robots.post_heartbeat(
        "pinky-01", RobotHeartbeatIn(system_state="active")))

    assert result.need_inventory is False
    assert connection.calls == []


def test_old_gateway_payload_still_works(monkeypatch):
    # 신규 필드 없이 오는 heartbeat 가 422 로 거부되면 멀쩡한 로봇에
    # comm_lost 가 찍힌다.
    connection = FakeConnection(value=1)
    _setup(monkeypatch, connection)
    heartbeat.touch("pinky-01")

    body = RobotHeartbeatIn.model_validate(
        {"system_state": "active", "localization_active": False})

    result = asyncio.run(robots.post_heartbeat("pinky-01", body))

    assert result.need_inventory is False
    assert robot_runtime.snapshot()["pinky-01"].cpu_total_pct is None


def test_inventory_upsert_uses_server_time_and_strips_the_hash(monkeypatch):
    connection = FakeConnection(value=1)
    _setup(monkeypatch, connection)

    body = RobotInventoryIn(
        inventory_hash="a1b2c3d4",
        node_graph=[{"name": "adc_reader", "namespace": "/", "count": 2}],
        workspaces=[{"path": "/ws", "commit": "abc", "process_count": 3}],
        ros_domain_id=7,
        reported_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )

    asyncio.run(robots.post_inventory("pinky-01", body))

    query, args = connection.calls[0]
    assert "ON CONFLICT (robot_id) DO UPDATE" in query
    # 로봇 시계를 믿지 않는다. 신선도 판정에 쓰이므로 전송 지연을 숨기면 안 된다.
    assert "now()" in query
    payload = json.loads(args[2])
    assert "inventory_hash" not in payload
    assert "reported_at" not in payload
    assert payload["ros_domain_id"] == 7


def test_inventory_from_unknown_robot_is_rejected(monkeypatch):
    _setup(monkeypatch, FakeConnection(value=None))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(robots.post_inventory(
            "ghost", RobotInventoryIn(inventory_hash="x")))

    assert exc_info.value.status_code == 404


def test_get_inventory_attaches_the_server_side_verdicts(monkeypatch):
    payload = {
        "node_graph": [
            {"name": "adc_reader", "namespace": "/", "count": 2},
            {"name": "guide_manager", "namespace": "/", "count": 1},
        ],
        "processes": [{"pid": 1, "install_path": "/ws/install/a"}],
        "workspaces": [
            {"path": "/home/pinky/mingky_care_pro", "commit": "44ad0a2",
             "process_count": 11},
            {"path": "/home/pinky/wmk/mingky_care_pro", "commit": "15446f3",
             "process_count": 1},
        ],
        "ros_domain_id": 0,
    }
    connection = FakeConnection(row={
        "inventory_hash": "a1b2c3d4",
        "payload": json.dumps(payload),
        "reported_at": datetime.now(timezone.utc),
    })
    _setup(monkeypatch, connection)

    result = asyncio.run(robots.get_inventory("pinky-01"))

    # 프론트가 같은 판정을 다시 구현하면 두 곳이 어긋난다.
    assert [d.name for d in result.duplicates] == ["adc_reader"]
    assert result.duplicates[0].severity == "error"
    assert result.mixed_workspaces is True


def test_get_inventory_before_any_report_is_404(monkeypatch):
    _setup(monkeypatch, FakeConnection(row=None))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(robots.get_inventory("pinky-01"))

    assert exc_info.value.status_code == 404
