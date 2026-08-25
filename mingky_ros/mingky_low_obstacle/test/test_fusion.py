import math

from mingky_low_obstacle.fusion import (
    CostmapObservationRetention,
    FusionConfig,
    guide_navigation_segment_active,
    limit_forward_velocity,
    LowObstacleFilter,
    matching_observation_cluster,
    NavigationScope,
    nearest_lidar_in_ultrasonic_cone,
    nearest_lidar_in_ultrasonic_cones,
    ObstacleCone,
    observation_overlap_estimate,
    observation_pose_distinct,
    observation_pose_expired,
    retained_cone_speed_limit,
    retained_obstacle_speed_limit,
)
import pytest


def _update(filter_, sonar, lidar=0.80, wall_context=None):
    return filter_.update(
        sonar,
        min_range_m=0.02,
        max_range_m=0.97,
        lidar_range_m=lidar,
        lidar_fresh=True,
        wall_context_range_m=wall_context,
    )


def test_median_and_two_of_three_reject_one_spike():
    filter_ = LowObstacleFilter()

    decisions = [_update(filter_, value) for value in (0.18, 0.63, 0.19)]

    assert decisions[-1].filtered_range_m == pytest.approx(0.19)
    assert decisions[-1].state == 'SLOW'
    assert decisions[-1].output_range_m == pytest.approx(0.20)


def test_lidar_agreement_does_not_create_low_obstacle():
    filter_ = LowObstacleFilter()

    decisions = [_update(filter_, 0.18, lidar=0.20) for _ in range(5)]

    assert decisions[-1].state == 'CLEAR'
    assert decisions[-1].output_range_m == pytest.approx(0.97)


def test_hysteresis_holds_costmap_for_two_raw_clear_observations():
    filter_ = LowObstacleFilter()
    for _ in range(5):
        decision = _update(filter_, 0.20)
    assert decision.low_obstacle_confirmed

    # Two consecutive raw absences give the costmap a 0.2 second hold at the
    # deployed 10 Hz sensor rate. The causal median can still report the old
    # distance on the first sample, but clear timing follows raw absences.
    first = _update(filter_, 0.46)
    assert first.low_obstacle_confirmed
    assert first.output_range_m == pytest.approx(0.20)
    decision = _update(filter_, 0.46)
    assert not decision.low_obstacle_confirmed
    assert decision.state == 'CLEAR'
    assert decision.output_range_m == pytest.approx(0.97)


def test_single_clear_dropout_keeps_costmap_observation():
    filter_ = LowObstacleFilter()
    for _ in range(5):
        _update(filter_, 0.20)

    dropout = _update(filter_, 0.80)
    recovered = _update(filter_, 0.20)

    assert dropout.low_obstacle_confirmed
    assert recovered.low_obstacle_confirmed
    assert recovered.output_range_m == pytest.approx(0.20)


def test_near_samples_do_not_block_before_low_obstacle_confirmation():
    filter_ = LowObstacleFilter()

    assert _update(filter_, 0.06).state == 'UNCERTAIN'
    decision = _update(filter_, 0.06)

    assert decision.state == 'UNCERTAIN'
    assert decision.forward_speed_limit_mps is None

    # A wall edge or a transient echo must not directly stop guidance.  The
    # LiDAR mismatch first has to satisfy the normal 2-of-3 confirmation rule.
    decision = _update(filter_, 0.06)
    assert decision.state == 'SLOW'
    assert decision.forward_speed_limit_mps == pytest.approx(
        0.08 * (0.06 - 0.04) / (0.25 - 0.04))
    # The endpoint is clamped outside the footprint so Nav2 sees a routable
    # obstacle instead of clearing it, while the real range controls speed.
    assert decision.output_range_m == pytest.approx(0.20)


def test_four_centimetres_is_the_hard_forward_stop():
    filter_ = LowObstacleFilter()

    for _ in range(3):
        decision = _update(filter_, 0.04)

    assert decision.state == 'FORWARD_BLOCKED'
    assert decision.forward_speed_limit_mps == 0.0
    assert decision.output_range_m == pytest.approx(0.20)


