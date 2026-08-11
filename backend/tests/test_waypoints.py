from pathlib import Path

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
