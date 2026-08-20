"""QR 거리 중심 환자 추적 노드의 세션·안전 회귀 테스트."""

import json
import time

from mingky_interfaces.msg import GuideState, QrObservation
from nav_msgs.msg import Odometry
from sensor_msgs.msg import CompressedImage
from mingky_person_follow.distance_policy import INACTIVE, NORMAL, SLOW, WAITING
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
    processing = []
    instance.speed_limit_pub.publish = lambda msg: speeds.append(msg.speed_limit)
    instance.follow_state_pub.publish = (
        lambda msg: states.append(json.loads(msg.data)))
    instance.processing_active_pub.publish = (
        lambda msg: processing.append(msg.data))
    instance._test_processing_messages = processing
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
    with instance._lock:
        instance._guidance_started_at = (
            time.monotonic() - instance.initial_acquire_grace_sec - 0.1)

    instance._control_tick()

    assert instance._mode == WAITING
    assert speeds[-1] == pytest.approx(0.1)
    assert states[-1]['distance'] is None


def test_initial_acquisition_moves_slowly_before_first_detection(node) -> None:
    instance, speeds, states = node
    instance._on_guide_state(_guide())

    instance._control_tick()

    assert instance._mode == SLOW
    assert speeds[-1] == pytest.approx(35.0)
    assert states[-1]['source'] == 'acquiring'
    assert states[-1]['distance'] is None


def test_initial_acquisition_stops_after_time_or_distance_limit(node) -> None:
    instance, speeds, _ = node
    instance._on_guide_state(_guide())
    with instance._lock:
        instance._acquire_odom_seen = True
        instance._acquire_traveled_m = 0.31

    instance._control_tick()

    assert instance._mode == WAITING
    assert speeds[-1] == pytest.approx(0.1)


def test_initial_acquisition_counts_traveled_odom_distance(node) -> None:
    instance, _, _ = node
    first = Odometry()
    second = Odometry()
    second.pose.pose.position.x = 0.20
    instance._on_guide_state(_guide())

    instance._on_odom(first)
    instance._on_odom(second)

    assert instance._acquire_odom_seen is True
    assert instance._acquire_traveled_m == pytest.approx(0.20)


def test_stale_qr_fails_safe_to_waiting(node) -> None:
    instance, speeds, _ = node
    instance._on_guide_state(_guide())
    instance._on_qr_observation(_qr(distance=0.10))
    instance._control_tick()
    with instance._lock:
        instance._last_qr_at = (
            time.monotonic() - instance.tracking_grace_sec - 0.1)
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


def test_inactive_session_does_not_copy_camera_frame(node) -> None:
    instance, _, _ = node
    message = CompressedImage(data=[1, 2, 3, 4])

    instance._on_image(message)

    assert instance._latest_jpeg is None
    assert instance._latest_frame_at is None


def test_guiding_session_copies_next_camera_frame(node) -> None:
    instance, _, _ = node
    instance._on_guide_state(_guide())

    instance._on_image(CompressedImage(data=[1, 2, 3, 4]))

    assert instance._latest_jpeg == bytes([1, 2, 3, 4])
    assert instance._latest_frame_at is not None


def test_guidance_state_controls_camera_processing(node) -> None:
    instance, _, _ = node

    instance._on_guide_state(_guide())
    instance._on_guide_state(_guide(state=GuideState.SESSION_COMPLETED))

    assert instance._test_processing_messages == [True, False]


def test_visual_distance_works_without_a_recent_qr(node) -> None:
    instance, _, states = node
    instance._on_guide_state(_guide())
    with instance._lock:
        instance._last_visual_at = time.monotonic()
        instance._visual_visible = True
        instance._visual_complete = True
        instance._visual_distances.append(0.18)

    instance._control_tick()

    assert states[-1]['source'] == 'visual'
    assert states[-1]['distance'] == pytest.approx(0.18)


def test_near_partial_visual_always_moves_slowly(node) -> None:
    instance, speeds, states = node
    instance._on_guide_state(_guide())
    with instance._lock:
        instance._last_visual_at = time.monotonic()
        instance._visual_visible = True
        instance._visual_complete = False
        instance._partial_visual_distance_m = 0.34

    instance._control_tick()

    assert instance._mode == SLOW
    assert speeds[-1] == pytest.approx(35.0)
    assert states[-1]['source'] == 'partial_near'
    assert states[-1]['distance'] == pytest.approx(0.34)
    assert states[-1]['visual_visible'] is True


def test_short_tracking_loss_keeps_guiding_then_waits(node) -> None:
    instance, speeds, states = node
    instance._on_guide_state(_guide())
    instance._on_qr_observation(_qr(distance=0.10))
    instance._control_tick()

    with instance._lock:
        instance._qr_visible = False
        instance._last_qr_at = time.monotonic() - 1.5
    instance._control_tick()

    assert instance._mode == NORMAL
    assert speeds[-1] == pytest.approx(100.0)

    with instance._lock:
        instance._last_qr_at = time.monotonic() - 2.1
    instance._control_tick()

    assert instance._mode == WAITING
    assert speeds[-1] == pytest.approx(0.1)
    assert states[-1]['source'] == 'stale'


def test_detection_log_records_changes_and_rate_limits_repeats(
        node, monkeypatch) -> None:
    instance, _, _ = node
    messages = []
    monkeypatch.setattr(
        instance.get_logger(), 'info', lambda message: messages.append(message))
    clock = iter((10.0, 11.0, 12.0, 17.0, 18.0))
    monkeypatch.setattr(time, 'monotonic', lambda: next(clock))
    p003 = {
        'cls': 'p003', 'conf': 0.91,
        'x': 320.0, 'y': 240.0, 'w': 180.0, 'h': 326.7,
    }

    instance._log_detections([p003])
    instance._log_detections([p003])
    instance._log_detections([])
    instance._log_detections([])
    instance._log_detections([])

    assert messages == [
        'YOLO 검출: count=1, targets=[p003(conf=0.91, bbox=180x327px)]',
        'YOLO 미검출: targets=[]',
        'YOLO 미검출: targets=[]',
    ]