def test_slow_limit_decreases_continuously_toward_stop_boundary():
    filter_ = LowObstacleFilter()

    for _ in range(3):
        farther = _update(filter_, 0.12)
    for _ in range(3):
        nearer = _update(filter_, 0.07)
    for _ in range(3):
        nearest = _update(filter_, 0.05)

    assert farther.forward_speed_limit_mps > nearer.forward_speed_limit_mps
    assert nearer.forward_speed_limit_mps > nearest.forward_speed_limit_mps
    assert nearest.forward_speed_limit_mps > 0.0


def test_confirmed_obstacle_is_projected_outside_inflated_footprint():
    filter_ = LowObstacleFilter()

    for _ in range(5):
        decision = _update(filter_, 0.12)

    assert decision.state == 'SLOW'
    assert decision.output_range_m == pytest.approx(0.20)


def test_obstacle_farther_than_projection_distance_keeps_measured_range():
    filter_ = LowObstacleFilter()

    for _ in range(3):
        decision = _update(filter_, 0.24)

    assert decision.state == 'SLOW'
    assert decision.output_range_m == pytest.approx(0.24)


def test_near_wall_seen_by_lidar_never_blocks_forward():
    filter_ = LowObstacleFilter()

    decisions = [_update(filter_, 0.06, lidar=0.08) for _ in range(8)]

    assert all(decision.state == 'CLEAR' for decision in decisions)
    assert all(
        decision.forward_speed_limit_mps is None for decision in decisions)


def test_off_axis_corner_wall_suppresses_ultrasonic_reflection():
    filter_ = LowObstacleFilter()

    decisions = [
        _update(filter_, 0.049, lidar=2.14, wall_context=0.181)
        for _ in range(8)
    ]

    assert all(decision.state == 'CLEAR' for decision in decisions)
    assert all(decision.wall_reflection_likely for decision in decisions)
    assert all(not decision.low_obstacle_confirmed for decision in decisions)


def test_low_obstacle_is_kept_when_no_close_side_wall_explains_echo():
    filter_ = LowObstacleFilter()

    decisions = [
        _update(filter_, 0.12, lidar=0.80, wall_context=0.80)
        for _ in range(5)
    ]

    assert decisions[-1].state == 'SLOW'
    assert not decisions[-1].wall_reflection_likely


def test_real_low_obstacle_beyond_short_echo_guard_is_kept_near_wall():
    filter_ = LowObstacleFilter()

    decisions = [
        _update(filter_, 0.28, lidar=0.80, wall_context=0.19)
        for _ in range(5)
    ]

    assert decisions[-1].state == 'CONFIRMED'
    assert decisions[-1].low_obstacle_confirmed
    assert not decisions[-1].wall_reflection_likely


def test_confirmed_obstacle_is_not_released_by_later_wall_context():
    filter_ = LowObstacleFilter()
    for _ in range(5):
        decision = _update(filter_, 0.12, lidar=0.80, wall_context=0.80)
    assert decision.low_obstacle_confirmed

    decisions = [
        _update(filter_, 0.05, lidar=0.80, wall_context=0.18)
        for _ in range(5)
    ]

    assert all(item.low_obstacle_confirmed for item in decisions)
    assert decisions[-1].state == 'SLOW'
    assert not decisions[-1].wall_reflection_likely


def test_spatial_observation_cluster_uses_nearest_match_only():
    endpoints = [(0.10, 0.20), (0.80, 0.20)]

    assert matching_observation_cluster(
        endpoints, (0.17, 0.23), merge_distance_m=0.12) == 0
    assert matching_observation_cluster(
        endpoints, (0.72, 0.20), merge_distance_m=0.12) == 1
    assert matching_observation_cluster(
        endpoints, (0.45, 0.20), merge_distance_m=0.12) is None


def _retained_limit(pose, endpoints):
    return retained_obstacle_speed_limit(
        pose,
        endpoints,
        sensor_offset_m=0.0267,
        corridor_half_width_m=0.08,
        slow_distance_m=0.25,
        stop_distance_m=0.04,
        slow_speed_mps=0.08,
    )


def test_retained_obstacle_keeps_slow_gate_during_sensor_dropout():
    limit = _retained_limit((0.0, 0.0, 0.0), [(0.20, 0.01)])

    assert limit is not None
    assert 0.0 < limit < 0.08


