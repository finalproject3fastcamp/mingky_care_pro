#!/usr/bin/env python3
"""Nav2 실험의 핵심 상태와 실패 순간을 SQLite에 기록하는 개발용 노드."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import subprocess
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit(repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, timeout=3
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def yaw_from_quaternion(q: Any) -> float:
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def path_length(path: Any) -> float | None:
    poses = path.poses
    if len(poses) < 2:
        return 0.0 if poses else None
    return sum(
        math.hypot(
            poses[index].pose.position.x - poses[index - 1].pose.position.x,
            poses[index].pose.position.y - poses[index - 1].pose.position.y,
        )
        for index in range(1, len(poses))
    )


def scan_sectors(scan: Any) -> dict[str, float | None]:
    sectors = {"front": 0.0, "left": math.pi / 2, "right": -math.pi / 2, "rear": math.pi}
    values: dict[str, list[float]] = {name: [] for name in sectors}
    half_width = math.pi / 6
    for index, distance in enumerate(scan.ranges):
        if not math.isfinite(distance) or not (scan.range_min <= distance <= scan.range_max):
            continue
        angle = scan.angle_min + index * scan.angle_increment
        for name, center in sectors.items():
            delta = math.atan2(math.sin(angle - center), math.cos(angle - center))
            if abs(delta) <= half_width:
                values[name].append(distance)
    return {name: min(items) if items else None for name, items in values.items()}


def occupancy_snapshot(grid: Any) -> tuple[dict[str, Any], bytes]:
    info = grid.info
    metadata = {
        "frame_id": grid.header.frame_id,
        "width": info.width,
        "height": info.height,
        "resolution": info.resolution,
        "origin": {"x": info.origin.position.x, "y": info.origin.position.y},
    }
    payload = bytes((value & 0xFF) for value in grid.data)
    return metadata, zlib.compress(payload, level=6)


def path_snapshot(path: Any) -> tuple[dict[str, Any], bytes]:
    values = [
        {
            "x": pose.pose.position.x,
            "y": pose.pose.position.y,
            "yaw": yaw_from_quaternion(pose.pose.orientation),
        }
        for pose in path.poses
    ]
    metadata = {"frame_id": path.header.frame_id, "poses": len(values)}
    return metadata, zlib.compress(json.dumps(values, separators=(",", ":")).encode(), level=6)


class Recorder:
    def __init__(self, args: argparse.Namespace) -> None:
        import rclpy
        from action_msgs.msg import GoalStatusArray
        from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
        from nav_msgs.msg import OccupancyGrid, Odometry, Path as NavPath
        from rcl_interfaces.msg import Log
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, ReliabilityPolicy
        from sensor_msgs.msg import LaserScan

        self.rclpy = rclpy
        self.Node = Node
        self.args = args
        self.conn = initialise_database(args)
        self.node = Node("nav2_diagnostics_recorder")
        self.run_id = args.run_id
        self.latest: dict[str, Any] = {}
        self.last_action_status: int | None = None
        self.last_snapshot_time = 0.0
        self.known_goal: tuple[float, float, float] | None = None
        self.goal_row_id: int | None = None

        self.node.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self.on_pose, 10)
        self.node.create_subscription(Odometry, "/odom", self.on_odom, 20)
        self.node.create_subscription(Twist, "/cmd_vel", self.on_cmd_vel, 20)
        sensor_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.node.create_subscription(LaserScan, "/scan", self.on_scan, sensor_qos)
        self.node.create_subscription(NavPath, "/plan", self.on_global_plan, 10)
        self.node.create_subscription(NavPath, "/local_plan", self.on_local_plan, 10)
        self.node.create_subscription(OccupancyGrid, "/global_costmap/costmap", self.on_global_costmap, 1)
        self.node.create_subscription(OccupancyGrid, "/local_costmap/costmap", self.on_local_costmap, 1)
        self.node.create_subscription(PoseStamped, "/goal_pose", self.on_goal, 10)
        self.node.create_subscription(GoalStatusArray, "/navigate_to_pose/_action/status", self.on_action_status, 10)
        self.node.create_subscription(Log, "/rosout", self.on_rosout, 100)
        self.node.create_timer(1.0 / args.sample_hz, self.write_sample)
        # recorder는 Nav2보다 먼저 시작된다. controller가 실제로 뜬 뒤의 값을 start로 남긴다.
        self.start_snapshot_timer = self.node.create_timer(3.0, self.capture_start_parameters)
        self.event("info", "recorder", "started", "Nav2 diagnostics recorder started")

    def event(self, level: str, source: str, event_type: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events(run_id, recorded_at, level, source, event_type, message, details_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, now(), level, source, event_type, message, json.dumps(details or {}, ensure_ascii=False)),
        )
        self.conn.commit()

    def on_pose(self, message: Any) -> None:
        pose = message.pose.pose
        self.latest["pose"] = {
            "x": pose.position.x,
            "y": pose.position.y,
            "yaw": yaw_from_quaternion(pose.orientation),
            "covariance_xy": message.pose.covariance[0] + message.pose.covariance[7],
        }

    def on_odom(self, message: Any) -> None:
        self.latest["odom"] = {"linear_x": message.twist.twist.linear.x, "angular_z": message.twist.twist.angular.z}

    def on_cmd_vel(self, message: Any) -> None:
        self.latest["cmd"] = {"linear_x": message.linear.x, "angular_z": message.angular.z}

    def on_scan(self, message: Any) -> None:
        sectors = scan_sectors(message)
        finite = [value for value in message.ranges if math.isfinite(value) and message.range_min <= value <= message.range_max]
        sectors["min"] = min(finite) if finite else None
        self.latest["scan"] = sectors

    def on_global_plan(self, message: Any) -> None:
        self.latest["global_plan"] = message

    def on_local_plan(self, message: Any) -> None:
        self.latest["local_plan"] = message

    def on_global_costmap(self, message: Any) -> None:
        self.latest["global_costmap"] = message

    def on_local_costmap(self, message: Any) -> None:
        self.latest["local_costmap"] = message

    def on_goal(self, message: Any) -> None:
        pose = message.pose
        goal = (pose.position.x, pose.position.y, yaw_from_quaternion(pose.orientation))
        if goal == self.known_goal:
            return
        self.known_goal = goal
        cursor = self.conn.execute(
            "INSERT INTO goals(run_id, requested_at, x, y, yaw) VALUES (?, ?, ?, ?, ?)",
            (self.run_id, now(), *goal),
        )
        self.goal_row_id = cursor.lastrowid
        self.conn.commit()
        self.event("info", "goal_pose", "goal_requested", "RViz goal received", {"x": goal[0], "y": goal[1], "yaw": goal[2]})

    def on_action_status(self, message: Any) -> None:
        if not message.status_list:
            return
        status = message.status_list[-1].status
        if status == self.last_action_status:
            return
        self.last_action_status = status
        names = {0: "unknown", 1: "accepted", 2: "executing", 3: "canceling", 4: "succeeded", 5: "canceled", 6: "aborted"}
        result = names.get(status, f"status_{status}")
        self.event("info" if status < 5 else "warn", "navigate_to_pose", "action_status", result, {"status": status})
        if self.goal_row_id is not None and status in (4, 5, 6):
            self.conn.execute("UPDATE goals SET finished_at=?, result=? WHERE id=?", (now(), result, self.goal_row_id))
            self.conn.commit()
        if status in (5, 6):
            self.snapshot(f"action_{result}")

    def on_rosout(self, message: Any) -> None:
        if message.level < 30:
            return
        source = message.name or "rosout"
        level = "error" if message.level >= 40 else "warn"
        text = message.msg
        self.event(level, source, "rosout", text)
        lower = text.lower()
        if any(token in lower for token in ("recovery", "failed", "abort", "unable", "collision", "no valid", "transform")):
            self.snapshot(f"rosout_{source}")

    def write_sample(self) -> None:
        pose = self.latest.get("pose", {})
        odom = self.latest.get("odom", {})
        cmd = self.latest.get("cmd", {})
        scan = self.latest.get("scan", {})
        global_plan = self.latest.get("global_plan")
        local_plan = self.latest.get("local_plan")
        self.conn.execute(
            """INSERT INTO samples(
                run_id, recorded_at, pose_x, pose_y, pose_yaw, pose_covariance_xy,
                odom_linear_x, odom_angular_z, cmd_linear_x, cmd_angular_z,
                scan_min_range, scan_front_range, scan_left_range, scan_right_range, scan_rear_range,
                global_plan_length, local_plan_length
              ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.run_id, now(), pose.get("x"), pose.get("y"), pose.get("yaw"), pose.get("covariance_xy"),
                odom.get("linear_x"), odom.get("angular_z"), cmd.get("linear_x"), cmd.get("angular_z"),
                scan.get("min"), scan.get("front"), scan.get("left"), scan.get("right"), scan.get("rear"),
                path_length(global_plan) if global_plan else None, path_length(local_plan) if local_plan else None,
            ),
        )
        self.conn.commit()

    def snapshot(self, reason: str) -> None:
        if time.monotonic() - self.last_snapshot_time < 2.0:
            return
        self.last_snapshot_time = time.monotonic()
        saved = 0
        for name, converter in (("global_costmap", occupancy_snapshot), ("local_costmap", occupancy_snapshot), ("global_plan", path_snapshot), ("local_plan", path_snapshot)):
            message = self.latest.get(name)
            if message is None:
                continue
            metadata, payload = converter(message)
            self.conn.execute(
                "INSERT INTO snapshots(run_id, recorded_at, reason, snapshot_type, metadata_json, payload_zlib) VALUES (?, ?, ?, ?, ?, ?)",
                (self.run_id, now(), reason, name, json.dumps(metadata), payload),
            )
            saved += 1
        self.conn.commit()
        self.event("warn", "recorder", "snapshot", f"{reason}: {saved}개 상태 저장", {"count": saved})

    def capture_start_parameters(self) -> None:
        node_names = {name for name, _namespace in self.node.get_node_names_and_namespaces()}
        if "controller_server" not in node_names:
            return
        self.write_parameter_snapshot("start")
        self.start_snapshot_timer.cancel()

    def write_parameter_snapshot(self, phase: str) -> None:
        for node_name in ("/controller_server", "/planner_server", "/global_costmap/global_costmap", "/local_costmap/local_costmap", "/amcl"):
            try:
                output = subprocess.check_output(["ros2", "param", "dump", node_name], text=True, stderr=subprocess.DEVNULL, timeout=2)
                payload: dict[str, Any] = {"raw": output}
            except (OSError, subprocess.SubprocessError):
                payload = {"unavailable": True}
            self.conn.execute(
                "INSERT INTO parameter_snapshots(run_id, captured_at, phase, node_name, parameters_json) VALUES (?, ?, ?, ?, ?)",
                (self.run_id, now(), phase, node_name, json.dumps(payload, ensure_ascii=False)),
            )
        self.conn.commit()

    def close(self) -> None:
        self.event("info", "recorder", "stopped", "Nav2 diagnostics recorder stopped")
        self.write_parameter_snapshot("end")
        self.conn.execute("UPDATE runs SET finished_at=?, status='finished' WHERE id=?", (now(), self.run_id))
        self.conn.commit()
        self.node.destroy_node()
        self.conn.close()


