"""관제 진단 레이어가 전송 주기보다 자주 계산되지 않는지 확인한다."""

import threading
from types import SimpleNamespace

from mingky_teleop.teleop_bridge import TeleopBridge


def _bridge_without_ros() -> TeleopBridge:
    bridge = TeleopBridge.__new__(TeleopBridge)
    bridge._scan_snapshot_requested = threading.Event()
    bridge._particle_snapshot_requested = threading.Event()
    bridge._scan = None
    bridge._particles = None
    bridge.diag_interval = 1.0
    return bridge


def test_scan_callback_returns_before_reading_unrequested_message() -> None:
    bridge = _bridge_without_ros()

    bridge._on_scan(object())

    assert bridge._scan is None


def test_particle_callback_returns_before_reading_unrequested_message() -> None:
    bridge = _bridge_without_ros()

    bridge._on_particles(object())

    assert bridge._particles is None


def test_requested_particle_snapshot_is_built_only_once() -> None:
    bridge = _bridge_without_ros()
    bridge._particle_snapshot_requested.set()
    particle = SimpleNamespace(
        pose=SimpleNamespace(position=SimpleNamespace(x=1.2345, y=-0.4567)))

    bridge._on_particles(SimpleNamespace(particles=[particle]))

    assert bridge._particles == {
        'type': 'particles', 'points': [[1.234, -0.457]]}
    assert bridge._particle_snapshot_requested.is_set() is False


def test_scan_is_requested_only_immediately_before_diagnostic_send() -> None:
    bridge = _bridge_without_ros()

    bridge._request_diagnostic_snapshots(0.79)
    assert bridge._scan_snapshot_requested.is_set() is False

    bridge._request_diagnostic_snapshots(0.80)
    assert bridge._scan_snapshot_requested.is_set() is True
