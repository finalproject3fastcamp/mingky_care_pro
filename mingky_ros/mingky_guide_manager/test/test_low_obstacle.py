"""저상 장애물 판별과 옆걸음 전략의 단위 테스트."""

import math

import pytest
from mingky_guide_manager.low_obstacle import (
    LowObstacleConfig,
    MotionResult,
    SidestepStrategy,
    is_low_obstacle,
    lidar_sector_min_range,
)


def test_low_obstacle_requires_ultrasonic_only_detection() -> None:
    assert is_low_obstacle(
        0.20, 0.60, trigger_distance_m=0.25, lidar_margin_m=0.15)
    assert not is_low_obstacle(
        0.20, 0.30, trigger_distance_m=0.25, lidar_margin_m=0.15)
    assert not is_low_obstacle(
        0.20, None, trigger_distance_m=0.25, lidar_margin_m=0.15)
    assert not is_low_obstacle(
        0.40, 0.60, trigger_distance_m=0.25, lidar_margin_m=0.15)


def test_lidar_sector_uses_configured_sensor_front() -> None:
    ranges = [2.0] * 9
    ranges[0] = 0.30  # -pi, 센서 좌표계에서 로봇 정면
    ranges[4] = 0.10  # 0 rad, 이 로봇에서는 후방

    result = lidar_sector_min_range(
        ranges,
        angle_min=-math.pi,
        angle_increment=math.pi / 4,
        range_min=0.05,
        range_max=5.0,
        center_deg=180.0,
        half_width_deg=15.0,
    )

    assert result == pytest.approx(0.30)


def test_lidar_sector_treats_positive_infinity_as_clear_space() -> None:
    result = lidar_sector_min_range(
        [math.inf] * 9,
        angle_min=-math.pi,
        angle_increment=math.pi / 4,
        range_min=0.05,
        range_max=5.0,
        center_deg=180.0,
        half_width_deg=15.0,
    )

    assert result == pytest.approx(5.0)
    assert is_low_obstacle(
        0.20,
        result,
        trigger_distance_m=0.25,
        lidar_margin_m=0.15,
    )


def test_lidar_sector_does_not_treat_invalid_values_as_clear_space() -> None:
    result = lidar_sector_min_range(
        [math.nan, -math.inf] * 4 + [math.nan],
        angle_min=-math.pi,
        angle_increment=math.pi / 4,
        range_min=0.05,
        range_max=5.0,
        center_deg=180.0,
        half_width_deg=15.0,
    )

    assert result is None


def _complete_left_sidestep(strategy: SidestepStrategy):
    sequence = strategy.commands()
    command = next(sequence)
    assert command.kind == 'spin'
    command = sequence.send(MotionResult(True, 0.60))  # 왼쪽
    assert command.value < 0.0
    command = sequence.send(MotionResult(True, 0.20))  # 오른쪽
    command = sequence.send(MotionResult(True))        # 정면 복귀
    assert command.value > 0.0                         # 왼쪽 probe
    command = sequence.send(MotionResult(True, 0.50))
    assert command.value > 0.0                         # 몸통 여유각
    command = sequence.send(MotionResult(True))
    while command.kind == 'drive':
        command = sequence.send(MotionResult(True, 0.50))
    assert command.kind == 'spin' and command.value < 0.0
    with pytest.raises(StopIteration) as finished:
        sequence.send(MotionResult(True))
    return finished.value.value


def test_sidestep_chooses_open_side_and_restores_heading() -> None:
    outcome = _complete_left_sidestep(
        SidestepStrategy(LowObstacleConfig(drive_total_m=0.17)))

    assert outcome.succeeded is True
    assert outcome.side == 1
    assert outcome.reason == 'sidestep completed'


def test_sidestep_tries_other_side_when_first_probe_is_blocked() -> None:
    strategy = SidestepStrategy(LowObstacleConfig(
        probe_max_steps=2,
        drive_total_m=0.08,
    ))
    sequence = strategy.commands(preferred_side=1)

    command = next(sequence)
    assert command.value > 0.0
    command = sequence.send(MotionResult(True, 0.20))
    command = sequence.send(MotionResult(True, 0.20))
    assert command.value < 0.0  # 첫 방향에서 중앙 복귀
    command = sequence.send(MotionResult(True))
    assert command.value < 0.0  # 반대 방향 probe
    command = sequence.send(MotionResult(True, 0.50))
    assert command.value < 0.0  # 몸통 여유각


def test_sidestep_stops_after_dangerous_drive_reading() -> None:
    strategy = SidestepStrategy(LowObstacleConfig(drive_total_m=0.16))
    sequence = strategy.commands(preferred_side=1)

    command = next(sequence)
    command = sequence.send(MotionResult(True, 0.50))  # probe
    command = sequence.send(MotionResult(True))        # 여유각
    assert command.kind == 'drive'
    command = sequence.send(MotionResult(True, 0.05))
    assert command.kind == 'spin'                      # 즉시 방향 복원
    with pytest.raises(StopIteration) as finished:
        sequence.send(MotionResult(True))

    assert finished.value.value.succeeded is False
    assert 'too close' in finished.value.value.reason