def test_retained_obstacle_stops_inside_safety_distance():
    assert _retained_limit((0.0, 0.0, 0.0), [(0.06, 0.0)]) == 0.0


def test_retained_obstacle_releases_after_robot_turns_to_detour():
    endpoint = (0.18, 0.0)

    assert _retained_limit((0.0, 0.0, 0.0), [endpoint]) is not None
    assert _retained_limit((0.0, 0.0, math.pi / 2.0), [endpoint]) is None


def test_retained_obstacle_outside_swept_width_does_not_block_detour():
    assert _retained_limit((0.0, 0.0, 0.0), [(0.18, 0.12)]) is None


def test_same_sensor_pose_replaces_evidence_instead_of_accumulating():
    first = ObstacleCone(0.0, 0.0, 0.0, 0.20, 0.26)
    noisy_repeat = ObstacleCone(0.01, 0.0, math.radians(2), 0.22, 0.26)
    new_view = ObstacleCone(0.05, 0.0, math.radians(6), 0.21, 0.26)

    assert not observation_pose_distinct(first, noisy_repeat)
    assert observation_pose_distinct(first, new_view)


def test_two_cones_narrow_to_an_overlap_candidate():
    # Both observations describe the same object near (0.20, 0.0).
    cones = [
        ObstacleCone(0.0, 0.0, 0.0, 0.20, math.radians(20)),
        ObstacleCone(0.0, 0.05, math.atan2(-0.05, 0.20),
                     math.hypot(0.20, 0.05), math.radians(20)),
    ]

    estimate = observation_overlap_estimate(cones)

    assert estimate is not None
    assert estimate[0] == pytest.approx(0.20, abs=0.05)
    assert estimate[1] == pytest.approx(0.0, abs=0.05)
    assert 0.0 < estimate[2] <= 0.10


def test_inconsistent_moving_observations_are_not_claimed_as_one_point():
    cones = [
        ObstacleCone(0.0, 0.0, 0.0, 0.12, math.radians(15)),
        ObstacleCone(0.0, 0.08, 0.0, 0.40, math.radians(15)),
    ]

    assert observation_overlap_estimate(cones) is None


def test_retained_cone_blocks_corridor_but_releases_after_detour_turn():
    clusters = [[ObstacleCone(0.0267, 0.0, 0.0, 0.12, 0.26)]]
    common = {
        'sensor_offset_m': 0.0267,
        'corridor_half_width_m': 0.08,
        'slow_distance_m': 0.25,
        'stop_distance_m': 0.04,
        'slow_speed_mps': 0.08,
    }

    forward = retained_cone_speed_limit(
        (0.0, 0.0, 0.0), clusters, **common)
    turned = retained_cone_speed_limit(
        (0.0, 0.0, math.pi / 2), clusters, **common)

    assert forward is not None
    assert turned is None


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
        _update(filter_, 0.04)

    decision = filter_.stale_decision(0.80)

    assert decision.low_obstacle_confirmed
    assert decision.output_range_m is None
    assert decision.forward_speed_limit_mps == 0.0


def test_scan_points_are_transformed_before_cone_comparison():
    # Rotating the scan frame by pi puts its zero-degree point behind the
    # ultrasonic sensor, while the pi ray becomes its front.
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


def test_wider_lidar_context_reports_nearby_side_wall():
    ranges = [0.18, 2.14, 0.18]
    common = {
        'ranges': ranges,
        'angle_min': -math.pi / 4.0,
        'angle_increment': math.pi / 4.0,
        'range_min': 0.02,
        'range_max': 3.0,
        'scan_to_ultrasonic_x': 0.0,
        'scan_to_ultrasonic_y': 0.0,
        'scan_to_ultrasonic_yaw': 0.0,
    }

    narrow = nearest_lidar_in_ultrasonic_cone(
        **common, ultrasonic_fov=math.radians(15.0))
    guarded = nearest_lidar_in_ultrasonic_cone(
        **common, ultrasonic_fov=math.radians(90.0))

    assert narrow == pytest.approx(2.14)
    assert guarded == pytest.approx(0.18)


