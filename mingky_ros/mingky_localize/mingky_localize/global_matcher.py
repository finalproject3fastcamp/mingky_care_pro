"""기존 OccupancyGrid와 2D LaserScan으로 전역 위치 후보를 찾는다.

맵 파일은 수정하지 않는다. 수신한 OccupancyGrid에서 장애물 거리장을 한 번
만들고, 거친 전역 검색 뒤 상위 후보만 정밀화한다. 점수는 LiDAR 끝점의 벽
일치도와 광선 중간의 free-space 모순을 분리해 계산한다.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np


def wrap_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


@dataclass(frozen=True)
class MatcherConfig:
    coarse_xy_m: float = 0.10
    coarse_yaw_rad: float = math.radians(15.0)
    fine_xy_m: float = 0.025
    fine_yaw_rad: float = math.radians(3.0)
    refine_xy_window_m: float = 0.10
    refine_yaw_window_rad: float = math.radians(15.0)
    tracking_xy_window_m: float = 0.05
    tracking_yaw_window_rad: float = math.radians(6.0)
    max_beams: int = 60
    top_k: int = 5
    hit_sigma_m: float = 0.075
    free_space_penalty: float = 0.35
    robot_clearance_m: float = 0.07
    cluster_xy_m: float = 0.15
    cluster_yaw_rad: float = math.radians(20.0)
    min_score: float = 0.52
    min_margin: float = 0.08


@dataclass(frozen=True)
class LaserObservation:
    """로봇 base 좌표계의 유효 LiDAR 끝점."""

    x: np.ndarray
    y: np.ndarray

    @classmethod
    def from_ranges(
        cls,
        ranges: Iterable[float],
        *,
        angle_min: float,
        angle_increment: float,
        range_min: float,
        range_max: float,
        yaw_offset_rad: float = 0.0,
        max_beams: int = 60,
    ) -> 'LaserObservation':
        values = np.asarray(tuple(ranges), dtype=np.float64)
        indices = np.arange(values.size, dtype=np.float64)
        valid = (
            np.isfinite(values)
            & (values >= range_min)
            & (values <= range_max)
        )
        values = values[valid]
        indices = indices[valid]
        if values.size > max_beams:
            selected = np.linspace(
                0, values.size - 1, max_beams, dtype=np.int64)
            values = values[selected]
            indices = indices[selected]
        angles = angle_min + indices * angle_increment + yaw_offset_rad
        return cls(x=values * np.cos(angles), y=values * np.sin(angles))

    @property
    def size(self) -> int:
        return int(self.x.size)


@dataclass(frozen=True)
class PoseHypothesis:
    x: float
    y: float
    yaw: float
    score: float
    observations: int = 1


@dataclass(frozen=True)
class MatchResult:
    hypotheses: tuple[PoseHypothesis, ...]
    min_score: float
    min_margin: float

    @property
    def best_score(self) -> float:
        return self.hypotheses[0].score if self.hypotheses else 0.0

    @property
    def margin(self) -> float:
        if not self.hypotheses:
            return 0.0
        if len(self.hypotheses) == 1:
            return self.hypotheses[0].score
        return self.hypotheses[0].score - self.hypotheses[1].score

    @property
    def confident(self) -> bool:
        return (
            bool(self.hypotheses)
            and self.best_score >= self.min_score
            and self.margin >= self.min_margin
        )


class OccupancyMap:
    """OccupancyGrid와 그로부터 만든 근사 Euclidean distance field."""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        resolution: float,
        origin_x: float,
        origin_y: float,
        origin_yaw: float,
        data: Iterable[int],
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.resolution = float(resolution)
        self.origin_x = float(origin_x)
        self.origin_y = float(origin_y)
        self.origin_yaw = float(origin_yaw)
        self.cos_origin = math.cos(self.origin_yaw)
        self.sin_origin = math.sin(self.origin_yaw)
        self.occupancy = np.asarray(tuple(data), dtype=np.int16).reshape(
            self.height, self.width)
        self.known_free = (
            (self.occupancy >= 0) & (self.occupancy < 65)
        )
        occupied = self.occupancy >= 65
        if not np.any(occupied):
            raise ValueError('맵에 점유 셀이 없습니다.')
        self.distance_m = _chamfer_distance(occupied) * self.resolution

    def world_to_grid(
        self, x: np.ndarray, y: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        dx = x - self.origin_x
        dy = y - self.origin_y
        local_x = self.cos_origin * dx + self.sin_origin * dy
        local_y = -self.sin_origin * dx + self.cos_origin * dy
        gx = np.floor(local_x / self.resolution).astype(np.int64)
        gy = np.floor(local_y / self.resolution).astype(np.int64)
        valid = (
            (gx >= 0) & (gx < self.width)
            & (gy >= 0) & (gy < self.height)
        )
        return gx, gy, valid

    def grid_centres(
        self, gx: np.ndarray, gy: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        local_x = (gx.astype(np.float64) + 0.5) * self.resolution
        local_y = (gy.astype(np.float64) + 0.5) * self.resolution
        x = (
            self.origin_x + self.cos_origin * local_x
            - self.sin_origin * local_y
        )
        y = (
            self.origin_y + self.sin_origin * local_x
            + self.cos_origin * local_y
        )
        return x, y


def _chamfer_distance(occupied: np.ndarray) -> np.ndarray:
    """작은 맵에 충분한 8-neighbour distance transform."""
    height, width = occupied.shape
    distance = np.full((height, width), np.inf, dtype=np.float64)
    distance[occupied] = 0.0
    diagonal = math.sqrt(2.0)
    for y in range(height):
        for x in range(width):
            best = distance[y, x]
            if x > 0:
                best = min(best, distance[y, x - 1] + 1.0)
            if y > 0:
                best = min(best, distance[y - 1, x] + 1.0)
                if x > 0:
                    best = min(best, distance[y - 1, x - 1] + diagonal)
                if x + 1 < width:
                    best = min(best, distance[y - 1, x + 1] + diagonal)
            distance[y, x] = best
    for y in range(height - 1, -1, -1):
        for x in range(width - 1, -1, -1):
            best = distance[y, x]
            if x + 1 < width:
                best = min(best, distance[y, x + 1] + 1.0)
            if y + 1 < height:
                best = min(best, distance[y + 1, x] + 1.0)
                if x > 0:
                    best = min(best, distance[y + 1, x - 1] + diagonal)
                if x + 1 < width:
                    best = min(best, distance[y + 1, x + 1] + diagonal)
            distance[y, x] = best
    return distance


class GlobalScanMatcher:
    """전역 coarse-to-fine 검색과 짧은 scan sequence 후보 추적."""

    def __init__(self, grid: OccupancyMap, config: MatcherConfig) -> None:
        self.grid = grid
        self.config = config

    def global_match(self, observation: LaserObservation) -> MatchResult:
        if observation.size < 8:
            return self._result(())
        stride = max(
            1, int(round(self.config.coarse_xy_m / self.grid.resolution)))
        gy, gx = np.mgrid[0:self.grid.height:stride, 0:self.grid.width:stride]
        gx = gx.reshape(-1)
        gy = gy.reshape(-1)
        usable = (
            self.grid.known_free[gy, gx]
            & (self.grid.distance_m[gy, gx]
               >= self.config.robot_clearance_m)
        )
        x, y = self.grid.grid_centres(gx[usable], gy[usable])
        raw: list[PoseHypothesis] = []
        yaw_values = np.arange(
            -math.pi, math.pi, self.config.coarse_yaw_rad)
        keep_per_yaw = max(2, self.config.top_k)
        for yaw in yaw_values:
            poses = np.column_stack((x, y, np.full(x.size, yaw)))
            scores = self._score_poses(poses, observation)
            for index in _largest_indices(scores, keep_per_yaw):
                raw.append(PoseHypothesis(
                    float(x[index]), float(y[index]), float(yaw),
                    float(scores[index])))
        coarse = self._cluster(raw, self.config.top_k * 4)
        return self._refine(observation, coarse, tracking=False)

    def update(
        self,
        observation: LaserObservation,
        hypotheses: Iterable[PoseHypothesis],
        *,
        delta_x: float,
        delta_y: float,
        delta_yaw: float,
    ) -> MatchResult:
        propagated = [
            _propagate(item, delta_x, delta_y, delta_yaw)
            for item in hypotheses
        ]
        return self._refine(observation, propagated, tracking=True)

    def _refine(
        self,
        observation: LaserObservation,
        seeds: Iterable[PoseHypothesis],
        *,
        tracking: bool,
    ) -> MatchResult:
        all_candidates: list[PoseHypothesis] = []
        xy_window = (
            self.config.tracking_xy_window_m if tracking
            else self.config.refine_xy_window_m
        )
        yaw_window = (
            self.config.tracking_yaw_window_rad if tracking
            else self.config.refine_yaw_window_rad
        )
        xy_offsets = _symmetric_offsets(xy_window, self.config.fine_xy_m)
        yaw_offsets = _symmetric_offsets(
            yaw_window, self.config.fine_yaw_rad)
        for seed in seeds:
            poses = np.asarray([
                (seed.x + dx, seed.y + dy, wrap_angle(seed.yaw + dyaw))
                for dx in xy_offsets
                for dy in xy_offsets
                for dyaw in yaw_offsets
            ], dtype=np.float64)
            scores = self._score_poses(poses, observation)
            combined = (
                (seed.score * seed.observations + scores)
                / (seed.observations + 1)
                if tracking else scores
            )
            keep = min(self.config.top_k * 3, poses.shape[0])
            for index in _largest_indices(combined, keep):
                all_candidates.append(PoseHypothesis(
                    float(poses[index, 0]), float(poses[index, 1]),
                    float(poses[index, 2]), float(combined[index]),
                    seed.observations + 1 if tracking else 1))
        return self._result(self._cluster(all_candidates, self.config.top_k))

    def _score_poses(
        self, poses: np.ndarray, observation: LaserObservation,
    ) -> np.ndarray:
        if poses.size == 0:
            return np.empty(0, dtype=np.float64)
        output = np.zeros(poses.shape[0], dtype=np.float64)
        fractions = np.asarray((0.25, 0.50, 0.75), dtype=np.float64)
        free_x = (observation.x[:, None] * fractions).reshape(-1)
        free_y = (observation.y[:, None] * fractions).reshape(-1)
        for start in range(0, poses.shape[0], 512):
            stop = min(start + 512, poses.shape[0])
            block = poses[start:stop]
            base_gx, base_gy, base_valid = self.grid.world_to_grid(
                block[:, 0], block[:, 1])
            base_safe = base_valid.copy()
            valid_indices = np.flatnonzero(base_valid)
            if valid_indices.size:
                bx = base_gx[valid_indices]
                by = base_gy[valid_indices]
                base_safe[valid_indices] = (
                    self.grid.known_free[by, bx]
                    & (self.grid.distance_m[by, bx]
                       >= self.config.robot_clearance_m)
                )

            cos_yaw = np.cos(block[:, 2])[:, None]
            sin_yaw = np.sin(block[:, 2])[:, None]
            end_x = (
                block[:, 0, None] + cos_yaw * observation.x
                - sin_yaw * observation.y
            )
            end_y = (
                block[:, 1, None] + sin_yaw * observation.x
                + cos_yaw * observation.y
            )
            gx, gy, valid = self.grid.world_to_grid(end_x, end_y)
            distance = np.full(end_x.shape, np.inf, dtype=np.float64)
            flat_valid = np.flatnonzero(valid)
            if flat_valid.size:
                flat_gx = gx.reshape(-1)[flat_valid]
                flat_gy = gy.reshape(-1)[flat_valid]
                distance.reshape(-1)[flat_valid] = self.grid.distance_m[
                    flat_gy, flat_gx]
            hit_score = np.exp(
                -0.5 * np.square(distance / self.config.hit_sigma_m))

            ray_x = (
                block[:, 0, None] + cos_yaw * free_x
                - sin_yaw * free_y
            )
            ray_y = (
                block[:, 1, None] + sin_yaw * free_x
                + cos_yaw * free_y
            )
            ray_gx, ray_gy, ray_valid = self.grid.world_to_grid(ray_x, ray_y)
            ray_free = np.zeros(ray_x.shape, dtype=bool)
            ray_indices = np.flatnonzero(ray_valid)
            if ray_indices.size:
                rx = ray_gx.reshape(-1)[ray_indices]
                ry = ray_gy.reshape(-1)[ray_indices]
                ray_free.reshape(-1)[ray_indices] = self.grid.known_free[ry, rx]
            contradiction = 1.0 - np.mean(ray_free, axis=1)
            scores = (
                np.mean(hit_score, axis=1)
                - self.config.free_space_penalty * contradiction
            )
            output[start:stop] = np.where(
                base_safe, np.clip(scores, 0.0, 1.0), 0.0)
        return output

    def _cluster(
        self, hypotheses: Iterable[PoseHypothesis], limit: int,
    ) -> tuple[PoseHypothesis, ...]:
        selected: list[PoseHypothesis] = []
        for item in sorted(hypotheses, key=lambda value: value.score, reverse=True):
            if item.score <= 0.0:
                continue
            same_cluster = any(
                math.hypot(item.x - kept.x, item.y - kept.y)
                < self.config.cluster_xy_m
                and abs(wrap_angle(item.yaw - kept.yaw))
                < self.config.cluster_yaw_rad
                for kept in selected
            )
            if not same_cluster:
                selected.append(item)
                if len(selected) >= limit:
                    break
        return tuple(selected)

    def _result(
        self, hypotheses: Iterable[PoseHypothesis],
    ) -> MatchResult:
        return MatchResult(
            tuple(hypotheses), self.config.min_score, self.config.min_margin)


def _largest_indices(values: np.ndarray, count: int) -> np.ndarray:
    if values.size == 0 or count <= 0:
        return np.empty(0, dtype=np.int64)
    count = min(count, values.size)
    if count == values.size:
        return np.argsort(values)[::-1]
    indices = np.argpartition(values, -count)[-count:]
    return indices[np.argsort(values[indices])[::-1]]


def _symmetric_offsets(window: float, step: float) -> np.ndarray:
    count = max(0, int(math.ceil(window / step)))
    return np.arange(-count, count + 1, dtype=np.float64) * step


def _propagate(
    hypothesis: PoseHypothesis,
    delta_x: float,
    delta_y: float,
    delta_yaw: float,
) -> PoseHypothesis:
    cos_yaw = math.cos(hypothesis.yaw)
    sin_yaw = math.sin(hypothesis.yaw)
    return PoseHypothesis(
        x=(hypothesis.x + cos_yaw * delta_x - sin_yaw * delta_y),
        y=(hypothesis.y + sin_yaw * delta_x + cos_yaw * delta_y),
        yaw=wrap_angle(hypothesis.yaw + delta_yaw),
        score=hypothesis.score,
        observations=hypothesis.observations,
    )
