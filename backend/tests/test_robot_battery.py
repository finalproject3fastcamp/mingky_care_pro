"""배터리 표본 API 검증."""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.routers import robots
from app.schemas import BatterySampleIn


class FakeConnection:
    def __init__(self, result=1):
        self.result = result
        self.call = None

    async def fetchval(self, query, *args):
        self.call = (query, args)
        return self.result


class AcquireContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, result=1):
        self.connection = FakeConnection(result)

    def acquire(self):
        return AcquireContext(self.connection)


class Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class ArmConnection:
    def __init__(self, row):
        self.row = row

    def transaction(self):
        return Transaction()

    async def fetchrow(self, query, robot_id):
        return self.row


class ArmPool:
    def __init__(self, row):
        self.connection = ArmConnection(row)

    def acquire(self):
        return AcquireContext(self.connection)


def test_battery_sample_is_stored_with_server_time(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(robots, "get_pool", lambda: pool)
    sample = BatterySampleIn(voltage=7.2, battery_percent=50)

    response = asyncio.run(robots.post_battery("pinky-01", sample))

    query, args = pool.connection.call
    assert "now()" in query
    assert args == ("pinky-01", 7.2, 50)
    assert response.status_code == 204


def test_unknown_robot_is_rejected(monkeypatch):
    monkeypatch.setattr(robots, "get_pool", lambda: FakePool(result=None))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(robots.post_battery(
            "missing", BatterySampleIn(battery_percent=50)))

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"voltage": 13.0},
        {"voltage": float("nan")},
        {"battery_percent": 101},
    ],
)
def test_invalid_battery_sample_is_rejected(values):
    with pytest.raises(ValidationError):
        BatterySampleIn(**values)


def test_arm_rejects_stale_battery_reading(monkeypatch):
    row = {
        "robot_id": "pinky-01",
        "robot_type": "mobile",
        "display_name": "Pinky 1",
        "domain_id": 21,
        "is_active": True,
        "battery_voltage": 7.4,
        "battery_percent": 75,
        "battery_recorded_at": datetime.now(timezone.utc) - timedelta(minutes=6),
        "active_session_id": None,
        "active_patient_id": None,
    }
    monkeypatch.setattr(robots, "get_pool", lambda: ArmPool(row))
    monkeypatch.setattr(
        robots.heartbeat,
        "snapshot",
        lambda: {"pinky-01": (datetime.now(timezone.utc), False)},
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(robots.arm_robot("pinky-01"))

    assert exc_info.value.status_code == 409
    # detail 은 코드·파라미터·기존 문구로 나뉜다. 상세 계약은
    # test_arm_rejection_codes.py 가 검증한다.
    assert exc_info.value.detail["code"] == "battery_stale"
    assert exc_info.value.detail["message"] == "battery reading is stale"
