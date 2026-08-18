"""QR 거리 기반 주행 상태 정책 테스트."""

import pytest

from mingky_person_follow.distance_policy import (
    DistancePolicy,
    NORMAL,
    select_mode,
    SLOW,
    WAITING,
    estimate_visual_distance,
)


@pytest.fixture
def policy() -> DistancePolicy:
    return DistancePolicy(
        slow_distance_m=1.5,
        stop_distance_m=2.5,
        hysteresis_m=0.2,
    )


def test_test_defaults_stay_within_twenty_centimeters() -> None:
    policy = DistancePolicy()

    assert policy.slow_distance_m == pytest.approx(0.15)
    assert policy.stop_distance_m == pytest.approx(0.20)
    assert policy.hysteresis_m == pytest.approx(0.02)


@pytest.mark.parametrize(('distance', 'expected'), [
    (1.0, NORMAL),
    (2.0, SLOW),
    (3.0, WAITING),
    (None, WAITING),
])
def test_distance_selects_speed_band(policy, distance, expected) -> None:
    assert select_mode(distance, NORMAL, policy) == expected


def test_hysteresis_prevents_boundary_chatter(policy) -> None:
    assert select_mode(1.4, SLOW, policy) == SLOW
    assert select_mode(1.2, SLOW, policy) == NORMAL
    assert select_mode(2.4, WAITING, policy) == WAITING
    assert select_mode(2.2, WAITING, policy) == SLOW


def test_visual_box_scale_estimates_short_qr_occlusion() -> None:
    assert estimate_visual_distance(2.0, 200.0, 100.0) == pytest.approx(4.0)
    assert estimate_visual_distance(2.0, 200.0, None) is None


@pytest.mark.parametrize('kwargs', [
    {'slow_distance_m': 0.0},
    {'slow_distance_m': 2.0, 'stop_distance_m': 1.0},
    {'hysteresis_m': -0.1},
])
def test_invalid_policy_is_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        DistancePolicy(**kwargs)
