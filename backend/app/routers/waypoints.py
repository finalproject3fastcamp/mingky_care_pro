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
    padding: float = Field(default=0.03, ge=0.0, le=1.0)
    margin: float = Field(default=0.15, ge=0.0, le=2.0)
    tolerance: float = Field(default=0.12, gt=0.0, le=2.0)


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


def _occupied_map() -> tuple[list[tuple[float, float]], tuple[float, float, float, float]]:
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
    return occupied, extent


def check_waypoints(request: CheckRequest) -> CheckResponse:
    occupied, (x0, x1, y0, y1) = _occupied_map()
    blocked = request.footprint + request.padding
    rows: list[tuple[str, tuple[float, float], bool]] = []
    items = []

    for name, waypoint in request.waypoints.items():
        point = (waypoint.x, waypoint.y)
        inside = x0 <= waypoint.x <= x1 and y0 <= waypoint.y <= y1
        rows.append((name, point, inside))
        if not inside:
            items.append(CheckItem(
                name=name, status="outside", clearance=None,
                message="맵 밖의 좌표입니다.",
            ))
            continue
        clearance = min(
            (math.dist(point, cell) for cell in occupied),
            default=float("inf"),
        )
        if clearance < blocked:
            status, message = "blocked", "로봇 footprint가 벽과 겹칩니다."
        elif clearance < request.margin:
            status, message = "warning", "벽과의 권장 여유가 부족합니다."
        else:
            status, message = "ok", "주행 가능한 여유가 있습니다."
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
