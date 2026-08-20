"""화면 안에서 "같은 손님"을 계속 같은 손님으로 잡아두는 잠금 로직.

병원 안내 시나리오에서는 인형(손님) 종류가 여러 개(p001/p002/p003)라, 위치만
보고 잠그면 손님 A가 화면을 벗어나고 비슷한 자리에 손님 B가 들어왔을 때
그대로 B를 A인 척 계속 따라가 버린다 -- 실제로 이 프로젝트에서 겪은 문제다.
그래서 잠금 후보는 반드시 "직전에 잠겼던 것과 같은 클래스"만 본다. 클래스가
다르면 위치가 아무리 가까워도 후보에서 제외한다.

같은 클래스 안에서도 순간적으로 검출이 흔들리는 걸 다루기 위해, 위치가
`max_jump_px`픽셀 이상 벗어나면 (예: 화면을 가로질러 순간이동한 것처럼
보이면) 다른 개체로 보고 버린다.
"""

import math
from typing import TypedDict


class Detection(TypedDict):
    """추론 서버가 돌려주는 검출 하나. 좌표는 이미지 픽셀 기준 중심점."""

    cls: str
    conf: float
    x: float
    y: float
    w: float
    h: float
    image_width: float
    image_height: float


def bbox_center_distance(a: Detection, b: Detection) -> float:
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def bbox_iou(a: Detection, b: Detection) -> float:
    """두 중심점 형식 박스의 IoU를 계산한다."""
    a_left = a['x'] - a['w'] / 2.0
    a_top = a['y'] - a['h'] / 2.0
    a_right = a['x'] + a['w'] / 2.0
    a_bottom = a['y'] + a['h'] / 2.0
    b_left = b['x'] - b['w'] / 2.0
    b_top = b['y'] - b['h'] / 2.0
    b_right = b['x'] + b['w'] / 2.0
    b_bottom = b['y'] + b['h'] / 2.0

    intersection_width = max(0.0, min(a_right, b_right) - max(a_left, b_left))
    intersection_height = max(
        0.0, min(a_bottom, b_bottom) - max(a_top, b_top))
    intersection = intersection_width * intersection_height
    a_area = max(0.0, a['w']) * max(0.0, a['h'])
    b_area = max(0.0, b['w']) * max(0.0, b['h'])
    union = a_area + b_area - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def _has_clear_class_winner(
    candidate: Detection,
    detections: list[Detection],
    *,
    overlap_iou: float,
    confidence_margin: float,
) -> bool:
    """같은 물체의 다른 클래스 가설보다 충분히 강한지 확인한다.

    Ultralytics의 기본 NMS는 클래스별로 동작하므로 동일한 물체 위치에
    p001/p002/p003 박스가 함께 남을 수 있다. 세션 클래스와 이름이 같다는
    이유만으로 낮은 점수 박스를 고르지 않도록 겹치는 타 클래스와 비교한다.
    """
    for other in detections:
        if other is candidate or other['cls'] == candidate['cls']:
            continue
        if bbox_iou(candidate, other) < overlap_iou:
            continue
        if candidate['conf'] < other['conf'] + confidence_margin:
            return False
    return True


def pick_target(
    detections: list[Detection],
    locked: Detection | None,
    *,
    screen_center: tuple[float, float],
    max_jump_px: float,
    required_class: str | None = None,
    min_confidence: float = 0.0,
    class_overlap_iou: float = 0.5,
    class_confidence_margin: float = 0.0,
) -> Detection | None:
    """이번 프레임에서 계속 따라갈 대상을 고른다.

    - 처음 잠그는 경우(locked=None): 화면 중앙에 가장 가까운 검출을 새로 잠근다
      (클래스는 아직 정해진 게 없으니 아무거나 가능).
    - 이미 잠긴 대상이 있는 경우: **같은 클래스**이면서 직전 위치에서
      `max_jump_px` 이내인 검출 중, 가장 가까운 것만 그 대상으로 인정한다.
      클래스가 다른 검출은 아무리 가까워도 절대 후보에 넣지 않는다 --
      다른 손님으로 바뀌치기되는 걸 막는 핵심 조건이다.
    """
    required = required_class or (locked['cls'] if locked else None)
    candidates = [
        detection for detection in detections
        if detection['conf'] >= min_confidence
        and (not required or detection['cls'] == required)
        and _has_clear_class_winner(
            detection,
            detections,
            overlap_iou=class_overlap_iou,
            confidence_margin=class_confidence_margin,
        )
    ]
    if not candidates:
        return None

    if locked is None:
        cx, cy = screen_center
        return min(
            candidates,
            key=lambda d: math.hypot(d['x'] - cx, d['y'] - cy))

    candidates = [
        d for d in candidates
        if d['cls'] == locked['cls']
        and bbox_center_distance(d, locked) < max_jump_px
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda d: bbox_center_distance(d, locked))
