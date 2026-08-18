"""저상 장애물 판별과 직접 옆걸음 전략의 ROS 비의존 핵심 로직."""

import math
from dataclasses import dataclass
from typing import Generator, Literal, Sequence

MotionKind = Literal['spin', 'drive']


@dataclass(frozen=True)
class LowObstacleConfig:
    trigger_distance_m: float = 0.25
    lidar_margin_m: float = 0.15
    lidar_front_center_deg: float = 180.0
    lidar_half_width_deg: float = 15.0
    probe_step_deg: float = 10.0
    probe_max_steps: int = 4
    probe_clearance_m: float = 0.45
    body_clearance_margin_deg: float = 15.0
    drive_step_m: float = 0.08
    drive_total_m: float = 0.35
    drive_speed_mps: float = 0.12
    minimum_drive_clearance_m: float = 0.10


@dataclass(frozen=True)
class MotionCommand:
    kind: MotionKind
    value: float
    speed: float = 0.0
    sample_range: bool = True


@dataclass(frozen=True)
class MotionResult:
    succeeded: bool
    range_m: float | None = None


@dataclass(frozen=True)
class SidestepOutcome:
    succeeded: bool
    side: int | None
    reason: str


def _angular_distance(left: float, right: float) -> float:
    return abs(math.atan2(math.sin(left - right), math.cos(left - right)))


def lidar_sector_min_range(
        ranges: Sequence[float], *, angle_min: float, angle_increment: float,
        range_min: float, range_max: float, center_deg: float,
        half_width_deg: float) -> float | None:
    """LiDAR 좌표계의 지정 방향 부채꼴에서 유효한 최솟값을 반환한다."""
    if not ranges or not math.isfinite(angle_increment) or angle_increment == 0.0:
        return None
    center = math.radians(center_deg)
    half_width = math.radians(max(0.0, half_width_deg))
    valid = []
    saw_no_return = False
    for index, distance in enumerate(ranges):
        angle = angle_min + index * angle_increment
        if _angular_distance(angle, center) > half_width:
            continue
        # LaserScan은 측정 거리 안에 반사체가 없을 때 +inf를 사용한다. 최신
        # 스캔의 정면 구간이 전부 +inf인 것은 센서 단절이 아니라 빈 공간이다.
        # NaN과 -inf는 센서 이상 가능성이 있어 같은 방식으로 통과시키지 않는다.
        if math.isinf(distance) and distance > 0.0:
            saw_no_return = True
            continue
        if math.isfinite(distance) and range_min <= distance <= range_max:
            valid.append(float(distance))
    if valid:
        return min(valid)
    return float(range_max) if saw_no_return else None


def is_low_obstacle(
        ultrasonic_range_m: float | None, lidar_range_m: float | None,
        *, trigger_distance_m: float, lidar_margin_m: float) -> bool:
    """초음파만 가까운 물체를 볼 때 저상 장애물로 판정한다.

    LiDAR 관측이 없으면 센서 단절과 저상 장애물을 구분할 수 없으므로 직접
    회피를 시작하지 않는다.
    """
    if ultrasonic_range_m is None or lidar_range_m is None:
        return False
    if not math.isfinite(ultrasonic_range_m) or ultrasonic_range_m < 0.0:
        return False
    return (
        ultrasonic_range_m < trigger_distance_m
        and lidar_range_m >= trigger_distance_m + lidar_margin_m
    )


class SidestepStrategy:
    """프로토타입의 좌우 탐색·단계 전진을 명령 시퀀스로 만든 전략.

    호출자는 생성기가 내놓는 명령을 Nav2 액션으로 실행하고, 액션 완료 뒤의
    최신 초음파 관측과 성공 여부를 :class:`MotionResult`로 돌려준다.
    """

    def __init__(self, config: LowObstacleConfig):
        self.config = config

    def commands(
            self, preferred_side: int | None = None,
            ) -> Generator[MotionCommand, MotionResult, SidestepOutcome]:
        config = self.config
        step_rad = math.radians(config.probe_step_deg)

        if preferred_side in (-1, 1):
            side = preferred_side
        else:
            left = yield MotionCommand('spin', step_rad)
            if not left.succeeded:
                return SidestepOutcome(False, None, 'left look spin failed')
            right = yield MotionCommand('spin', -2.0 * step_rad)
            if not right.succeeded:
                return SidestepOutcome(False, None, 'right look spin failed')
            centered = yield MotionCommand('spin', step_rad, sample_range=False)
            if not centered.succeeded:
                return SidestepOutcome(False, None, 'center spin failed')
            left_range = left.range_m if left.range_m is not None else -1.0
            right_range = right.range_m if right.range_m is not None else -1.0
            side = 1 if left_range >= right_range else -1

        cumulative, clear = yield from self._probe(side)
        if not clear:
            centered = yield MotionCommand(
                'spin', -side * cumulative, sample_range=False)
            if not centered.succeeded:
                return SidestepOutcome(False, None, 'first probe reset failed')
            side = -side
            cumulative, clear = yield from self._probe(side)
            if not clear:
                centered = yield MotionCommand(
                    'spin', -side * cumulative, sample_range=False)
                if not centered.succeeded:
                    return SidestepOutcome(False, None, 'second probe reset failed')
                return SidestepOutcome(False, None, 'neither side is clear')

        margin = math.radians(config.body_clearance_margin_deg)
        margin_result = yield MotionCommand(
            'spin', side * margin, sample_range=False)
        if not margin_result.succeeded:
            return SidestepOutcome(False, side, 'body clearance spin failed')
        cumulative += margin

        traveled = 0.0
        drive_failure = None
        while traveled + 1e-9 < config.drive_total_m:
            distance = min(config.drive_step_m, config.drive_total_m - traveled)
            result = yield MotionCommand(
                'drive', distance, speed=config.drive_speed_mps)
            if not result.succeeded:
                drive_failure = 'sidestep drive failed'
                break
            traveled += distance
            if result.range_m is None:
                drive_failure = 'ultrasonic reading missing during sidestep'
                break
            if result.range_m < config.minimum_drive_clearance_m:
                drive_failure = 'obstacle became too close during sidestep'
                break

        restored = yield MotionCommand(
            'spin', -side * cumulative, sample_range=False)
        if not restored.succeeded:
            return SidestepOutcome(False, side, 'heading restore failed')
        if drive_failure is not None:
            return SidestepOutcome(False, side, drive_failure)
        return SidestepOutcome(True, side, 'sidestep completed')

    def _probe(
            self, side: int,
            ) -> Generator[MotionCommand, MotionResult, tuple[float, bool]]:
        step_rad = math.radians(self.config.probe_step_deg)
        cumulative = 0.0
        for _ in range(self.config.probe_max_steps):
            result = yield MotionCommand('spin', side * step_rad)
            if not result.succeeded:
                return cumulative, False
            cumulative += step_rad
            if (
                    result.range_m is not None
                    and result.range_m >= self.config.probe_clearance_m):
                return cumulative, True
        return cumulative, False
