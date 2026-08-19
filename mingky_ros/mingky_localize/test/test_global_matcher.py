"""LiDAR 전역 후보 검색의 정확도·모호성·이동 추적 검증."""

import math

from mingky_localize.global_matcher import (
    GlobalScanMatcher,
    LaserObservation,
    MatcherConfig,
    OccupancyMap,
)
import numpy as np


def _map(width, height, occupied, *, resolution=0.1):
    data = [0] * (width * height)
    for x, y in occupied:
        data[y * width + x] = 100
    return OccupancyMap(
        width=width, height=height, resolution=resolution,
        origin_x=0.0, origin_y=0.0, origin_yaw=0.0, data=data)


def _scan(grid, pose, *, max_range=3.0, rays=180):
    ranges = []
    angle_min = -math.pi
    increment = 2.0 * math.pi / rays
    step = grid.resolution / 4.0
    for index in range(rays):
        angle = pose[2] + angle_min + index * increment
        hit = float('inf')
        distance = step
        while distance <= max_range:
            x = pose[0] + math.cos(angle) * distance
            y = pose[1] + math.sin(angle) * distance
            gx, gy, valid = grid.world_to_grid(
                np.asarray([x]), np.asarray([y]))
            if not valid[0] or not grid.known_free[gy[0], gx[0]]:
                hit = distance
                break
            distance += step
        ranges.append(hit)
    return LaserObservation.from_ranges(
        ranges, angle_min=angle_min, angle_increment=increment,
        range_min=0.05, range_max=max_range, max_beams=60)


def _border(width, height):
    return {
        *((x, 0) for x in range(width)),
        *((x, height - 1) for x in range(width)),
        *((0, y) for y in range(height)),
        *((width - 1, y) for y in range(height)),
    }


def test_unique_geometry_recovers_pose_without_using_amcl():
    width, height = 48, 36
    occupied = _border(width, height)
    occupied.update((24, y) for y in range(8, 29))
    occupied.update((x, 18) for x in range(24, 42))
    occupied.update((10, y) for y in range(4, 12))
    occupied.update((x, 30) for x in range(30, 45))
    grid = _map(width, height, occupied)
    config = MatcherConfig(min_score=0.45, min_margin=0.04)
    matcher = GlobalScanMatcher(grid, config)
    expected = (1.35, 2.45, math.radians(21.0))

    result = matcher.global_match(_scan(grid, expected))

    assert result.hypotheses
    best = result.hypotheses[0]
    assert math.hypot(best.x - expected[0], best.y - expected[1]) < 0.12
    assert abs(math.atan2(
        math.sin(best.yaw - expected[2]),
        math.cos(best.yaw - expected[2]))) < math.radians(7.0)
    assert result.confident


def test_repeating_corridor_stays_ambiguous():
    width, height = 100, 30
    occupied = {(x, 5) for x in range(width)}
    occupied.update((x, 24) for x in range(width))
    occupied.update((0, y) for y in range(height))
    occupied.update((width - 1, y) for y in range(height))
    grid = _map(width, height, occupied)
    matcher = GlobalScanMatcher(
        grid, MatcherConfig(min_score=0.40, min_margin=0.08))

    result = matcher.global_match(
        _scan(grid, (4.05, 1.45, 0.0), max_range=1.2))

    assert len(result.hypotheses) >= 2
    assert result.best_score >= 0.40
    assert result.margin < 0.08
    assert not result.confident


def test_sequence_update_propagates_candidates_with_odometry():
    width, height = 48, 36
    occupied = _border(width, height)
    occupied.update((24, y) for y in range(8, 29))
    occupied.update((x, 18) for x in range(24, 42))
    occupied.update((10, y) for y in range(4, 12))
    occupied.update((x, 30) for x in range(30, 45))
    grid = _map(width, height, occupied)
    matcher = GlobalScanMatcher(
        grid, MatcherConfig(min_score=0.40, min_margin=0.03))
    first_pose = (1.35, 2.45, 0.0)
    first = matcher.global_match(_scan(grid, first_pose))

    second_pose = (1.45, 2.45, 0.0)
    second = matcher.update(
        _scan(grid, second_pose), first.hypotheses,
        delta_x=0.10, delta_y=0.0, delta_yaw=0.0)

    assert second.hypotheses
    best = second.hypotheses[0]
    assert math.hypot(best.x - second_pose[0], best.y - second_pose[1]) < 0.12
    assert best.observations == 2


def test_too_few_laser_hits_returns_no_candidate():
    grid = _map(20, 20, _border(20, 20))
    matcher = GlobalScanMatcher(grid, MatcherConfig())
    observation = LaserObservation(
        x=np.asarray([0.1, 0.2]), y=np.asarray([0.0, 0.0]))

    result = matcher.global_match(observation)

    assert result.hypotheses == ()
    assert not result.confident
