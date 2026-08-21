import math

from mingky_low_obstacle.fusion import (
    FusionConfig,
    limit_forward_velocity,
    LowObstacleFilter,
    nearest_lidar_in_ultrasonic_cone,
)
import pytest


def _update(filter_, sonar, lidar=0.80):
    return filter_.update(
        sonar,
        min_range_m=0.02,
        max_range_m=0.97,
        lidar_range_m=lidar,
        lidar_fresh=True,
    )


def test_median_and_three_of_five_reject_one_spike():
    filter_ = LowObstacleFilter()

    decisions = [_update(filter_, value) for value in (0.18, 0.19, 0.63, 0.18, 0.19)]

    assert decisions[-1].filtered_range_m == pytest.approx(0.19)
    assert decisions[-1].state == 'CONFIRMED'
    assert decisions[-1].output_range_m == pytest.approx(0.19)


def test_lidar_agreement_does_not_create_low_obstacle():
    filter_ = LowObstacleFilter()

    decisions = [_update(filter_, 0.18, lidar=0.20) for _ in range(5)]

    assert decisions[-1].state == 'CLEAR'
    assert decisions[-1].output_range_m == pytest.approx(0.97)


def test_hysteresis_requires_three_clear_observations():
    filter_ = LowObstacleFilter()
    for _ in range(5):
        decision = _update(filter_, 0.20)
    assert decision.low_obstacle_confirmed

    # The first clear raw sample is still hidden by the causal median-of-three.
    assert _update(filter_, 0.36).low_obstacle_confirmed
    assert _update(filter_, 0.36).low_obstacle_confirmed
    assert _update(filter_, 0.36).low_obstacle_confirmed
    decision = _update(filter_, 0.36)
    assert not decision.low_obstacle_confirmed
    assert decision.state == 'CLEAR'


def test_two_near_samples_block_forward_without_waiting_for_map_confirmation():
    filter_ = LowObstacleFilter()

    assert _update(filter_, 0.06).state == 'UNCERTAIN'
    decision = _update(filter_, 0.06)

    assert decision.state == 'FORWARD_BLOCKED'
    assert decision.forward_speed_limit_mps == 0.0


def test_stale_lidar_does_not_clear_or_create_obstacle():
    filter_ = LowObstacleFilter()

    decision = filter_.update(
        0.12,
        min_range_m=0.02,
        max_range_m=0.97,
        lidar_range_m=None,
        lidar_fresh=False,
    )

    assert decision.state == 'STALE_LIDAR'
    assert decision.output_range_m is None


def test_stale_sensor_keeps_confirmed_near_forward_limit():
    filter_ = LowObstacleFilter()
    for _ in range(5):
        _update(filter_, 0.06)

    decision = filter_.stale_decision(0.80)

    assert decision.low_obstacle_confirmed
    assert decision.output_range_m is None
    assert decision.forward_speed_limit_mps == 0.0


def test_scan_points_are_transformed_before_cone_comparison():
    # Scan zero degrees points along +x. Rotating the scan frame by pi puts that
    # point behind the ultrasonic sensor, while the pi ray becomes its front.
    ranges = [0.20, 0.80]
    result = nearest_lidar_in_ultrasonic_cone(
        ranges,
        angle_min=0.0,
        angle_increment=math.pi,
        range_min=0.02,
        range_max=1.0,
        scan_to_ultrasonic_x=0.0,
        scan_to_ultrasonic_y=0.0,
        scan_to_ultrasonic_yaw=math.pi,
        ultrasonic_fov=0.26,
    )

    assert result == pytest.approx(0.80)


def test_forward_limiter_preserves_reverse_and_only_caps_forward():
    assert limit_forward_velocity(0.20, 0.08) == pytest.approx(0.08)
    assert limit_forward_velocity(-0.10, 0.0) == pytest.approx(-0.10)
    assert limit_forward_velocity(0.20, None) == pytest.approx(0.20)


def test_invalid_configuration_is_rejected():
    with pytest.raises(ValueError):
        FusionConfig(detect_distance_m=0.30, clear_distance_m=0.25)
