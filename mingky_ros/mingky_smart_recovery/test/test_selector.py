import math

import pytest

from mingky_smart_recovery.selector import SelectorConfig, select_escape_candidates


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

    assert result[0].name == 'left'


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
