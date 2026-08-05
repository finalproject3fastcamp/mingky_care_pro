#!/usr/bin/env python3
"""waypoint 가 실제로 주행 가능한 위치인지 검사한다.

두 가지를 본다.

1. 벽까지 거리
   Nav2 는 로봇 내접 반경 + footprint_padding 안쪽 셀을 통과 불가로 본다.
   그보다 벽에 가까운 waypoint 는 planner 가 도달할 수 없고,
   NavFn 의 tolerance 때문에 '근처까지만' 경로를 그린 뒤 조용히 실패한다.

2. waypoint 사이 간격
   두 지점이 xy_goal_tolerance 지름보다 가까우면, 한쪽에 서 있는 상태에서
   다른 쪽으로 보내도 이미 도착 조건을 만족해 로봇이 움직이지 않는다.

맵을 다시 만들면 좌표계가 바뀌어 기존 waypoint 가 전부 무의미해진다.
맵을 교체할 때마다 이 스크립트를 돌려야 하는 이유다.

사용:
    ./check_waypoints.py
    ./check_waypoints.py --map ../map/archive/yun_map.yaml
    ./check_waypoints.py --tolerance 0.12 --footprint 0.06 --padding 0.03
"""

import argparse
import math
import sys
from itertools import combinations
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML 이 필요합니다: pip install pyyaml")


def read_pgm(path: Path):
    """P5 (binary) PGM 을 읽는다. GIMP 가 넣는 주석 줄도 건너뛴다."""
    data = path.read_bytes()
    if data[:2] != b"P5":
        raise ValueError(f"P5 PGM 이 아닙니다: {path}")
    fields, i = [], 2
    while len(fields) < 3:
        while data[i : i + 1].isspace():
            i += 1
        if data[i : i + 1] == b"#":
            while data[i : i + 1] != b"\n":
                i += 1
            continue
        j = i
        while not data[j : j + 1].isspace():
            j += 1
        fields.append(int(data[i:j]))
        i = j
    i += 1
    width, height, _ = fields
    return width, height, data[i : i + width * height]


def occupied_cells(map_yaml: Path):
    """맵에서 점유 셀의 월드 좌표 목록을 만든다."""
    meta = yaml.safe_load(map_yaml.read_text())
    width, height, pixels = read_pgm(map_yaml.parent / meta["image"])
    res = meta["resolution"]
    ox, oy = meta["origin"][0], meta["origin"][1]
    threshold = meta.get("occupied_thresh", 0.65)

    cells = []
    for row in range(height):
        base = row * width
        # 이미지 첫 행이 가장 큰 y 에 해당한다.
        y = oy + (height - 1 - row + 0.5) * res
        for col in range(width):
            if (255 - pixels[base + col]) / 255.0 > threshold:
                cells.append((ox + (col + 0.5) * res, y))
    extent = (ox, ox + width * res, oy, oy + height * res)
    return cells, meta, (width, height), extent


def find_default(*relative: str) -> Path | None:
    """설치된 패키지 share 를 먼저 보고, 없으면 소스 트리를 거슬러 올라간다.

    ros2 run 으로 실행하면 이 파일이 install/.../lib 에 있어서 소스 트리
    탐색만으로는 map/ 과 config/ 을 찾지 못한다.
    """
    try:
        from ament_index_python.packages import get_package_share_directory

        share = Path(get_package_share_directory("mingky_bringup"))
        for rel in relative:
            candidate = share / Path(rel).relative_to("mingky_ros/mingky_bringup") \
                if rel.startswith("mingky_ros/mingky_bringup/") else share / rel
            if candidate.is_file():
                return candidate
    except Exception:
        pass

    for parent in Path(__file__).resolve().parents:
        for rel in relative:
            candidate = parent / rel
            if candidate.is_file():
                return candidate
    return None


