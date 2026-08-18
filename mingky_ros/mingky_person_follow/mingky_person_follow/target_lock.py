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


def bbox_center_distance(a: Detection, b: Detection) -> float:
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def pick_target(
    detections: list[Detection],
    locked: Detection | None,
    *,
    screen_center: tuple[float, float],
    max_jump_px: float,
    required_class: str | None = None,
) -> Detection | None:
    """이번 프레임에서 계속 따라갈 대상을 고른다.

    - 처음 잠그는 경우(locked=None): 화면 중앙에 가장 가까운 검출을 새로 잠근다
      (클래스는 아직 정해진 게 없으니 아무거나 가능).
    - 이미 잠긴 대상이 있는 경우: **같은 클래스**이면서 직전 위치에서
      `max_jump_px` 이내인 검출 중, 가장 가까운 것만 그 대상으로 인정한다.
      클래스가 다른 검출은 아무리 가까워도 절대 후보에 넣지 않는다 --
      다른 손님으로 바뀌치기되는 걸 막는 핵심 조건이다.
    """
    if required_class:
        detections = [
            detection for detection in detections
            if detection['cls'] == required_class
        ]
    if not detections:
        return None

    if locked is None:
        cx, cy = screen_center
        return min(
            detections,
            key=lambda d: math.hypot(d['x'] - cx, d['y'] - cy))

    candidates = [
        d for d in detections
        if d['cls'] == locked['cls']
        and bbox_center_distance(d, locked) < max_jump_px
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda d: bbox_center_distance(d, locked))
