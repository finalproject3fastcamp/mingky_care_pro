"""ROS와 독립적인 ArUco 검출·자세 추정 로직."""

from dataclasses import dataclass
from pathlib import Path

import cv2

import numpy as np

import yaml


@dataclass(frozen=True)
class CameraCalibration:
    """Intrinsic calibration for one fixed image resolution."""

    width: int
    height: int
    camera_matrix: np.ndarray
    distortion: np.ndarray


@dataclass(frozen=True)
class Detection:
    """One marker pose expressed in the camera optical frame."""

    marker_id: int
    translation: np.ndarray
    quaternion: tuple[float, float, float, float]
    distance: float
    reprojection_error: float


def load_ros_calibration(path: str | Path) -> CameraCalibration:
    """camera_calibration 형식의 ROS YAML을 읽는다."""
    calibration_path = Path(path).expanduser()
    if not calibration_path.is_file():
        raise FileNotFoundError(f'캘리브레이션 파일을 찾을 수 없습니다: {calibration_path}')

    with calibration_path.open(encoding='utf-8') as stream:
        data = yaml.safe_load(stream) or {}

    try:
        width = int(data['image_width'])
        height = int(data['image_height'])
        camera_matrix = np.asarray(
            data['camera_matrix']['data'], dtype=np.float64).reshape(3, 3)
        distortion = np.asarray(
            data['distortion_coefficients']['data'],
            dtype=np.float64,
        ).reshape(-1, 1)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'잘못된 ROS 카메라 YAML입니다: {calibration_path}') from exc

    if width <= 0 or height <= 0 or distortion.size < 4:
        raise ValueError(f'유효하지 않은 캘리브레이션 값입니다: {calibration_path}')
    if (not np.all(np.isfinite(camera_matrix))
            or not np.all(np.isfinite(distortion))):
        raise ValueError(f'캘리브레이션 값에 NaN 또는 inf가 있습니다: {calibration_path}')

    return CameraCalibration(width, height, camera_matrix, distortion)


def rotation_matrix_to_quaternion(
        matrix: np.ndarray) -> tuple[float, float, float, float]:
    """3x3 회전행렬을 ROS 순서(x, y, z, w)의 단위 쿼터니언으로 바꾼다."""
    m = np.asarray(matrix, dtype=np.float64)
    trace = float(np.trace(m))

    if trace > 0.0:
        scale = np.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (m[2, 1] - m[1, 2]) / scale
        y = (m[0, 2] - m[2, 0]) / scale
        z = (m[1, 0] - m[0, 1]) / scale
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        scale = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / scale
        x = 0.25 * scale
        y = (m[0, 1] + m[1, 0]) / scale
        z = (m[0, 2] + m[2, 0]) / scale
    elif m[1, 1] > m[2, 2]:
        scale = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / scale
        x = (m[0, 1] + m[1, 0]) / scale
        y = 0.25 * scale
        z = (m[1, 2] + m[2, 1]) / scale
    else:
        scale = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / scale
        x = (m[0, 2] + m[2, 0]) / scale
        y = (m[1, 2] + m[2, 1]) / scale
        z = 0.25 * scale

    quaternion = np.asarray([x, y, z, w], dtype=np.float64)
    quaternion /= np.linalg.norm(quaternion)
    return tuple(float(value) for value in quaternion)


class ArucoPoseEstimator:
    """정사각 ArUco 마커를 검출하고 optical frame 기준 자세를 계산한다."""

    def __init__(self, dictionary_name: str, marker_length: float) -> None:
        """Configure a predefined dictionary and physical marker size."""
        if marker_length <= 0.0:
            raise ValueError('marker_length는 0보다 커야 합니다.')
        dictionary_id = getattr(cv2.aruco, dictionary_name, None)
        if dictionary_id is None or not dictionary_name.startswith('DICT_'):
            raise ValueError(f'지원하지 않는 ArUco dictionary: {dictionary_name}')

        dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        parameters = cv2.aruco.DetectorParameters()
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self._detector = cv2.aruco.ArucoDetector(dictionary, parameters)

        half = marker_length / 2.0
        # SOLVEPNP_IPPE_SQUARE가 요구하는 순서: 좌상, 우상, 우하, 좌하.
        self._object_points = np.asarray([
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ], dtype=np.float32)

    def detect(self, image: np.ndarray,
               calibration: CameraCalibration) -> list[Detection]:
        """Return all valid marker poses found in an image."""
        if image.ndim == 2:
            gray = image
        elif image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            raise ValueError(f'지원하지 않는 이미지 shape: {image.shape}')

        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return []

        detections = []
        for marker_id, image_points in zip(ids.reshape(-1), corners):
            points = np.asarray(image_points, dtype=np.float32).reshape(4, 2)
            ok, rotation_vector, translation_vector = cv2.solvePnP(
                self._object_points,
                points,
                calibration.camera_matrix,
                calibration.distortion,
                flags=cv2.SOLVEPNP_IPPE_SQUARE,
            )
            if not ok:
                continue

            translation = translation_vector.reshape(3).astype(np.float64)
            if not np.all(np.isfinite(translation)) or translation[2] <= 0.0:
                continue
            rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
            quaternion = rotation_matrix_to_quaternion(rotation_matrix)

            projected, _ = cv2.projectPoints(
                self._object_points,
                rotation_vector,
                translation_vector,
                calibration.camera_matrix,
                calibration.distortion,
            )
            projected = projected.reshape(4, 2)
            reprojection_error = float(np.sqrt(np.mean(np.sum(
                (points - projected) ** 2, axis=1))))

            detections.append(Detection(
                marker_id=int(marker_id),
                translation=translation,
                quaternion=quaternion,
                distance=float(np.linalg.norm(translation)),
                reprojection_error=reprojection_error,
            ))

        return sorted(detections, key=lambda detection: detection.marker_id)
