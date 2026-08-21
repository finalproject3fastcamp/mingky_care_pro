import math

import pytest

from mingky_smart_recovery.selector import (
    EscapeCandidate,
    SelectorConfig,
    candidate_to_map,
    select_diverse_candidates,
    select_escape_candidates,
)


def scan(default: float = 0.20) -> list[float]:
    return [default] * 360


def open_sector(ranges: list[float], center_deg: int, distance: float) -> None:
    for offset in range(-15, 16):
        ranges[(center_deg + offset) % 360] = distance


def candidates(ranges: list[float], **kwargs):
    return select_escape_candidates(
        ranges,
        angle_min=0.0,
        angle_increment=math.radians(1.0),
        range_min=0.05,
        range_max=12.0,
        **kwargs,
    )


def test_prefers_open_direction() -> None:
    ranges = scan()
    open_sector(ranges, 45, 1.4)
    open_sector(ranges, 315, 0.8)

    result = candidates(ranges)

    assert result[0].name == 'forward_left'
    assert result[0].clearance_m == pytest.approx(1.4)
    assert result[0].distance_m == pytest.approx(0.35)


def test_rejects_direction_without_escape_distance() -> None:
    result = candidates(scan(default=0.25))

    assert result == []


def test_previous_failure_moves_next_candidate_up() -> None:
    ranges = scan()
    open_sector(ranges, 45, 1.0)
    open_sector(ranges, 315, 0.95)

    first = candidates(ranges)
    second = candidates(ranges, failures={'forward_left': 1})

    assert first[0].name == 'forward_left'
    assert second[0].name == 'forward_right'


def test_goal_alignment_breaks_equal_clearance_tie() -> None:
    ranges = scan()
    open_sector(ranges, 45, 1.0)
    open_sector(ranges, 315, 1.0)

    result = candidates(ranges, goal_bearing_rad=math.radians(-60.0))

    assert result[0].name == 'forward_right'


def test_distance_is_clamped_by_safety_margin() -> None:
    ranges = scan()
    open_sector(ranges, 0, 0.40)

    result = candidates(ranges)

    assert result[0].name == 'forward'
    assert result[0].distance_m == pytest.approx(0.26)


def test_ignores_invalid_scan_values() -> None:
    ranges = [math.inf] * 360
    open_sector(ranges, 90, 0.8)
    ranges[90] = math.nan

    result = candidates(ranges)

    assert result[0].clearance_m == pytest.approx(0.8)
    assert math.radians(75) <= result[0].bearing_rad <= math.radians(105)


@pytest.mark.parametrize(
    'config',
    [
        SelectorConfig(),
        SelectorConfig(clearance_quantile=0.0),
        SelectorConfig(clearance_quantile=1.0),
    ],
)
def test_configuration_accepts_quantile_edges(config: SelectorConfig) -> None:
    assert config.minimum_distance_m > 0.0


def test_candidate_is_rotated_from_robot_frame_into_map_frame() -> None:
    candidate = EscapeCandidate(
        name='forward',
        bearing_rad=0.0,
        distance_m=0.35,
        x_m=0.35,
        y_m=0.0,
        clearance_m=1.0,
        score=1.0,
    )

    x, y, yaw = candidate_to_map(
        candidate,
        robot_x=2.0,
        robot_y=3.0,
        robot_yaw=math.pi / 2.0,
    )

    assert x == pytest.approx(2.0)
    assert y == pytest.approx(3.35)
    assert yaw == pytest.approx(math.pi / 2.0)


def test_candidate_map_yaw_is_normalized() -> None:
    candidate = EscapeCandidate(
        name='left',
        bearing_rad=math.pi / 2.0,
        distance_m=0.2,
        x_m=0.0,
        y_m=0.2,
        clearance_m=1.0,
        score=1.0,
    )

    _, _, yaw = candidate_to_map(
        candidate,
        robot_x=0.0,
        robot_y=0.0,
        robot_yaw=math.pi,
    )

    assert yaw == pytest.approx(-math.pi / 2.0)


def test_full_open_scan_creates_24_directions() -> None:
    result = candidates(scan(default=1.0))

    assert len(result) == 24
    assert {candidate.name for candidate in result} >= {
        'forward', 'left_015', 'right_015', 'left_030', 'right_030', 'rear'}


def test_reverse_candidates_can_be_disabled() -> None:
    result = candidates(
        scan(default=1.0), config=SelectorConfig(allow_reverse=False))

    assert result
    assert all(math.cos(candidate.bearing_rad) >= -1.0e-9 for candidate in result)
    assert all(not candidate.name.startswith('rear') for candidate in result)


def test_forward_candidates_can_be_blocked_for_low_obstacle() -> None:
    result = candidates(
        scan(default=1.0), config=SelectorConfig(block_forward=True))

    assert result
    assert all(math.cos(candidate.bearing_rad) <= 1.0e-9
               for candidate in result)
    assert {'left', 'right', 'rear'} <= {
        candidate.name for candidate in result}


def test_finds_escape_between_old_45_degree_directions() -> None:
    ranges = scan()
    open_sector(ranges, 30, 1.0)

    result = candidates(ranges)

    assert result[0].name == 'left_030'


def test_diverse_selection_avoids_nearly_identical_directions() -> None:
    ranked = [
        EscapeCandidate('forward', math.radians(0), 0.3, 0.3, 0.0, 1.0, 4.0),
        EscapeCandidate('left_015', math.radians(15), 0.3, 0.29, 0.08, 1.0, 3.0),
        EscapeCandidate('left_030', math.radians(30), 0.3, 0.26, 0.15, 1.0, 2.0),
        EscapeCandidate('forward_right', math.radians(-45), 0.3, 0.21, -0.21, 1.0, 1.0),
    ]

    result = select_diverse_candidates(
        ranked, limit=3, minimum_separation_rad=math.radians(30))

    assert [candidate.name for candidate in result] == [
        'forward', 'left_030', 'forward_right']
