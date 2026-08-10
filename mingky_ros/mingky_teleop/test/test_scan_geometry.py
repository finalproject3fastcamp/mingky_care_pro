"""관제 라이다 좌표계 변환 회귀 테스트."""

import math

from mingky_teleop.scan_geometry import transform_polar_point

import pytest


def test_identity_transform_preserves_measurement() -> None:
    """항등 TF는 센서 측정값을 바꾸지 않는다."""
    angle, distance = transform_polar_point(
        math.pi / 4.0,
        2.0,
        translation=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, 0.0, 1.0),
    )

    assert angle == pytest.approx(math.pi / 4.0)
    assert distance == pytest.approx(2.0)


def test_pinky_lidar_rotation_and_offset_are_applied() -> None:
    """Pinky의 180도 장착 회전과 센서 원점 오프셋을 함께 적용한다."""
    # Pinky TF: base_footprint -> rplidar_link = x -0.017m, yaw 180°.
    # 센서 뒤쪽(angle=pi)의 1m 측정은 로봇 앞쪽에 나타나야 한다.
    angle, distance = transform_polar_point(
        math.pi,
        1.0,
        translation=(-0.017, 0.0, 0.125),
        rotation=(0.0, 0.0, 1.0, 0.0),
    )

    assert angle == pytest.approx(0.0, abs=1e-9)
    assert distance == pytest.approx(0.983)


def test_sensor_translation_changes_angle_from_robot_origin() -> None:
    """센서 이동 오프셋은 로봇 원점에서 본 각도와 거리에도 반영된다."""
    angle, distance = transform_polar_point(
        0.0,
        1.0,
        translation=(0.0, 0.1, 0.0),
        rotation=(0.0, 0.0, 0.0, 1.0),
    )

    assert angle == pytest.approx(math.atan2(0.1, 1.0))
    assert distance == pytest.approx(math.hypot(1.0, 0.1))
