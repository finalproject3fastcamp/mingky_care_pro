"""QR·YOLO 추정 거리와 직전 상태로 주행 속도를 결정한다."""

from dataclasses import dataclass
import math


INACTIVE = 'inactive'
NORMAL = 'normal'
SLOW = 'slow'
WAITING = 'waiting'


@dataclass(frozen=True)
class DistancePolicy:
    slow_distance_m: float = 0.15
    stop_distance_m: float = 0.30
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
    """QR 보정 시점의 YOLO 박스 크기로 현재 거리를 추정한다."""
    values = (anchor_distance_m, anchor_height_px, current_height_px)
    if any(value is None or not math.isfinite(value) or value <= 0.0
           for value in values):
        return None
    return float(anchor_distance_m * anchor_height_px / current_height_px)


def estimate_bbox_distance(
    focal_y_px: float | None,
    target_height_m: float,
    bbox_height_px: float | None,
) -> float | None:
    """실제 높이와 카메라 초점거리로 YOLO 박스의 절대거리를 근사한다."""
    values = (focal_y_px, target_height_m, bbox_height_px)
    if any(value is None or not math.isfinite(value) or value <= 0.0
           for value in values):
        return None
    return float(focal_y_px * target_height_m / bbox_height_px)


def estimate_near_partial_bbox_distance(
    focal_y_px: float | None,
    target_height_m: float,
    bbox_height_px: float | None,
    confidence: float,
    *,
    min_confidence: float,
    max_distance_m: float,
) -> float | None:
    """가까이에서 잘린 YOLO 박스를 보수적으로 거리 관측으로 인정한다.

    잘린 박스 높이는 실제 전체 박스보다 작으므로 산출 거리는 실제보다
    멀게 나온다. 그 보수적인 거리도 한계 이내이고 신뢰도가 충분할 때만
    저속 시야 확보에 사용한다.
    """
    values = (confidence, min_confidence, max_distance_m)
    if not all(math.isfinite(value) for value in values):
        return None
    if min_confidence < 0.0 or confidence < min_confidence:
        return None
    if max_distance_m <= 0.0:
        return None
    distance = estimate_bbox_distance(
        focal_y_px, target_height_m, bbox_height_px)
    if distance is None or distance > max_distance_m:
        return None
    return distance


def bbox_is_complete(
    *,
    center_y_px: float,
    height_px: float,
    image_height_px: float,
    edge_margin_px: float,
) -> bool:
    """상·하단이 화면에 잘린 박스는 거리 추정에서 제외한다."""
    values = (center_y_px, height_px, image_height_px, edge_margin_px)
    if not all(math.isfinite(value) for value in values):
        return False
    if height_px <= 0.0 or image_height_px <= 0.0 or edge_margin_px < 0.0:
        return False
    top = center_y_px - height_px / 2.0
    bottom = center_y_px + height_px / 2.0
    return top > edge_margin_px and bottom < image_height_px - edge_margin_px
