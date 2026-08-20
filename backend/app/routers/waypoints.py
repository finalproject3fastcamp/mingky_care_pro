"""엔지니어용 waypoint 조회와 정적 안전 검사."""

from __future__ import annotations

import math
import os
from itertools import combinations
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .maps import MAP_YAML, _read_pgm

router = APIRouter(prefix="/waypoints", tags=["waypoints"])

WAYPOINTS_FILE = Path(os.environ.get(
    "WAYPOINTS_FILE",
    "/srv/mingky/waypoints/yun_map_highres_clean_waypoints.yaml",
))
EXCLUSIVE_PREFIX = "charging_station_"


class Waypoint(BaseModel):
    x: float
    y: float
    yaw: float


class WaypointSet(BaseModel):
    map_name: str
    visit_waypoints: dict[str, dict[str, str | None]]
    waypoints: dict[str, Waypoint]


class CheckRequest(BaseModel):
    waypoints: dict[str, Waypoint]
    footprint: float = Field(default=0.06, ge=0.0, le=1.0)
    padding: float = Field(default=0.01, ge=0.0, le=1.0)
    minimum_clearance: float = Field(default=0.01, ge=0.0, le=2.0)
    margin: float = Field(default=0.08, ge=0.0, le=2.0)
    tolerance: float = Field(default=0.07, gt=0.0, le=2.0)


class CheckItem(BaseModel):
    name: str
    status: str
    clearance: float | None
    message: str


class SpacingConflict(BaseModel):
    first: str
    second: str
    distance: float


class CheckResponse(BaseModel):
    ok: bool
    items: list[CheckItem]
    conflicts: list[SpacingConflict]


def _load_waypoints() -> WaypointSet:
    data = yaml.safe_load(WAYPOINTS_FILE.read_text(encoding="utf-8")) or {}
    return WaypointSet(
        map_name=MAP_YAML.stem,
        visit_waypoints=data.get("visit_waypoints") or {},
        waypoints=data.get("waypoints") or {},
    )


def _occupied_map() -> tuple[
    list[tuple[float, float]],
    tuple[float, float, float, float],
    float,
]:
    meta = yaml.safe_load(MAP_YAML.read_text(encoding="utf-8")) or {}
    width, height, pixels = _read_pgm(MAP_YAML.parent / meta["image"])
    resolution = float(meta["resolution"])
    origin_x, origin_y = float(meta["origin"][0]), float(meta["origin"][1])
    threshold = float(meta.get("occupied_thresh", 0.65))

    occupied = []
    for row in range(height):
        base = row * width
        y = origin_y + (height - 1 - row + 0.5) * resolution
        for col in range(width):
            if (255 - pixels[base + col]) / 255.0 > threshold:
                occupied.append((origin_x + (col + 0.5) * resolution, y))
    extent = (
        origin_x,
        origin_x + width * resolution,
        origin_y,
        origin_y + height * resolution,
    )
    return occupied, extent, resolution


def _footprint_vertices(
    point: tuple[float, float], yaw: float, half_extent: float,
) -> list[tuple[float, float]]:
    """12x12cm 정사각 footprint를 waypoint yaw에 맞춰 회전한다."""
    cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)
    result = []
    for local_x, local_y in (
        (half_extent, half_extent),
        (half_extent, -half_extent),
        (-half_extent, -half_extent),
        (-half_extent, half_extent),
    ):
        result.append((
            point[0] + cos_yaw * local_x - sin_yaw * local_y,
            point[1] + sin_yaw * local_x + cos_yaw * local_y,
        ))
    return result


def _body_clearance(
    point: tuple[float, float],
    yaw: float,
    half_extent: float,
    occupied: list[tuple[float, float]],
    resolution: float,
) -> float:
    """회전 footprint 외곽부터 가장 가까운 점유 셀까지의 여유를 구한다.

    점유 셀을 중심점으로만 보면 최대 반 셀만큼 여유를 크게 계산한다.
    셀의 반대각 길이를 빼서 실제 셀 영역에 대해 보수적으로 판정한다.
    """
    if not occupied:
        return float("inf")
    cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)
    cell_radius = resolution / math.sqrt(2.0)
    nearest = float("inf")
    for wall_x, wall_y in occupied:
        dx, dy = wall_x - point[0], wall_y - point[1]
        local_x = cos_yaw * dx + sin_yaw * dy
        local_y = -sin_yaw * dx + cos_yaw * dy
        outside_x = max(abs(local_x) - half_extent, 0.0)
        outside_y = max(abs(local_y) - half_extent, 0.0)
        distance = max(math.hypot(outside_x, outside_y) - cell_radius, 0.0)
        nearest = min(nearest, distance)
    return nearest


def check_waypoints(request: CheckRequest) -> CheckResponse:
    occupied, (x0, x1, y0, y1), resolution = _occupied_map()
    # Nav2 padding 1cm를 기본 하한으로 삼아 실제 costmap 충돌을 통과시키는
    # 구성은 허용하지 않되, 그 밖의 좁은 지점은 경고 상태로 시험할 수 있다.
    blocked = max(request.minimum_clearance, request.padding)
    rows: list[tuple[str, tuple[float, float], bool]] = []
    items = []

    for name, waypoint in request.waypoints.items():
        point = (waypoint.x, waypoint.y)
        footprint = _footprint_vertices(
            point, waypoint.yaw, request.footprint)
        inside = all(
            x0 <= x <= x1 and y0 <= y <= y1 for x, y in footprint)
        rows.append((name, point, inside))
        if not inside:
            items.append(CheckItem(
                name=name, status="outside", clearance=None,
                message="맵 밖의 좌표입니다.",
            ))
            continue
        clearance = _body_clearance(
            point,
            waypoint.yaw,
            request.footprint,
            occupied,
            resolution,
        )
        if clearance < blocked:
            status, message = (
                "blocked",
                "차체와 벽 사이 여유가 1cm 미만입니다.",
            )
        elif clearance < request.margin:
            status, message = (
                "warning",
                "차체와 벽 사이 여유가 8cm 미만입니다.",
            )
        else:
            status, message = "ok", "차체와 벽 사이 여유가 충분합니다."
        items.append(CheckItem(
            name=name, status=status, clearance=clearance, message=message,
        ))

    conflicts = []
    diameter = request.tolerance * 2
    for (first, p1, in1), (second, p2, in2) in combinations(rows, 2):
        if not (in1 and in2):
            continue
        distance = math.dist(p1, p2)
        if distance >= diameter:
            continue
        if first.startswith(EXCLUSIVE_PREFIX) and second.startswith(EXCLUSIVE_PREFIX):
            continue
        conflicts.append(SpacingConflict(
            first=first, second=second, distance=distance,
        ))

    return CheckResponse(
        ok=not any(item.status in ("blocked", "outside") for item in items),
        items=items,
        conflicts=conflicts,
    )


@router.get("", response_model=WaypointSet)
async def list_waypoints() -> WaypointSet:
    try:
        return _load_waypoints()
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=404, detail=f"waypoint를 읽지 못했다: {exc}") from exc


@router.post("/check", response_model=CheckResponse)
async def check(request: CheckRequest) -> CheckResponse:
    try:
        return check_waypoints(request)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=404, detail=f"지도를 검사하지 못했다: {exc}") from exc
