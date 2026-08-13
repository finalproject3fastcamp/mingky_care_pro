import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest

from app import qr_runtime
from app.routers import robots
from app.schemas import QrObservationIn


def setup_function():
    qr_runtime.reset()


def teardown_function():
    qr_runtime.reset()


def test_visible_observation_is_returned(monkeypatch):
    monkeypatch.setattr(robots.heartbeat, 'is_tracked', lambda robot_id: True)

    response = asyncio.run(robots.post_qr_observation(
        'pinky-01', QrObservationIn(visible=True, distance=0.42)))
    output = asyncio.run(robots.get_qr_observation('pinky-01'))

    assert response.status_code == 204
    assert output.visible is True
    assert output.distance == pytest.approx(0.42)


def test_stale_observation_is_hidden():
    qr_runtime.update('pinky-01', True, 0.42)
    old = qr_runtime._observations['pinky-01']
    qr_runtime._observations['pinky-01'] = qr_runtime.QrObservation(
        visible=old.visible,
        distance=old.distance,
        observed_at=datetime.now(timezone.utc) - timedelta(seconds=3),
    )

    output = asyncio.run(robots.get_qr_observation('pinky-01'))

    assert output.visible is False
    assert output.distance is None


def test_unconnected_robot_cannot_report(monkeypatch):
    monkeypatch.setattr(robots.heartbeat, 'is_tracked', lambda robot_id: False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(robots.post_qr_observation(
            'pinky-01', QrObservationIn(visible=False)))

    assert exc_info.value.status_code == 409