def initialise_database(args: argparse.Namespace) -> sqlite3.Connection:
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    connection.executescript(schema)
    map_path = Path(args.map)
    profile_path = Path(args.profile) if args.profile else None
    effective_path = Path(args.effective_params)
    connection.execute(
        """INSERT INTO runs(
             id, started_at, label, robot_name, ros_domain_id, git_commit,
             map_path, map_sha256, profile_path, profile_sha256,
             effective_params_path, effective_params_sha256, effective_params_yaml, notes
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            args.run_id, now(), args.label, args.robot_name, args.ros_domain_id, git_commit(Path(args.repo)),
            str(map_path), sha256_file(map_path), str(profile_path) if profile_path else None, sha256_file(profile_path),
            str(effective_path), sha256_file(effective_path), effective_path.read_text(encoding="utf-8"), args.notes,
        ),
    )
    connection.commit()
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="기록할 SQLite 파일 경로")
    parser.add_argument("--run-id", required=True, help="실험 세션 ID")
    parser.add_argument("--repo", required=True, help="저장소 루트")
    parser.add_argument("--map", required=True, help="사용 지도 YAML")
    parser.add_argument("--profile", help="적용한 프로파일 YAML")
    parser.add_argument("--effective-params", required=True, help="합쳐진 Nav2 파라미터 YAML")
    parser.add_argument("--label", default="manual-nav2-test", help="실험 목적")
    parser.add_argument("--notes", default="", help="추가 메모")
    parser.add_argument("--robot-name", default="pinky_6294")
    parser.add_argument("--ros-domain-id", type=int, default=21)
    parser.add_argument("--sample-hz", type=float, default=5.0)
    args = parser.parse_args()
    if args.sample_hz <= 0 or args.sample_hz > 20:
        parser.error("--sample-hz는 0 초과 20 이하이어야 합니다.")
    if Path(args.db).exists():
        parser.error(f"이미 존재하는 DB입니다: {args.db}")

    import rclpy

    rclpy.init(args=None)
    recorder = Recorder(args)
    try:
        rclpy.spin(recorder.node)
    except KeyboardInterrupt:
        pass
    finally:
        recorder.close()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
