"""QR 거리 중심 환자 추적 노드의 세션·안전 회귀 테스트."""

import json
import time

from mingky_interfaces.msg import GuideState, QrObservation
from mingky_person_follow.distance_policy import INACTIVE, NORMAL, WAITING
from mingky_person_follow.person_follow_node import PersonFollowNode
import pytest
import rclpy


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def node():
    instance = PersonFollowNode()
    speeds = []
    states = []
    instance.speed_limit_pub.publish = lambda msg: speeds.append(msg.speed_limit)
    instance.follow_state_pub.publish = (
        lambda msg: states.append(json.loads(msg.data)))
    yield instance, speeds, states
    instance.destroy_node()


def _guide(*, state=GuideState.SESSION_GUIDING, patient='patient-001'):
    return GuideState(
        session_id=42,
        session_state=state,
        patient_id=patient,
    )


def _qr(patient='patient-001', distance=0.10, visible=True):
    return QrObservation(
        visible=visible,
        data=patient,
        distance=distance,
        center_x=320.0,
        center_y=240.0,
    )


def test_matching_qr_controls_distance_band(node) -> None:
    instance, speeds, states = node
    instance._on_guide_state(_guide())
    instance._on_qr_observation(_qr(distance=0.10))

    instance._control_tick()

    assert instance._mode == NORMAL
    assert speeds[-1] == pytest.approx(100.0)
    assert states[-1]['state'] == NORMAL
    assert states[-1]['session_id'] == 42
    assert states[-1]['source'] == 'qr'


def test_other_patient_qr_is_not_used(node) -> None:
    instance, speeds, states = node
    instance._on_guide_state(_guide())
    instance._on_qr_observation(_qr(patient='patient-999'))

    instance._control_tick()

    assert instance._mode == WAITING
    assert speeds[-1] == pytest.approx(0.1)
    assert states[-1]['distance'] is None


def test_stale_qr_fails_safe_to_waiting(node) -> None:
    instance, speeds, _ = node
    instance._on_guide_state(_guide())
    instance._on_qr_observation(_qr(distance=0.10))
    instance._control_tick()
    with instance._lock:
        instance._last_qr_at = time.monotonic() - instance.qr_stale_sec - 0.1
        instance._qr_visible = True

    instance._control_tick()

    assert instance._mode == WAITING
    assert speeds[-1] == pytest.approx(0.1)


def test_non_guiding_session_releases_speed_limit(node) -> None:
    instance, speeds, states = node
    instance._on_guide_state(_guide())
    instance._control_tick()
    instance._on_guide_state(_guide(state=GuideState.SESSION_ARRIVED))

    instance._control_tick()

    assert instance._mode == INACTIVE
    assert speeds[-1] == pytest.approx(100.0)
    assert states[-1]['session_id'] == 0


def test_visual_fallback_is_bounded_by_last_qr(node) -> None:
    instance, _, states = node
    instance._on_guide_state(_guide())
    instance._on_qr_observation(_qr(distance=0.18))
    with instance._lock:
        instance._qr_visible = False
        instance._last_visual_at = time.monotonic()
        instance._visual_visible = True
        instance._visual_anchor_distance_m = 0.18
        instance._visual_anchor_height_px = 200.0
        instance._visual_height_px = 200.0

    instance._control_tick()

    assert states[-1]['source'] == 'visual'
    assert states[-1]['distance'] == pytest.approx(0.18)

    with instance._lock:
        instance._last_qr_at = (
            time.monotonic() - instance.visual_fallback_sec - 0.1)
    instance._control_tick()
    assert instance._mode == WAITING
