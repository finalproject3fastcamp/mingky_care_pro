"""ROS와 독립적인 QR 검출·자세 추정 로직."""

from dataclasses import dataclass

import cv2
from mingky_aruco_detector.detector import (
    CameraCalibration,
    rotation_matrix_to_quaternion,
)
import numpy as np


@dataclass(frozen=True)
class QrDetection:
    """한 QR의 내용과 optical frame 기준 자세."""

    data: str
    translation: np.ndarray
    quaternion: tuple[float, float, float, float]
    distance: float
    reprojection_error: float
    image_center: tuple[float, float]


class QrPoseEstimator:
    """정사각 QR 심볼의 네 모서리로 3차원 자세를 계산한다."""

    def __init__(self, qr_size: float) -> None:
        if qr_size <= 0.0:
            raise ValueError('qr_size는 0보다 커야 합니다.')
        self._detector = cv2.QRCodeDetector()
        half = qr_size / 2.0
        # OpenCV QRCodeDetector의 순서(좌상, 우상, 우하, 좌하)에 맞춘다.
        self._object_points = np.asarray([
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ], dtype=np.float32)

    def detect(
        self, image: np.ndarray, calibration: CameraCalibration,
    ) -> QrDetection | None:
        """검출된 QR의 내용과 자세를 반환한다."""
        if image.ndim == 2:
            gray = image
        elif image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            raise ValueError(f'지원하지 않는 이미지 shape: {image.shape}')

        data, image_points, _ = self._detector.detectAndDecode(gray)
        if image_points is None or not data.strip():
            return None
        points = np.asarray(image_points, dtype=np.float32).reshape(4, 2)
        ok, rotation_vector, translation_vector = cv2.solvePnP(
            self._object_points,
            points,
            calibration.camera_matrix,
            calibration.distortion,
            flags=cv2.SOLVEPNP_IPPE_SQUARE,
        )
        if not ok:
            return None

        translation = translation_vector.reshape(3).astype(np.float64)
        if not np.all(np.isfinite(translation)) or translation[2] <= 0.0:
            return None
        rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
        projected, _ = cv2.projectPoints(
            self._object_points,
            rotation_vector,
            translation_vector,
            calibration.camera_matrix,
            calibration.distortion,
        )
        error = float(np.sqrt(np.mean(np.sum(
            (points - projected.reshape(4, 2)) ** 2, axis=1))))
        return QrDetection(
            data=data.strip(),
            translation=translation,
            quaternion=rotation_matrix_to_quaternion(rotation_matrix),
            distance=float(np.linalg.norm(translation)),
            reprojection_error=error,
            image_center=(
                float(np.mean(points[:, 0])),
                float(np.mean(points[:, 1])),
            ),
        )
