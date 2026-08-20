"""손님 잠금(target_lock) 정책 테스트."""

from mingky_person_follow.target_lock import (
    bbox_center_distance,
    bbox_iou,
    pick_target,
)


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


def test_bbox_iou_for_same_and_separate_boxes() -> None:
    box = _det('p001', 100, 100)

    assert bbox_iou(box, box) == 1.0
    assert bbox_iou(box, _det('p002', 400, 400)) == 0.0


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


def test_required_class_loses_to_stronger_overlapping_class() -> None:
    detections = [
        {**_det('p003', 320, 240), 'conf': 0.70},
        {**_det('p001', 320, 240), 'conf': 0.84},
    ]

    target = pick_target(
        detections,
        None,
        screen_center=(320.0, 240.0),
        max_jump_px=200.0,
        required_class='p003',
        min_confidence=0.55,
        class_overlap_iou=0.5,
        class_confidence_margin=0.15,
    )

    assert target is None


def test_required_class_wins_with_clear_confidence_margin() -> None:
    detections = [
        {**_det('p003', 320, 240), 'conf': 0.82},
        {**_det('p001', 320, 240), 'conf': 0.41},
    ]

    target = pick_target(
        detections,
        None,
        screen_center=(320.0, 240.0),
        max_jump_px=200.0,
        required_class='p003',
        min_confidence=0.55,
        class_overlap_iou=0.5,
        class_confidence_margin=0.15,
    )

    assert target is detections[0]


def test_distant_other_class_does_not_make_target_ambiguous() -> None:
    detections = [
        {**_det('p003', 120, 240), 'conf': 0.75},
        {**_det('p001', 520, 240), 'conf': 0.90},
    ]

    target = pick_target(
        detections,
        None,
        screen_center=(320.0, 240.0),
        max_jump_px=200.0,
        required_class='p003',
        min_confidence=0.55,
        class_overlap_iou=0.5,
        class_confidence_margin=0.15,
    )

    assert target is detections[0]


def test_low_confidence_required_class_is_rejected() -> None:
    target = pick_target(
        [{**_det('p003', 320, 240), 'conf': 0.54}],
        None,
        screen_center=(320.0, 240.0),
        max_jump_px=200.0,
        required_class='p003',
        min_confidence=0.55,
    )

    assert target is None
