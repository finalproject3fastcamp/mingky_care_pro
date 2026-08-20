"""손님 잠금(target_lock) 정책 테스트."""

from mingky_person_follow.target_lock import (
    bbox_center_distance, color_distance, pick_target,
)


def _det(
        cls: str, x: float, y: float, w: float = 100.0, h: float = 200.0,
        color: tuple[float, float, float] = (0.0, 0.0, 0.0)):
    return {'cls': cls, 'conf': 0.9, 'x': x, 'y': y, 'w': w, 'h': h,
            'color': color}


def test_first_lock_picks_detection_nearest_screen_center() -> None:
    detections = [_det('p001', 10, 10), _det('p002', 320, 240)]
    target = pick_target(
        detections, None, screen_center=(320.0, 240.0), max_jump_px=200.0)
    assert target['cls'] == 'p002'


def test_locked_target_ignores_other_class_even_if_closer() -> None:
    locked = _det('p002', 300, 240)
    detections = [
        _det('p001', 305, 245),  # 더 가깝지만 클래스가 다름
        _det('p002', 260, 240),  # 더 멀지만 같은 클래스
    ]
    target = pick_target(
        detections, locked, screen_center=(320.0, 240.0), max_jump_px=200.0)
    assert target['cls'] == 'p002'
    assert target['x'] == 260


def test_locked_target_returns_none_when_moved_too_far() -> None:
    locked = _det('p002', 300, 240)
    detections = [_det('p002', 550, 240)]  # 같은 클래스지만 250px 이동
    target = pick_target(
        detections, locked, screen_center=(320.0, 240.0), max_jump_px=200.0)
    assert target is None


def test_locked_target_returns_none_when_no_detections() -> None:
    locked = _det('p002', 300, 240)
    assert pick_target(
        [], locked, screen_center=(320.0, 240.0), max_jump_px=200.0) is None


def test_bbox_center_distance() -> None:
    a = _det('p001', 0, 0)
    b = _det('p001', 3, 4)
    assert bbox_center_distance(a, b) == 5.0


def test_reacquire_keeps_verified_class_without_old_position() -> None:
    detections = [_det('p001', 320, 240), _det('p002', 500, 240)]

    target = pick_target(
        detections,
        None,
        screen_center=(320.0, 240.0),
        max_jump_px=200.0,
        required_class='p002',
    )

    assert target['cls'] == 'p002'


def test_color_distance() -> None:
    a = _det('p001', 0, 0, color=(255.0, 0.0, 0.0))
    b = _det('p001', 0, 0, color=(255.0, 3.0, 4.0))
    assert color_distance(a, b) == 5.0


def test_wrong_class_label_rejected_when_color_jumps() -> None:
    """YOLO가 라벨을 잘못 붙여도(같은 클래스로 오분류) 색상으로 걸러진다."""
    locked = _det('p001', 300, 240, color=(230.0, 180.0, 190.0))  # 분홍 돼지
    detections = [
        # 위치·클래스는 통과하지만 실제로는 다른 인형(노란 병아리 색).
        _det('p001', 320, 245, color=(230.0, 200.0, 40.0)),
    ]
    target = pick_target(
        detections, locked, screen_center=(320.0, 240.0),
        max_jump_px=200.0, max_color_distance=60.0,
    )
    assert target is None


def test_matching_color_within_tolerance_is_accepted() -> None:
    locked = _det('p001', 300, 240, color=(230.0, 180.0, 190.0))
    detections = [
        # 약간의 조명 변화 정도의 작은 색 편차는 통과해야 한다.
        _det('p001', 320, 245, color=(225.0, 178.0, 188.0)),
    ]
    target = pick_target(
        detections, locked, screen_center=(320.0, 240.0),
        max_jump_px=200.0, max_color_distance=60.0,
    )
    assert target is not None


def test_color_check_skipped_when_not_provided() -> None:
    """max_color_distance를 안 주면(기존 호출자) 색상 검사를 건너뛴다."""
    locked = _det('p001', 300, 240, color=(230.0, 180.0, 190.0))
    detections = [_det('p001', 320, 245, color=(0.0, 0.0, 0.0))]
    target = pick_target(
        detections, locked, screen_center=(320.0, 240.0), max_jump_px=200.0)
    assert target is not None
