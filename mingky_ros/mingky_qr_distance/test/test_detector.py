import cv2
from mingky_aruco_detector.detector import CameraCalibration
from mingky_qr_distance.detector import QrPoseEstimator
from mingky_qr_distance.qr_distance_node import QrDistanceNode
from mingky_interfaces.msg import GuideState
import numpy as np
import pytest


def test_qr_size_must_be_positive():
    with pytest.raises(ValueError):
        QrPoseEstimator(0.0)


def test_unsupported_image_shape_is_rejected():
    estimator = QrPoseEstimator(0.028)
    calibration = type('Calibration', (), {})()

    with pytest.raises(ValueError):
        estimator.detect(np.zeros((10,), dtype=np.uint8), calibration)


def test_detects_synthetic_qr_and_estimates_distance():
    qr = cv2.QRCodeEncoder_create().encode('patient-001')
    qr = cv2.resize(qr, (200, 200), interpolation=cv2.INTER_NEAREST)
    image = np.full((480, 640), 255, dtype=np.uint8)
    image[140:340, 220:420] = qr
    calibration = CameraCalibration(
        width=640,
        height=480,
        camera_matrix=np.asarray([
            [900.0, 0.0, 320.0],
            [0.0, 900.0, 240.0],
            [0.0, 0.0, 1.0],
        ]),
        distortion=np.zeros((5, 1)),
    )

    detection = QrPoseEstimator(0.028).detect(image, calibration)

    assert detection is not None
    assert detection.data == 'patient-001'
    assert detection.translation[0] == pytest.approx(0.0, abs=0.002)
    assert detection.translation[1] == pytest.approx(0.0, abs=0.002)
    assert detection.image_center[0] == pytest.approx(320.0, abs=1.0)
    assert detection.image_center[1] == pytest.approx(240.0, abs=1.0)
    # QRCodeEncoder 결과의 quiet zone은 자세 계산 모서리에 포함되지 않는다.
    # 200px 전체 중 실제 심볼 약 167px를 28mm로 보므로 약 15.1cm다.
    assert detection.distance == pytest.approx(0.151, abs=0.006)


def test_integrated_detector_skips_frames_outside_guidance():
    node = QrDistanceNode.__new__(QrDistanceNode)
    node._guiding = False
    node._frame_count = 0

    node._on_image(object())

    assert node._frame_count == 0


def test_guidance_state_enables_integrated_detector():
    node = QrDistanceNode.__new__(QrDistanceNode)
    node._guiding = False

    node._on_guide_state(GuideState(
        session_id=42,
        session_state=GuideState.SESSION_GUIDING,
    ))

    assert node._guiding is True
