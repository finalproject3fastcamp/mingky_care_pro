"""충전소 복귀 중에는 QR 세션 행을 만들지 않는지 검증."""

import asyncio

from fastapi import HTTPException
import pytest

from app import arming, robot_runtime
from app.routers import qr
from app.schemas import QrScanRequest


class Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class Connection:
    def __init__(self):
        self.created = False

    def transaction(self):
        return Transaction()

    async def fetchrow(self, query, *args):
        if 'FROM patients' in query:
            return {
                'patient_id': 'patient-001',
                'name': '홍길동',
                'gender': 'M',
                'birth_date': None,
                'condition_name': '검진',
                'condition_id': 1,
            }
        return None

    async def fetchval(self, query, *args):
        self.created = True
        return 101


class AcquireContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return AcquireContext(self.connection)


def test_scan_does_not_create_session_while_returning(monkeypatch):
    connection = Connection()
    monkeypatch.setattr(qr, 'get_pool', lambda: Pool(connection))
    arming._armed.clear()
    robot_runtime.reset()
    arming.arm('pinky-01')
    robot_runtime.update(
        'pinky-01', 'active', False, returning_to_dock=True)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(qr.scan(QrScanRequest(
            patient_id='patient-001', robot_id='pinky-01')))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == 'robot returning to charging station'
    assert connection.created is False
    arming._armed.clear()
    robot_runtime.reset()
