"""ROS-independent low-obstacle filtering and LiDAR cone comparison."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import statistics
from typing import Sequence


def guide_navigation_segment_active(
        session_state: str, robot_state: str, returning_to_dock: bool) -> bool:
    """Return whether Guide Manager owns one in-progress navigation segment."""
    return bool(
        returning_to_dock
        or session_state == 'guiding'
        or (session_state == 'arrived' and robot_state == 'moving')
    )


class NavigationScope:
    """Combine guidance and Waypoint Test lifecycles into one costmap scope."""

    def __init__(self) -> None:
        """Start with no managed navigation source active."""
        self._sources = {
            'guidance': False,
            'waypoint_test': False,
        }

    @property
    def active(self) -> bool:
        """Return whether any managed navigation task is active."""
        return any(self._sources.values())

    def update(self, source: str, active: bool) -> str | None:
        """Update one source and return an aggregate lifecycle transition."""
        if source not in self._sources:
            raise ValueError(f'지원하지 않는 주행 소스입니다: {source}')
        was_active = self.active
        self._sources[source] = bool(active)
        is_active = self.active
        if is_active and not was_active:
            return 'started'
        if was_active and not is_active:
            return 'finished'
        return None


@dataclass(frozen=True)
class FusionConfig:
    """Thresholds for conservative low-obstacle confirmation."""

    detect_distance_m: float = 0.40
    clear_distance_m: float = 0.45
    slow_distance_m: float = 0.25
    stop_distance_m: float = 0.04
    costmap_min_range_m: float = 0.20
    slow_speed_mps: float = 0.08
    lidar_margin_m: float = 0.15
    side_wall_guard_distance_m: float = 0.22
    side_wall_reflection_max_range_m: float = 0.10
    median_samples: int = 3
    confirmation_window: int = 3
    confirmations_required: int = 2
    clear_confirmations: int = 2
    near_window: int = 3
    near_confirmations: int = 2

    def __post_init__(self) -> None:
        if not 0.0 < self.stop_distance_m < self.slow_distance_m:
            raise ValueError('stop_distance_m은 slow_distance_m보다 작아야 합니다.')
        if not self.slow_distance_m < self.detect_distance_m:
            raise ValueError('slow_distance_m은 detect_distance_m보다 작아야 합니다.')
        if not self.stop_distance_m < self.costmap_min_range_m:
            raise ValueError(
                'costmap_min_range_m은 stop_distance_m보다 커야 합니다.')
        if self.clear_distance_m <= self.detect_distance_m:
            raise ValueError('clear_distance_m은 detect_distance_m보다 커야 합니다.')
        if self.side_wall_guard_distance_m <= 0.0:
            raise ValueError('side_wall_guard_distance_m은 0보다 커야 합니다.')
        if not 0.0 < self.side_wall_reflection_max_range_m < self.detect_distance_m:
            raise ValueError(
                'side_wall_reflection_max_range_m은 0보다 크고 '
                'detect_distance_m보다 작아야 합니다.')
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
    wall_reflection_likely: bool


@dataclass(frozen=True)
class ObstacleCone:
    """One map/odom-fixed ultrasonic observation with angular uncertainty."""

    origin_x: float
    origin_y: float
    yaw: float
    range_m: float
    fov_rad: float


def _angle_delta(first: float, second: float) -> float:
    return math.atan2(math.sin(first - second), math.cos(first - second))


def observation_pose_distinct(
        previous: ObstacleCone | None, current: ObstacleCone, *,
        distance_m: float = 0.04,
        yaw_rad: float = math.radians(5.0)) -> bool:
    """Return whether a new cone contributes meaningfully different geometry."""
    if previous is None:
        return True
    moved = math.hypot(
        current.origin_x - previous.origin_x,
        current.origin_y - previous.origin_y,
    )
    return moved >= distance_m or abs(
        _angle_delta(current.yaw, previous.yaw)) >= yaw_rad


def _point_in_observation_zone(
        point: tuple[float, float], cone: ObstacleCone, *,
        radial_tolerance_m: float) -> bool:
    dx = point[0] - cone.origin_x
    dy = point[1] - cone.origin_y
    distance = math.hypot(dx, dy)
    if abs(distance - cone.range_m) > radial_tolerance_m:
        return False
    bearing = math.atan2(dy, dx)
    return abs(_angle_delta(bearing, cone.yaw)) <= cone.fov_rad / 2.0


def _observation_zone_samples(
        cone: ObstacleCone, *, radial_tolerance_m: float,
        angular_samples: int = 17,
        radial_samples: int = 5) -> list[tuple[float, float]]:
    """Return a bounded, deterministic sample of one annular cone."""
    points: list[tuple[float, float]] = []
    for radial_index in range(max(2, radial_samples)):
        ratio = radial_index / (max(2, radial_samples) - 1)
        radius = max(
            0.01,
            cone.range_m - radial_tolerance_m
            + ratio * radial_tolerance_m * 2.0,
        )
        for angle_index in range(max(2, angular_samples)):
            angle_ratio = angle_index / (max(2, angular_samples) - 1)
            angle = cone.yaw - cone.fov_rad / 2.0 + angle_ratio * cone.fov_rad
            points.append((
                cone.origin_x + radius * math.cos(angle),
                cone.origin_y + radius * math.sin(angle),
            ))
    return points


def observation_overlap_estimate(
        cones: Sequence[ObstacleCone], *,
        radial_tolerance_m: float = 0.05,
) -> tuple[float, float, float] | None:
    """Estimate only the region supported by two or more different cones.

    The input is deliberately capped by the caller at three observations.
    Sampling therefore has a small fixed upper bound and never grows with the
    sensor frame rate or navigation duration.
    """
    if len(cones) < 2:
        return None
    matches: list[tuple[float, float]] = []
    for source in cones:
        for point in _observation_zone_samples(
                source, radial_tolerance_m=radial_tolerance_m):
            if all(_point_in_observation_zone(
                    point, cone,
                    radial_tolerance_m=radial_tolerance_m) for cone in cones):
                matches.append(point)
    if not matches:
        return None
    x = statistics.fmean(point[0] for point in matches)
    y = statistics.fmean(point[1] for point in matches)
    radius = max(
        0.02,
        max(math.hypot(point[0] - x, point[1] - y) for point in matches),
    )
    return x, y, min(radius, radial_tolerance_m * 2.0)


def retained_cone_speed_limit(
        pose: tuple[float, float, float] | None,
        cone_clusters: Sequence[Sequence[ObstacleCone]], *,
        overlap_estimates: Sequence[
            tuple[float, float, float] | None] | None = None,
        sensor_offset_m: float,
        corridor_half_width_m: float,
        slow_distance_m: float,
        stop_distance_m: float,
        slow_speed_mps: float,
        radial_tolerance_m: float = 0.05,
) -> float | None:
    """Limit forward motion using bounded cone uncertainty, not a guessed dot."""
    if pose is None:
        return None
    cos_yaw = math.cos(pose[2])
    sin_yaw = math.sin(pose[2])
    nearest_sensor_distance: float | None = None
    for index, cluster in enumerate(cone_clusters):
        if not cluster:
            continue
        overlap = (
            overlap_estimates[index]
            if overlap_estimates is not None
            and index < len(overlap_estimates)
            else observation_overlap_estimate(
                cluster, radial_tolerance_m=radial_tolerance_m)
        )
        if overlap is not None:
            candidates = [(overlap[0], overlap[1])]
        else:
            # One observation cannot identify an angle. Conservatively test a
            # small fixed sample of only the latest cone against the corridor.
            candidates = _observation_zone_samples(
                cluster[-1], radial_tolerance_m=radial_tolerance_m,
                angular_samples=9, radial_samples=3)
        for point in candidates:
            dx = point[0] - pose[0]
            dy = point[1] - pose[1]
            forward = cos_yaw * dx + sin_yaw * dy
            lateral = -sin_yaw * dx + cos_yaw * dy
            if forward <= 0.0 or abs(lateral) > corridor_half_width_m:
                continue
            sensor_distance = forward - sensor_offset_m
            if (
                    nearest_sensor_distance is None
                    or sensor_distance < nearest_sensor_distance):
                nearest_sensor_distance = sensor_distance
    if nearest_sensor_distance is None:
        return None
    if nearest_sensor_distance <= stop_distance_m:
        return 0.0
    if nearest_sensor_distance > slow_distance_m:
        return None
    remaining = nearest_sensor_distance - stop_distance_m
    slowing_span = slow_distance_m - stop_distance_m
    return slow_speed_mps * remaining / slowing_span


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
    return nearest_lidar_in_ultrasonic_cones(
        ranges,
        angle_min=angle_min,
        angle_increment=angle_increment,
        range_min=range_min,
        range_max=range_max,
        scan_to_ultrasonic_x=scan_to_ultrasonic_x,
        scan_to_ultrasonic_y=scan_to_ultrasonic_y,
        scan_to_ultrasonic_yaw=scan_to_ultrasonic_yaw,
        ultrasonic_fovs=(ultrasonic_fov,),
    )[0]


def nearest_lidar_in_ultrasonic_cones(
        ranges: Sequence[float], *, angle_min: float, angle_increment: float,
        range_min: float, range_max: float, scan_to_ultrasonic_x: float,
        scan_to_ultrasonic_y: float, scan_to_ultrasonic_yaw: float,
        ultrasonic_fovs: Sequence[float]) -> list[float | None]:
    """Return nearest ranges for several cones after one scan transformation."""
    fovs = tuple(float(fov) for fov in ultrasonic_fovs)
    if (
            not ranges or not fovs or not math.isfinite(angle_increment)
            or angle_increment == 0.0 or range_max <= range_min
            or any(not 0.0 < fov < math.pi for fov in fovs)):
        return [None] * len(fovs)

    cos_yaw = math.cos(scan_to_ultrasonic_yaw)
    sin_yaw = math.sin(scan_to_ultrasonic_yaw)
    half_fovs = [fov / 2.0 for fov in fovs]
    nearest: list[float | None] = [None] * len(fovs)

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
        bearing = abs(math.atan2(cone_y, cone_x))
        endpoint_distance = math.hypot(cone_x, cone_y)
        for cone_index, half_fov in enumerate(half_fovs):
            if bearing > half_fov:
                continue
            if (
                    nearest[cone_index] is None
                    or endpoint_distance < nearest[cone_index]):
                nearest[cone_index] = endpoint_distance

    return nearest


def observation_pose_expired(
        anchor: tuple[float, float, float],
        current: tuple[float, float, float], *,
        distance_m: float, yaw_rad: float,
        preserve_forward_approach: bool = False) -> bool:
    """Return whether a robot-relative obstacle observation became obsolete."""
    dx = current[0] - anchor[0]
    dy = current[1] - anchor[1]
    yaw_delta = math.atan2(
        math.sin(current[2] - anchor[2]),
        math.cos(current[2] - anchor[2]),
    )
    if preserve_forward_approach:
        # A confirmed obstacle must remain in the costmap while the robot is
        # merely approaching it. Expire only after lateral avoidance, backing
        # away, or a meaningful heading change shows that the original
        # robot-relative cone is no longer the current driving corridor.
        cos_yaw = math.cos(anchor[2])
        sin_yaw = math.sin(anchor[2])
        forward = cos_yaw * dx + sin_yaw * dy
        lateral = -sin_yaw * dx + cos_yaw * dy
        translation_expired = (
            abs(lateral) >= distance_m or forward <= -distance_m)
    else:
        translation_expired = math.hypot(dx, dy) >= distance_m
    return translation_expired or abs(yaw_delta) >= yaw_rad


def matching_observation_cluster(
        endpoints: Sequence[tuple[float, float] | None],
        candidate: tuple[float, float] | None, *,
        merge_distance_m: float) -> int | None:
    """Return the nearest spatial observation cluster within the merge radius."""
    if not endpoints:
        return None
    if candidate is None:
        # TF/odometry can be briefly unavailable. Replacing the latest cone is
        # safer than turning one sensor sample into another persistent object.
        return len(endpoints) - 1
    matches = [
        (math.hypot(candidate[0] - endpoint[0], candidate[1] - endpoint[1]), i)
        for i, endpoint in enumerate(endpoints)
        if endpoint is not None
    ]
    if not matches:
        return len(endpoints) - 1
    distance, index = min(matches)
    return index if distance <= merge_distance_m else None


def retained_obstacle_speed_limit(
        pose: tuple[float, float, float] | None,
        endpoints: Sequence[tuple[float, float] | None], *,
        sensor_offset_m: float,
        corridor_half_width_m: float,
        slow_distance_m: float,
        stop_distance_m: float,
        slow_speed_mps: float) -> float | None:
    """Limit forward motion toward a retained map obstacle.

    A low object can leave the narrow ultrasonic cone while still being inside
    the robot's swept footprint.  Stored odom-frame endpoints bridge that short
    sensor dropout.  Turning or moving laterally out of the corridor releases
    the gate naturally, so a valid Nav2 detour is not blocked.
    """
    if pose is None:
        return None
    cos_yaw = math.cos(pose[2])
    sin_yaw = math.sin(pose[2])
    nearest_sensor_distance: float | None = None
    for endpoint in endpoints:
        if endpoint is None:
            continue
        dx = endpoint[0] - pose[0]
        dy = endpoint[1] - pose[1]
        forward = cos_yaw * dx + sin_yaw * dy
        lateral = -sin_yaw * dx + cos_yaw * dy
        if forward <= 0.0 or abs(lateral) > corridor_half_width_m:
            continue
        sensor_distance = forward - sensor_offset_m
        if (
                nearest_sensor_distance is None
                or sensor_distance < nearest_sensor_distance):
            nearest_sensor_distance = sensor_distance
    if nearest_sensor_distance is None:
        return None
    if nearest_sensor_distance <= stop_distance_m:
        return 0.0
    if nearest_sensor_distance > slow_distance_m:
        return None
    remaining = nearest_sensor_distance - stop_distance_m
    slowing_span = slow_distance_m - stop_distance_m
    return slow_speed_mps * remaining / slowing_span


class CostmapObservationRetention:
    """Keep a temporary obstacle stable while Nav2 replans around it."""

    def __init__(self, hold_sec: float):
        self.hold_ns = max(0, int(hold_sec * 1_000_000_000))
        self.deadline_ns = 0
        self.reason = ''
        self.suppress_until_sensor_clear = False

    def on_detection(self, confirmed: bool) -> None:
        if not confirmed:
            self.suppress_until_sensor_clear = False
            return
        if not self.suppress_until_sensor_clear:
            self.cancel_pending_clear()

    def request_clear(
            self, now_ns: int, reason: str, *,
            suppress_until_sensor_clear: bool = False) -> None:
        if self.deadline_ns <= 0:
            self.deadline_ns = now_ns + self.hold_ns
            self.reason = reason
        if suppress_until_sensor_clear:
            self.suppress_until_sensor_clear = True

    def clear_due(self, now_ns: int) -> bool:
        return self.deadline_ns > 0 and now_ns >= self.deadline_ns

    def mark_cleared(self) -> None:
        self.deadline_ns = 0
        self.reason = ''

    def cancel_pending_clear(self) -> None:
        self.deadline_ns = 0
        self.reason = ''


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
            lidar_fresh: bool,
            wall_context_range_m: float | None = None) -> FusionDecision:
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
        wall_context_comparable = (
            comparable and wall_context_range_m is not None
            and math.isfinite(wall_context_range_m))
        # 초음파 한 개로는 반사파의 실제 방향을 알 수 없다. 다만 코너에서
        # 관측된 실제 오반사는 5cm 안팎의 짧은 튐이었다. 가까운 옆 벽이
        # 있다는 이유만으로 20~40cm 앞의 실제 물체까지 지우지 않도록,
        # 반사 억제는 아직 확정되지 않은 짧은 에코에만 적용한다.
        # 한 번 확정한 장애물은 벽 문맥이 흔들려도 clear 거리의 실제 센서
        # 해제가 연속 확인될 때까지 유지해 전진 게이트가 풀리지 않게 한다.
        wall_reflection_likely = bool(
            wall_context_comparable
            and not self._confirmed
            and filtered <= self.config.side_wall_reflection_max_range_m
            and wall_context_range_m
            <= self.config.side_wall_guard_distance_m
            and lidar_range_m
            >= wall_context_range_m + self.config.lidar_margin_m)
        mismatch = bool(
            comparable
            and not wall_reflection_likely
            and lidar_range_m >= filtered + self.config.lidar_margin_m)
        candidate = mismatch and filtered <= self.config.detect_distance_m
        present_now = mismatch and filtered < self.config.clear_distance_m
        raw_mismatch = bool(
            comparable
            and not wall_reflection_likely
            and lidar_range_m >= (
                ultrasonic_range_m + self.config.lidar_margin_m))
        present_in_raw_sample = (
            raw_mismatch
            and ultrasonic_range_m < self.config.clear_distance_m)
        self._evidence.append(candidate)
        self._near_evidence.append(
            mismatch and ultrasonic_range_m <= self.config.stop_distance_m)

        if self._confirmed:
            # The wider clear threshold prevents chatter at the detection edge.
            # Clear on two consecutive raw absences. Using the raw sample here
            # makes the hold deterministic (about 0.2 s at the deployed 10 Hz),
            # while the median handles distance and initial confirmation.
            self._clear_streak = (
                0 if present_in_raw_sample else self._clear_streak + 1)
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
            and present_now
            and len(self._near_evidence) >= self.config.near_confirmations
            and sum(self._near_evidence) >= self.config.near_confirmations)

        if not comparable:
            state = 'STALE_LIDAR'
            output = (
                self._costmap_output(filtered, max_range_m)
                if self._confirmed else None)
            limit = self._confirmed_limit(filtered)
        elif near:
            state = 'FORWARD_BLOCKED'
            output = self._costmap_output(filtered, max_range_m)
            limit = 0.0
        elif self._confirmed and not present_now:
            # Keep the old costmap cone during the short clear hold. Publishing
            # nothing avoids moving it to a noisy one-frame distance. The
            # second raw absence reaches CLEAR and publishes max range.
            state = 'UNCERTAIN'
            output = None
            limit = self._confirmed_limit(filtered)
        elif self._confirmed and filtered <= self.config.slow_distance_m:
            state = 'SLOW'
            output = self._costmap_output(filtered, max_range_m)
            limit = self._confirmed_limit(filtered)
        elif self._confirmed:
            state = 'CONFIRMED'
            output = self._costmap_output(filtered, max_range_m)
            limit = None
        elif candidate:
            state = 'UNCERTAIN'
            output = None
            limit = None
        else:
            state = 'CLEAR'
            # An exact max-range reading explicitly clears RangeSensorLayer.
            output = max_range_m
            limit = None

        return FusionDecision(
            state=state,
            filtered_range_m=filtered,
            lidar_range_m=lidar_range_m,
            output_range_m=output,
            forward_speed_limit_mps=limit,
            low_obstacle_confirmed=self._confirmed,
            wall_reflection_likely=wall_reflection_likely,
        )

    def stale_decision(
            self, lidar_range_m: float | None = None) -> FusionDecision:
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
            wall_reflection_likely=False,
        )

    def _confirmed_limit(self, distance_m: float | None) -> float | None:
        if distance_m is None:
            return None
        if distance_m <= self.config.stop_distance_m:
            return 0.0
        if distance_m <= self.config.slow_distance_m:
            # A fixed 0.08 m/s limit until the 4 cm boundary still carries
            # enough momentum and smoothing latency to overshoot the desired
            # clearance. Reduce speed continuously as that boundary approaches
            # while keeping 4 cm as the actual zero-velocity threshold.
            remaining = distance_m - self.config.stop_distance_m
            slowing_span = (
                self.config.slow_distance_m - self.config.stop_distance_m)
            return self.config.slow_speed_mps * remaining / slowing_span
        return None

    def _costmap_output(self, distance_m: float, max_range_m: float) -> float:
        """
        Clamp a near echo just outside the padded footprint.

        A RangeSensorLayer endpoint immediately against the padded footprint
        makes every MPPI trajectory start in collision.  Clearing that echo,
        however, hides the obstacle from the global planner and prevents a
        detour.  Clamp it to the configured safe range instead; the separate
        velocity gate still enforces the real measured stop distance.
        """
        return min(
            max(distance_m, self.config.costmap_min_range_m), max_range_m)


def limit_forward_velocity(
        linear_x: float, forward_speed_limit_mps: float | None) -> float:
    """Limit positive forward motion while preserving stop/reverse commands."""
    if forward_speed_limit_mps is None or linear_x <= forward_speed_limit_mps:
        return linear_x
    return forward_speed_limit_mps
