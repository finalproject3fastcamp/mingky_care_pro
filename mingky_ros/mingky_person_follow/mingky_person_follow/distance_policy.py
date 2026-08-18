"""QR 거리와 직전 상태로 안내 주행 속도 단계를 결정한다."""

from dataclasses import dataclass
import math


INACTIVE = 'inactive'
NORMAL = 'normal'
SLOW = 'slow'
WAITING = 'waiting'


@dataclass(frozen=True)
class DistancePolicy:
    slow_distance_m: float = 0.15
    stop_distance_m: float = 0.20
    hysteresis_m: float = 0.02

    def __post_init__(self) -> None:
        values = (
            self.slow_distance_m,
            self.stop_distance_m,
            self.hysteresis_m,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError('거리 정책값은 유한한 수여야 합니다.')
        if self.slow_distance_m <= 0.0:
            raise ValueError('slow_distance_m은 0보다 커야 합니다.')
        if self.stop_distance_m <= self.slow_distance_m:
            raise ValueError(
                'stop_distance_m은 slow_distance_m보다 커야 합니다.')
        if not 0.0 <= self.hysteresis_m < self.slow_distance_m:
            raise ValueError(
                'hysteresis_m은 0 이상 slow_distance_m 미만이어야 합니다.')


def select_mode(
    distance_m: float | None,
    previous: str,
    policy: DistancePolicy,
) -> str:
    """환자 거리를 정상·감속·대기로 변환한다.

    경계 근처에서 속도가 반복 변경되지 않도록 현재 상태에 따라
    복귀 기준을 hysteresis만큼 가까운 쪽으로 당긴다.
    """
    if distance_m is None or not math.isfinite(distance_m) or distance_m <= 0.0:
        return WAITING
    if previous == WAITING and distance_m > (
            policy.stop_distance_m - policy.hysteresis_m):
        return WAITING
    if distance_m >= policy.stop_distance_m:
        return WAITING
    if previous == SLOW and distance_m > (
            policy.slow_distance_m - policy.hysteresis_m):
        return SLOW
    if distance_m > policy.slow_distance_m:
        return SLOW
    return NORMAL


def estimate_visual_distance(
    anchor_distance_m: float | None,
    anchor_height_px: float | None,
    current_height_px: float | None,
) -> float | None:
    """QR 검증 시점의 YOLO 박스 크기로 짧은 가림 구간 거리를 보간한다."""
    values = (anchor_distance_m, anchor_height_px, current_height_px)
    if any(value is None or not math.isfinite(value) or value <= 0.0
           for value in values):
        return None
    return float(anchor_distance_m * anchor_height_px / current_height_px)
