"""손님 잠금(target_lock) 정책 테스트."""

from mingky_person_follow.target_lock import bbox_center_distance, pick_target


def _det(cls: str, x: float, y: float, w: float = 100.0, h: float = 200.0):
    return {'cls': cls, 'conf': 0.9, 'x': x, 'y': y, 'w': w, 'h': h}


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
