"""arm 거부 사유가 기계용 코드와 파라미터로 나뉘어 나가는지 검증.

영어 문자열 하나만 내려주면 프론트가 그걸 파싱해 화면 문구를 만들게 되고,
그러면 백엔드가 문구를 못 고친다. code 로 분기하고 params 로 숫자를 넘긴다.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest

from app import arming, heartbeat
from app.routers import robots


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


class AcquireContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class ArmPool:
    def __init__(self, row):
        self.connection = ArmConnection(row)

    def acquire(self):
        return AcquireContext(self.connection)


def _row(**overrides):
    row = {
        "robot_id": "pinky-01",
        "robot_type": "mobile",
        "display_name": "핑키 1호",
        "domain_id": 1,
        "is_active": True,
        "battery_voltage": 7.4,
        "battery_percent": 80,
        "battery_recorded_at": datetime.now(timezone.utc),
        "active_session_id": None,
        "active_patient_id": None,
        "last_session_ended_at": None,
        "last_session_end_reason": None,
    }
    row.update(overrides)
    return row


def _arm(monkeypatch, row, *, seen="online"):
    monkeypatch.setattr(robots, "get_pool", lambda: ArmPool(row))
    heartbeat.reset()
    arming.reset() if hasattr(arming, "reset") else None
    if seen == "online":
        heartbeat.touch("pinky-01")
    elif seen == "offline":
        heartbeat.touch("pinky-01")
        heartbeat._offline.add("pinky-01")
    # seen == "unknown" 이면 아무것도 안 한다.

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(robots.arm_robot("pinky-01"))
    return exc_info.value


def test_rejection_carries_code_params_and_legacy_message(monkeypatch):
    error = _arm(monkeypatch, _row(is_active=False))

    assert error.status_code == 409
    assert error.detail["code"] == "robot_inactive"
    # detail 을 dict 로 바꾸면 문자열을 기대하던 기존 클라이언트가 깨진다.
    # 프론트를 다 옮길 때까지 message 를 남긴다.
    assert error.detail["message"] == "robot is not active"


def test_battery_low_carries_the_numbers_the_screen_needs(monkeypatch):
    error = _arm(monkeypatch, _row(battery_percent=12))

    assert error.detail["code"] == "battery_low"
    # 문자열에서 정규식으로 뽑지 않아도 되게 숫자를 그대로 넘긴다.
    assert error.detail["params"]["percent"] == 12
    assert error.detail["params"]["min_percent"] == robots.MIN_BATTERY_PERCENT


def test_missing_battery_is_distinct_from_low_battery(monkeypatch):
    # "정보 없음" 과 "부족" 은 대응이 다르다. 전자는 엔지니어 호출이다.
    error = _arm(monkeypatch, _row(battery_percent=None))

    assert error.detail["code"] == "battery_unknown"


def test_stale_battery_reports_its_age(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(seconds=743)
    error = _arm(monkeypatch, _row(battery_recorded_at=old))

    assert error.detail["code"] == "battery_stale"
    # "오래됐다" 만으로는 5분인지 12분인지 모르고, 그 차이가 엔지니어를
    # 부를지 말지를 가른다.
    assert error.detail["params"]["age_sec"] >= 743
    assert error.detail["params"]["max_age_sec"] == 300


def test_busy_robot_reports_the_session_it_is_running(monkeypatch):
    error = _arm(monkeypatch, _row(active_session_id=42))

    assert error.detail["code"] == "robot_busy"
    assert error.detail["params"]["session_id"] == 42


def test_offline_and_unknown_link_are_separate_codes(monkeypatch):
    offline = _arm(monkeypatch, _row(), seen="offline")
    assert offline.detail["code"] == "robot_offline"

    unknown = _arm(monkeypatch, _row(), seen="unknown")
    assert unknown.detail["code"] == "link_unknown"
