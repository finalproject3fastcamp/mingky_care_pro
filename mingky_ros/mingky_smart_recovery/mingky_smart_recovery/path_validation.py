"""Adaptive Recovery 경로가 실제 탈출 이동을 포함하는지 검증한다."""

from dataclasses import dataclass
import math
from typing import Sequence


@dataclass(frozen=True)
class RecoveryPathValidation:
    valid: bool
    start_error_m: float
    endpoint_error_m: float
    displacement_m: float
    path_length_m: float


def validate_recovery_path(
    points: Sequence[tuple[float, float]],
    *,
    expected_start: tuple[float, float],
    expected_goal: tuple[float, float],
    requested_distance_m: float,
    start_tolerance_m: float = 0.10,
    endpoint_tolerance_m: float = 0.05,
    minimum_progress_ratio: float = 0.70,
) -> RecoveryPathValidation:
    """Planner의 근접 성공이나 사실상 제자리인 경로를 거부한다."""
    if len(points) < 2:
        return RecoveryPathValidation(False, math.inf, math.inf, 0.0, 0.0)

    start = points[0]
    end = points[-1]
    start_error = math.hypot(
        start[0] - expected_start[0], start[1] - expected_start[1])
    endpoint_error = math.hypot(
        end[0] - expected_goal[0], end[1] - expected_goal[1])
    displacement = math.hypot(end[0] - start[0], end[1] - start[1])
    path_length = sum(
        math.hypot(next_x - x, next_y - y)
        for (x, y), (next_x, next_y) in zip(points, points[1:])
    )
    minimum_progress = max(0.0, requested_distance_m * minimum_progress_ratio)
    valid = (
        start_error <= start_tolerance_m
        and endpoint_error <= endpoint_tolerance_m
        and displacement >= minimum_progress
        and path_length >= minimum_progress
    )
    return RecoveryPathValidation(
        valid,
        start_error,
        endpoint_error,
        displacement,
        path_length,
    )
