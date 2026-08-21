from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Path

from mingky_teleop.mode_sync import ModeAlignmentMonitor
from mingky_teleop.teleop_bridge import (
    parse_low_obstacle_observation,
    TeleopBridge,
)


def test_short_mismatch_is_not_reported():
    monitor = ModeAlignmentMonitor(grace_sec=3.0)

    assert monitor.check("manual", "auto", 10.0) is None
    assert monitor.check("manual", "auto", 12.9) is None
    assert monitor.check("manual", "manual", 13.0) is None


def test_persistent_mismatch_and_recovery_are_each_reported_once():
    monitor = ModeAlignmentMonitor(grace_sec=3.0)

    assert monitor.check("manual", None, 10.0) is None
    assert monitor.check("manual", None, 13.0) == "mismatch"
    assert monitor.check("manual", None, 20.0) is None
    assert monitor.check("manual", "manual", 21.0) == "recovered"
    assert monitor.check("manual", "manual", 22.0) is None


def test_new_mismatch_can_be_reported_after_recovery():
    monitor = ModeAlignmentMonitor(grace_sec=1.0)

    monitor.check("auto", "manual", 1.0)
    assert monitor.check("auto", "manual", 2.0) == "mismatch"
    assert monitor.check("auto", "auto", 3.0) == "recovered"
    monitor.check("manual", "auto", 4.0)
    assert monitor.check("manual", "auto", 5.0) == "mismatch"


def test_recovery_path_payload_has_a_separate_message_type():
    path = Path()
    first = PoseStamped()
    first.pose.position.x = 1.2345
    first.pose.position.y = -0.4567
    second = PoseStamped()
    second.pose.position.x = 2.0
    second.pose.position.y = 3.0
    path.poses = [first, second]

    payload = TeleopBridge._path_payload(path, "recovery_plan")

    assert payload == {
        "type": "recovery_plan",
        "points": [[1.234, -0.457], [2.0, 3.0]],
    }


def test_low_obstacle_observation_is_forwarded_as_realtime_layer():
    payload = parse_low_obstacle_observation(
        '{"active":true,"distance_m":0.1844,"fov_rad":0.26,'
        '"state":"CONFIRMED"}')

    assert payload == {
        "type": "low_obstacle",
        "active": True,
        "distance_m": 0.184,
        "fov_rad": 0.26,
        "state": "CONFIRMED",
    }


def test_cleared_low_obstacle_does_not_keep_old_distance():
    payload = parse_low_obstacle_observation(
        '{"active":false,"distance_m":0.18,"fov_rad":0.26,'
        '"state":"CLEAR"}')

    assert payload == {
        "type": "low_obstacle",
        "active": False,
        "distance_m": None,
        "fov_rad": None,
        "state": "CLEAR",
    }
