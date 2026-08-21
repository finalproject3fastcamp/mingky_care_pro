"""ROS-independent low-obstacle filtering and LiDAR cone comparison."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import statistics
from typing import Sequence


@dataclass(frozen=True)
class FusionConfig:
    """Thresholds for conservative low-obstacle confirmation."""

    detect_distance_m: float = 0.30
    clear_distance_m: float = 0.35
    slow_distance_m: float = 0.15
    stop_distance_m: float = 0.07
    slow_speed_mps: float = 0.08
    lidar_margin_m: float = 0.15
    median_samples: int = 3
    confirmation_window: int = 5
    confirmations_required: int = 3
    clear_confirmations: int = 3
    near_window: int = 3
    near_confirmations: int = 2

    def __post_init__(self) -> None:
        if not 0.0 < self.stop_distance_m < self.slow_distance_m:
            raise ValueError('stop_distance_m은 slow_distance_m보다 작아야 합니다.')
        if not self.slow_distance_m < self.detect_distance_m:
            raise ValueError('slow_distance_m은 detect_distance_m보다 작아야 합니다.')
        if self.clear_distance_m <= self.detect_distance_m:
            raise ValueError('clear_distance_m은 detect_distance_m보다 커야 합니다.')
        for value, name in (
                (self.median_samples, 'median_samples'),
                (self.confirmation_window, 'confirmation_window'),
                (self.confirmations_required, 'confirmations_required'),
                (self.clear_confirmations, 'clear_confirmations'),
                (self.near_window, 'near_window'),
                (self.near_confirmations, 'near_confirmations')):
            if value <= 0:
                raise ValueError(f'{name}은 1 이상이어야 합니다.')
        if self.confirmations_required > self.confirmation_window:
            raise ValueError('confirmations_required가 window보다 클 수 없습니다.')
        if self.near_confirmations > self.near_window:
            raise ValueError('near_confirmations가 near_window보다 클 수 없습니다.')


@dataclass(frozen=True)
class FusionDecision:
    """One sensor update converted into perception and command policy."""

    state: str
    filtered_range_m: float | None
    lidar_range_m: float | None
    output_range_m: float | None
    forward_speed_limit_mps: float | None
    low_obstacle_confirmed: bool


def nearest_lidar_in_ultrasonic_cone(
        ranges: Sequence[float], *, angle_min: float, angle_increment: float,
        range_min: float, range_max: float, scan_to_ultrasonic_x: float,
        scan_to_ultrasonic_y: float, scan_to_ultrasonic_yaw: float,
        ultrasonic_fov: float) -> float | None:
    """
    Return the nearest scan endpoint inside the ultrasonic sensor cone.

    Scan points are transformed into the ultrasonic frame before testing the
    cone. Positive infinity is represented by the scan's maximum range so an
    otherwise empty cone is distinguishable from missing or invalid scan data.
    """
    if (
            not ranges or not math.isfinite(angle_increment)
            or angle_increment == 0.0 or range_max <= range_min
            or not 0.0 < ultrasonic_fov < math.pi):
        return None

    cos_yaw = math.cos(scan_to_ultrasonic_yaw)
    sin_yaw = math.sin(scan_to_ultrasonic_yaw)
    half_fov = ultrasonic_fov / 2.0
    candidates: list[float] = []

    for index, measured in enumerate(ranges):
        if math.isinf(measured) and measured > 0.0:
            distance = range_max
        elif math.isfinite(measured) and range_min <= measured <= range_max:
            distance = float(measured)
        else:
            continue

        angle = angle_min + index * angle_increment
        scan_x = distance * math.cos(angle)
        scan_y = distance * math.sin(angle)
        cone_x = (
            cos_yaw * scan_x - sin_yaw * scan_y + scan_to_ultrasonic_x)
        cone_y = (
            sin_yaw * scan_x + cos_yaw * scan_y + scan_to_ultrasonic_y)
        if cone_x <= 0.0:
            continue
        if abs(math.atan2(cone_y, cone_x)) > half_fov:
            continue
        candidates.append(math.hypot(cone_x, cone_y))

    return min(candidates) if candidates else None


class LowObstacleFilter:
    """Filter noisy sonar without inventing an exact obstacle bearing."""

    def __init__(self, config: FusionConfig | None = None):
        self.config = config or FusionConfig()
        self._ranges: deque[float] = deque(maxlen=self.config.median_samples)
        self._evidence: deque[bool] = deque(
            maxlen=self.config.confirmation_window)
        self._near_evidence: deque[bool] = deque(
            maxlen=self.config.near_window)
        self._clear_streak = 0
        self._confirmed = False

    def reset(self) -> None:
        self._ranges.clear()
        self._evidence.clear()
        self._near_evidence.clear()
        self._clear_streak = 0
        self._confirmed = False

    def update(
            self, ultrasonic_range_m: float, *, min_range_m: float,
            max_range_m: float, lidar_range_m: float | None,
            lidar_fresh: bool) -> FusionDecision:
        valid_range = (
            math.isfinite(ultrasonic_range_m)
            and min_range_m <= ultrasonic_range_m <= max_range_m)
        if not valid_range:
            return self.stale_decision(lidar_range_m)

        self._ranges.append(float(ultrasonic_range_m))
        filtered = float(statistics.median(self._ranges))
        comparable = (
            lidar_fresh and lidar_range_m is not None
            and math.isfinite(lidar_range_m))
        mismatch = bool(
            comparable
            and lidar_range_m >= filtered + self.config.lidar_margin_m)
        candidate = mismatch and filtered <= self.config.detect_distance_m
        self._evidence.append(candidate)
        self._near_evidence.append(
            mismatch and ultrasonic_range_m <= self.config.stop_distance_m)

        if self._confirmed:
            # The wider clear threshold prevents chatter at the detection edge.
            still_present = mismatch and filtered < self.config.clear_distance_m
            self._clear_streak = 0 if still_present else self._clear_streak + 1
            if self._clear_streak >= self.config.clear_confirmations:
                self._confirmed = False
                self._clear_streak = 0
                self._evidence.clear()
        elif (
                len(self._evidence) >= self.config.confirmation_window
                and sum(self._evidence) >= self.config.confirmations_required):
            self._confirmed = True
            self._clear_streak = 0

        near = (
            self._confirmed
            and comparable
            and len(self._near_evidence) >= self.config.near_confirmations
            and sum(self._near_evidence) >= self.config.near_confirmations)

        if not comparable:
            state = 'STALE_LIDAR'
            output = filtered if self._confirmed else None
            limit = self._confirmed_limit(filtered)
        elif near:
            state = 'FORWARD_BLOCKED'
            output = filtered
            limit = 0.0
        elif self._confirmed and filtered <= self.config.slow_distance_m:
            state = 'SLOW'
            output = filtered
            limit = self.config.slow_speed_mps
        elif self._confirmed:
            state = 'CONFIRMED'
            output = filtered
            limit = None
        elif candidate:
            state = 'UNCERTAIN'
            output = None
            limit = None
        else:
            state = 'CLEAR'
            # An exact max-range reading explicitly clears the RangeSensorLayer.
            output = max_range_m
            limit = None

        return FusionDecision(
            state=state,
            filtered_range_m=filtered,
            lidar_range_m=lidar_range_m,
            output_range_m=output,
            forward_speed_limit_mps=limit,
            low_obstacle_confirmed=self._confirmed,
        )

    def stale_decision(self, lidar_range_m: float | None = None) -> FusionDecision:
        """Report missing input without falsely clearing accumulated costs."""
        filtered = (
            float(statistics.median(self._ranges)) if self._ranges else None)
        return FusionDecision(
            state='STALE_RANGE',
            filtered_range_m=filtered,
            lidar_range_m=lidar_range_m,
            output_range_m=None,
            forward_speed_limit_mps=(
                self._confirmed_limit(filtered) if self._confirmed else None),
            low_obstacle_confirmed=self._confirmed,
        )

    def _confirmed_limit(self, distance_m: float | None) -> float | None:
        if distance_m is None:
            return None
        if distance_m <= self.config.stop_distance_m:
            return 0.0
        if distance_m <= self.config.slow_distance_m:
            return self.config.slow_speed_mps
        return None


def limit_forward_velocity(
        linear_x: float, forward_speed_limit_mps: float | None) -> float:
    """Limit positive forward motion while preserving stop/reverse commands."""
    if forward_speed_limit_mps is None or linear_x <= forward_speed_limit_mps:
        return linear_x
    return forward_speed_limit_mps
