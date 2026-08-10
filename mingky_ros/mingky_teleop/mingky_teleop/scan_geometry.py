"""LaserScan 점을 센서 프레임에서 로봇 기준 프레임으로 옮긴다."""

import math


def transform_polar_point(
        angle: float,
        distance: float,
        *,
        translation: tuple[float, float, float],
        rotation: tuple[float, float, float, float],
) -> tuple[float, float]:
    """센서 기준 극좌표를 변환 뒤 로봇 기준 극좌표로 반환한다.

    ``rotation`` 은 ROS 메시지와 같은 ``(x, y, z, w)`` 순서다. 라이다
    장착이 평면 회전만 가진다는 가정을 두지 않고 쿼터니언 회전행렬의 xy
    성분을 사용한다. 센서의 위치 오프셋도 더한 뒤 로봇 원점 기준 거리와
    각도를 다시 계산한다.
    """
    sensor_x = distance * math.cos(angle)
    sensor_y = distance * math.sin(angle)

    qx, qy, qz, qw = rotation
    # 쿼터니언 회전행렬의 첫 두 행. 입력 점의 z는 0이다.
    robot_x = (
        translation[0]
        + (1.0 - 2.0 * (qy * qy + qz * qz)) * sensor_x
        + 2.0 * (qx * qy - qz * qw) * sensor_y
    )
    robot_y = (
        translation[1]
        + 2.0 * (qx * qy + qz * qw) * sensor_x
        + (1.0 - 2.0 * (qx * qx + qz * qz)) * sensor_y
    )

    return math.atan2(robot_y, robot_x), math.hypot(robot_x, robot_y)
