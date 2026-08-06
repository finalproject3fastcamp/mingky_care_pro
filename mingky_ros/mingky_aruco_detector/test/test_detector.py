"""Unit tests for calibration loading and ArUco pose estimation."""

from pathlib import Path

import cv2

from mingky_aruco_detector.detector import (
    ArucoPoseEstimator,
    CameraCalibration,
    load_ros_calibration,
    rotation_matrix_to_quaternion,
)

import numpy as np

import pytest


def test_load_ros_calibration() -> None:
    """Load a checked-in ROS camera_calibration YAML."""
    calibration_path = (
        Path(__file__).parents[2]
        / 'mingky_bringup/config/camera/pinky_15e2/rear_camera.yaml'
    )

    calibration = load_ros_calibration(calibration_path)

    assert (calibration.width, calibration.height) == (640, 480)
    assert calibration.camera_matrix.shape == (3, 3)
    assert calibration.distortion.shape == (5, 1)


def test_rotation_matrix_identity_quaternion() -> None:
    """Keep the ROS quaternion component order stable."""
    quaternion = rotation_matrix_to_quaternion(np.eye(3))

    assert quaternion == pytest.approx((0.0, 0.0, 0.0, 1.0))


def test_rejects_unknown_dictionary() -> None:
    """Reject typoed OpenCV dictionary names at startup."""
    with pytest.raises(ValueError, match='dictionary'):
        ArucoPoseEstimator('DICT_NOT_REAL', 0.026)


def test_detects_synthetic_marker_and_estimates_distance() -> None:
    """Estimate the distance of a known-size front-facing marker."""
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker = cv2.aruco.generateImageMarker(dictionary, 7, 200)
    image = np.full((480, 640), 255, dtype=np.uint8)
    image[140:340, 220:420] = marker
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

    detections = ArucoPoseEstimator('DICT_4X4_50', 0.026).detect(
        image, calibration)

    assert len(detections) == 1
    assert detections[0].marker_id == 7
    assert detections[0].translation[0] == pytest.approx(0.0, abs=0.001)
    assert detections[0].translation[1] == pytest.approx(0.0, abs=0.001)
    assert detections[0].translation[2] == pytest.approx(0.1176, abs=0.003)
    assert detections[0].distance == pytest.approx(0.1176, abs=0.003)
    assert detections[0].reprojection_error < 0.1