def test_narrow_and_wall_cones_share_one_scan_transformation():
    ranges = [0.18, 2.14, 0.18]
    narrow, wall = nearest_lidar_in_ultrasonic_cones(
        ranges,
        angle_min=-math.pi / 4.0,
        angle_increment=math.pi / 4.0,
        range_min=0.02,
        range_max=3.0,
        scan_to_ultrasonic_x=0.0,
        scan_to_ultrasonic_y=0.0,
        scan_to_ultrasonic_yaw=0.0,
        ultrasonic_fovs=(math.radians(15.0), math.radians(90.0)),
    )

    assert narrow == pytest.approx(2.14)
    assert wall == pytest.approx(0.18)


def test_forward_limiter_preserves_reverse_and_only_caps_forward():
    assert limit_forward_velocity(0.20, 0.08) == pytest.approx(0.08)
    assert limit_forward_velocity(-0.10, 0.0) == pytest.approx(-0.10)
    assert limit_forward_velocity(0.20, None) == pytest.approx(0.20)


def test_robot_motion_expires_robot_relative_obstacle_observation():
    anchor = (1.0, 2.0, 0.0)

    assert not observation_pose_expired(
        anchor, (1.09, 2.0, math.radians(19.0)),
        distance_m=0.10, yaw_rad=math.radians(20.0))
    assert observation_pose_expired(
        anchor, (1.10, 2.0, 0.0),
        distance_m=0.10, yaw_rad=math.radians(20.0))
    assert not observation_pose_expired(
        anchor, (1.20, 2.0, 0.0),
        distance_m=0.10, yaw_rad=math.radians(20.0),
        preserve_forward_approach=True)
    assert observation_pose_expired(
        anchor, (1.0, 2.10, 0.0),
        distance_m=0.10, yaw_rad=math.radians(20.0),
        preserve_forward_approach=True)
    assert observation_pose_expired(
        anchor, (1.0, 2.0, math.radians(20.0)),
        distance_m=0.10, yaw_rad=math.radians(20.0))
    # 각도 래핑도 최단 회전량으로 계산한다.
    assert observation_pose_expired(
        anchor, (1.0, 2.0, math.radians(350.0)),
        distance_m=0.10, yaw_rad=math.radians(10.0))


def test_costmap_clear_is_delayed_and_cancelled_by_redetection():
    retention = CostmapObservationRetention(hold_sec=1.5)

    retention.request_clear(1_000_000_000, 'sensor clear')
    assert not retention.clear_due(2_499_999_999)
    assert retention.clear_due(2_500_000_000)

    retention.on_detection(True)
    assert not retention.clear_due(3_000_000_000)


def test_avoidance_clear_suppresses_remarking_until_sensor_clears():
    retention = CostmapObservationRetention(hold_sec=1.5)

    retention.request_clear(
        1_000_000_000, 'avoidance', suppress_until_sensor_clear=True)
    retention.on_detection(True)

    assert retention.suppress_until_sensor_clear
    assert retention.clear_due(2_500_000_000)

    retention.mark_cleared()
    retention.on_detection(False)
    assert not retention.suppress_until_sensor_clear


def test_guidance_segment_stays_active_during_patient_pause():
    assert guide_navigation_segment_active('guiding', 'paused', False)
    assert guide_navigation_segment_active('guiding', 'moving', False)
    assert guide_navigation_segment_active('arrived', 'moving', False)
    assert guide_navigation_segment_active('none', 'returning_to_dock', True)
    assert not guide_navigation_segment_active('arrived', 'waiting', False)
    assert not guide_navigation_segment_active('confirmed', 'idle', False)


def test_navigation_scope_finishes_only_after_all_navigation_sources_end():
    scope = NavigationScope()

    assert scope.update('guidance', True) == 'started'
    assert scope.update('waypoint_test', True) is None
    assert scope.update('guidance', False) is None
    assert scope.active
    assert scope.update('waypoint_test', False) == 'finished'
    assert not scope.active


def test_invalid_configuration_is_rejected():
    with pytest.raises(ValueError):
        FusionConfig(detect_distance_m=0.30, clear_distance_m=0.25)
