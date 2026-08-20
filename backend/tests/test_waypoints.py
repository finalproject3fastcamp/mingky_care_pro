import math
from pathlib import Path

import pytest

from backend.app.routers import maps, waypoints


ROOT = Path(__file__).resolve().parents[2]


def configure_files(monkeypatch):
    map_file = ROOT / "mingky_ros/mingky_bringup/map/yun_map_highres_clean.yaml"
    waypoint_file = (
        ROOT
        / "mingky_ros/mingky_bringup/config/waypoints"
        / "yun_map_highres_clean_waypoints.yaml"
    )
    monkeypatch.setattr(maps, "MAP_YAML", map_file)
    monkeypatch.setattr(waypoints, "MAP_YAML", map_file)
    monkeypatch.setattr(waypoints, "WAYPOINTS_FILE", waypoint_file)


def test_loads_repository_waypoints(monkeypatch):
    configure_files(monkeypatch)

    result = waypoints._load_waypoints()

    assert result.map_name == "yun_map_highres_clean"
    assert "xray_room_goal" in result.waypoints
    assert result.visit_waypoints["X-ray"]["goal"] == "xray_room_goal"


def test_check_rejects_outside_point(monkeypatch):
    configure_files(monkeypatch)
    request = waypoints.CheckRequest(waypoints={
        "outside": waypoints.Waypoint(x=999.0, y=999.0, yaw=0.0),
    })

    result = waypoints.check_waypoints(request)

    assert result.ok is False
    assert result.items[0].status == "outside"


def test_check_reports_too_close_pair(monkeypatch):
    configure_files(monkeypatch)
    request = waypoints.CheckRequest(waypoints={
        "first": waypoints.Waypoint(x=0.0, y=0.0, yaw=0.0),
        "second": waypoints.Waypoint(x=0.1, y=0.0, yaw=0.0),
    })

    result = waypoints.check_waypoints(request)

    assert [(item.first, item.second) for item in result.conflicts] == [
        ("first", "second")
    ]


def test_check_defaults_match_nav2_and_clearance_policy():
    request = waypoints.CheckRequest(waypoints={})

    assert request.footprint == 0.06
    assert request.padding == 0.01
    assert request.minimum_clearance == 0.01
    assert request.margin == 0.08
    assert request.tolerance == 0.07


def test_check_measures_clearance_from_body_not_center(monkeypatch):
    monkeypatch.setattr(
        waypoints,
        "_occupied_map",
        lambda: ([(0.13, 0.0)], (-1.0, 1.0, -1.0, 1.0), 0.02),
    )
    request = waypoints.CheckRequest(waypoints={
        "warning": waypoints.Waypoint(x=0.0, y=0.0, yaw=0.0),
    })

    result = waypoints.check_waypoints(request)

    assert result.ok is True
    assert result.items[0].status == "warning"
    assert result.items[0].clearance == pytest.approx(
        0.13 - 0.06 - 0.02 / math.sqrt(2.0)
    )


def test_check_applies_waypoint_yaw_to_footprint(monkeypatch):
    monkeypatch.setattr(
        waypoints,
        "_occupied_map",
        lambda: ([(0.105, 0.0)], (-1.0, 1.0, -1.0, 1.0), 0.02),
    )
    request = waypoints.CheckRequest(waypoints={
        "rotated": waypoints.Waypoint(x=0.0, y=0.0, yaw=math.pi / 4.0),
    })

    result = waypoints.check_waypoints(request)

    assert result.ok is False
    assert result.items[0].status == "blocked"
    assert result.items[0].clearance < 0.01
