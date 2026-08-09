"""LiDAR 기반 임시 탈출 지점 후보 생성.

이 모듈은 ROS 타입에 의존하지 않는다. LaserScan의 수치 배열만 받아 계산하므로
센서가 없는 개발 PC에서도 판단 규칙을 단위 테스트할 수 있다. 여기서 고른 후보는
아직 이동 명령이 아니다. Nav2 ComputePathToPose가 실제 도달 가능성을 다시
검증한 뒤에만 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence


@dataclass(frozen=True)
class SelectorConfig:
    """탈출 후보 생성과 점수 계산 설정."""

    nominal_distance_m: float = 0.35
    minimum_distance_m: float = 0.15
    safety_margin_m: float = 0.14
    sector_half_width_rad: float = math.radians(15.0)
    clearance_cap_m: float = 2.0
    clearance_weight: float = 1.0
    goal_alignment_weight: float = 0.45
    turn_penalty_weight: float = 0.25
    reverse_penalty: float = 0.45
    previous_failure_penalty: float = 0.75
    clearance_quantile: float = 0.10

    def __post_init__(self) -> None:
        if self.minimum_distance_m <= 0.0:
            raise ValueError('minimum_distance_m은 0보다 커야 합니다.')
        if self.nominal_distance_m < self.minimum_distance_m:
            raise ValueError('nominal_distance_m은 minimum_distance_m 이상이어야 합니다.')
        if self.safety_margin_m < 0.0:
            raise ValueError('safety_margin_m은 음수일 수 없습니다.')
        if not 0.0 < self.sector_half_width_rad <= math.pi / 2:
            raise ValueError('sector_half_width_rad 범위가 잘못되었습니다.')
        if not 0.0 <= self.clearance_quantile <= 1.0:
            raise ValueError('clearance_quantile은 0~1이어야 합니다.')


@dataclass(frozen=True)
class EscapeCandidate:
    """로봇 기준 좌표계에 놓은 임시 탈출 지점."""

    name: str
    bearing_rad: float
    distance_m: float
    x_m: float
    y_m: float
    clearance_m: float
    score: float


# 앞쪽 후보를 먼저 두는 것은 점수가 완전히 같을 때 불필요한 후진을 피하기 위함이다.
_DIRECTIONS: tuple[tuple[str, float], ...] = (
    ('forward', 0.0),
    ('forward_left', math.radians(45.0)),
    ('forward_right', math.radians(-45.0)),
    ('left', math.radians(90.0)),
    ('right', math.radians(-90.0)),
    ('rear_left', math.radians(135.0)),
    ('rear_right', math.radians(-135.0)),
    ('rear', math.pi),
)


def _angle_delta(left: float, right: float) -> float:
    return math.atan2(math.sin(left - right), math.cos(left - right))


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def _sector_clearance(
    ranges: Sequence[float],
    *,
    angle_min: float,
    angle_increment: float,
    range_min: float,
    range_max: float,
    center: float,
    half_width: float,
    quantile: float,
) -> float | None:
    values: list[float] = []
    for index, distance in enumerate(ranges):
        if not math.isfinite(distance) or not range_min <= distance <= range_max:
            continue
        angle = angle_min + index * angle_increment
        if abs(_angle_delta(angle, center)) <= half_width:
            values.append(float(distance))
    return _quantile(values, quantile) if values else None


def select_escape_candidates(
    ranges: Sequence[float],
    *,
    angle_min: float,
    angle_increment: float,
    range_min: float,
    range_max: float,
    goal_bearing_rad: float = 0.0,
    failures: Mapping[str, int] | None = None,
    config: SelectorConfig | None = None,
) -> list[EscapeCandidate]:
    """안전 여유와 목적지 방향을 고려해 임시 탈출 후보를 점수순으로 돌려준다.

    ``goal_bearing_rad``는 로봇 정면 기준 원래 목적지의 방향이다. 실제 이동 전에는
    반환 후보를 map 좌표로 변환하고 ComputePathToPose로 검증해야 한다.
    """
    cfg = config or SelectorConfig()
    attempted = failures or {}
    if not ranges or angle_increment == 0.0 or range_max <= range_min:
        return []

    candidates: list[EscapeCandidate] = []
    for name, bearing in _DIRECTIONS:
        clearance = _sector_clearance(
            ranges,
            angle_min=angle_min,
            angle_increment=angle_increment,
            range_min=range_min,
            range_max=range_max,
            center=bearing,
            half_width=cfg.sector_half_width_rad,
            quantile=cfg.clearance_quantile,
        )
        if clearance is None:
            continue

        usable_distance = min(cfg.nominal_distance_m, clearance - cfg.safety_margin_m)
        if usable_distance < cfg.minimum_distance_m:
            continue

        alignment = math.cos(_angle_delta(bearing, goal_bearing_rad))
        turn_fraction = abs(_angle_delta(bearing, 0.0)) / math.pi
        reverse = math.cos(bearing) < 0.0
        score = (
            min(clearance, cfg.clearance_cap_m) * cfg.clearance_weight
            + alignment * cfg.goal_alignment_weight
            - turn_fraction * cfg.turn_penalty_weight
            - (cfg.reverse_penalty if reverse else 0.0)
            - attempted.get(name, 0) * cfg.previous_failure_penalty
        )
        candidates.append(EscapeCandidate(
            name=name,
            bearing_rad=bearing,
            distance_m=usable_distance,
            x_m=usable_distance * math.cos(bearing),
            y_m=usable_distance * math.sin(bearing),
            clearance_m=clearance,
            score=score,
        ))

    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


def candidate_to_map(
    candidate: EscapeCandidate,
    *,
    robot_x: float,
    robot_y: float,
    robot_yaw: float,
) -> tuple[float, float, float]:
    """로봇 기준 탈출 후보를 map 좌표의 ``x, y, yaw``로 변환한다."""
    map_x = (
        robot_x + math.cos(robot_yaw) * candidate.x_m
        - math.sin(robot_yaw) * candidate.y_m
    )
    map_y = (
        robot_y + math.sin(robot_yaw) * candidate.x_m
        + math.cos(robot_yaw) * candidate.y_m
    )
    map_yaw = math.atan2(
        math.sin(robot_yaw + candidate.bearing_rad),
        math.cos(robot_yaw + candidate.bearing_rad),
    )
    return map_x, map_y, map_yaw