def current_pose(timeout: float = 5.0):
    """/amcl_pose 를 한 건 읽어 (x, y) 를 돌려준다. ROS 환경에서만 동작한다."""
    import rclpy
    from geometry_msgs.msg import PoseWithCovarianceStamped
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

    rclpy.init()
    node = Node("waypoint_probe")
    box = {}
    qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(
        PoseWithCovarianceStamped, "/amcl_pose",
        lambda m: box.setdefault("p", (m.pose.pose.position.x, m.pose.pose.position.y)),
        qos)
    deadline = node.get_clock().now().nanoseconds + int(timeout * 1e9)
    while "p" not in box and node.get_clock().now().nanoseconds < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
    node.destroy_node()
    rclpy.try_shutdown()
    return box.get("p")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", action="store_true",
                   help="현재 /amcl_pose 위치를 검사한다. 찍기 전에 확인용")
    p.add_argument("--at", nargs=2, type=float, metavar=("X", "Y"),
                   help="좌표를 직접 주고 검사한다")
    p.add_argument("--map", type=Path, help="맵 yaml 경로")
    p.add_argument("--waypoints", type=Path, help="waypoint yaml 경로")
    p.add_argument("--footprint", type=float, default=0.06,
                   help="로봇 내접 반경 [m] (기본 0.06 = 0.12x0.12 정사각)")
    p.add_argument("--padding", type=float, default=0.03,
                   help="costmap footprint_padding [m]")
    p.add_argument("--margin", type=float, default=0.15,
                   help="권장 최소 여유 [m]. 이 아래는 경고")
    p.add_argument("--tolerance", type=float, default=0.12,
                   help="xy_goal_tolerance [m]. 간격 검사에 쓴다")
    args = p.parse_args()

    map_yaml = args.map or find_default(
        "mingky_ros/mingky_bringup/map/yun_map_highres_clean.yaml",
        "mingky_ros/mingky_bringup/map/archive/yun_map.yaml",
    )
    wp_yaml = args.waypoints or find_default(
        "mingky_ros/mingky_bringup/config/waypoints/yun_map_highres_clean_waypoints.yaml")

    if map_yaml is None or wp_yaml is None:
        return print("맵 또는 waypoint 파일을 찾지 못했습니다. --map / --waypoints 로 지정하세요.") or 2

    cells, meta, (w, h), (x0, x1, y0, y1) = occupied_cells(map_yaml)
    waypoints = yaml.safe_load(wp_yaml.read_text())["waypoints"] or {}

    blocked = args.footprint + args.padding

    # --- 찍기 전 확인 모드 ---
    if args.probe or args.at:
        point = tuple(args.at) if args.at else current_pose()
        if point is None:
            print("/amcl_pose 를 받지 못했습니다. localization 이 떠 있는지,")
            print("2D Pose Estimate 로 초기 위치를 찍었는지 확인하세요.")
            return 2

        print(f"현재 위치  x={point[0]:.3f}  y={point[1]:.3f}")
        if not (x0 <= point[0] <= x1 and y0 <= point[1] <= y1):
            print("  ✗ 맵 밖입니다. 위치추정이 틀렸을 가능성이 큽니다.")
            return 1

        dist = min(math.dist(point, c) for c in cells)
        print(f"벽까지     {dist:.3f}m", end="  ")
        if dist < blocked:
            print(f"✗ 도달 불가 ({blocked:.2f}m 미만). 벽에서 더 떨어지세요.")
        elif dist < args.margin:
            print(f"△ 여유 부족 (권장 {args.margin:.2f}m). 조금 더 떨어지는 게 좋습니다.")
        else:
            print("○ 좋습니다.")

        if waypoints:
            near = sorted(
                (math.dist(point, (float(v["x"]), float(v["y"]))), k)
                for k, v in waypoints.items())
            gap, name = near[0]
            print(f"가장 가까운 기존 waypoint  {name}  {gap:.3f}m", end="  ")
            if gap < args.tolerance * 2:
                print(f"✗ 너무 가깝습니다 (판정 원 지름 {args.tolerance*2:.2f}m).")
                print("   여기서 찍으면 두 지점을 구분하지 못해 로봇이 안 움직입니다.")
            else:
                print("○")

        ok = dist >= args.margin and (
            not waypoints or near[0][0] >= args.tolerance * 2)
        print("\n" + ("여기서 찍어도 됩니다." if ok else "위치를 옮기세요."))
        return 0 if ok else 1

    print(f"맵        {map_yaml}")
    print(f"          {w}x{h}px  res={meta['resolution']}  "
          f"x[{x0:.3f},{x1:.3f}] y[{y0:.3f},{y1:.3f}]  점유 {len(cells)}셀")
    print(f"waypoint  {wp_yaml}  ({len(waypoints)}개)")
    print(f"기준      벽에서 {blocked:.3f}m 이내 = 도달 불가, "
          f"{args.margin:.3f}m 이내 = 경고\n")

    rows, n_blocked, n_outside, n_warn = [], 0, 0, 0
    for name, wp in waypoints.items():
        point = (float(wp["x"]), float(wp["y"]))
        inside = x0 <= point[0] <= x1 and y0 <= point[1] <= y1
        dist = min((math.dist(point, c) for c in cells), default=float("inf")) if inside else None
        rows.append((name, point, inside, dist))

    for name, _, inside, dist in sorted(rows, key=lambda r: (r[2], r[3] or 0)):
        if not inside:
            verdict, n_outside = "맵 밖", n_outside + 1
            shown = "   ---"
        elif dist < blocked:
            verdict, n_blocked = "도달 불가", n_blocked + 1
            shown = f"{dist:6.3f}m"
        elif dist < args.margin:
            verdict, n_warn = "여유 부족", n_warn + 1
            shown = f"{dist:6.3f}m"
        else:
            verdict = "OK"
            shown = f"{dist:6.3f}m"
        print(f"  {name:36s} {shown}  {verdict}")

    # --- 간격 검사 ---
    diameter = args.tolerance * 2
    close = []
    for (n1, p1, in1, _), (n2, p2, in2, _) in combinations(rows, 2):
        if not (in1 and in2):
            continue
        gap = math.dist(p1, p2)
        if gap < diameter:
            close.append((gap, n1, n2))

    print(f"\n간격 검사  xy_goal_tolerance={args.tolerance} → 판정 원 지름 {diameter:.2f}m")
    if close:
        print(f"  {len(close)}쌍이 겹칩니다. 한쪽에 서 있으면 다른 쪽으로 안 움직입니다.")
        for gap, n1, n2 in sorted(close)[:10]:
            print(f"    {gap:6.3f}m  {n1} ↔ {n2}")
        if len(close) > 10:
            print(f"    ... 외 {len(close) - 10}쌍")
    else:
        print("  겹치는 쌍 없음")

    print(f"\n요약  OK {len(rows) - n_blocked - n_outside - n_warn} / "
          f"여유 부족 {n_warn} / 도달 불가 {n_blocked} / 맵 밖 {n_outside} / "
          f"간격 겹침 {len(close)}쌍")

    # 재측정이 필요한 상태면 0 이 아닌 값을 돌려준다.
    return 1 if (n_blocked or n_outside) else 0


if __name__ == "__main__":
    sys.exit(main())
