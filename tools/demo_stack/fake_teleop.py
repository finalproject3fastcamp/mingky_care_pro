#!/usr/bin/env python3
"""회수된 로봇의 teleop 브리지 자리에 서는 가상 주행체.

## 왜 이것이 없으면 지도가 빈 채로 있나

HospitalMap3D 는 `h.robot.visible = !!pose` 다. 그리고 pose 는 오직 teleop
소켓으로만 온다(useTeleopSocket.ts). 실기가 있을 때 그 소켓의 로봇 쪽은
로봇이 붙였다.

    [대시보드] --ws--> /robots/{id}/teleop/operator ─┐
    [로봇]     --ws--> /robots/{id}/teleop/robot   ─┘ ← 여기가 비었다

로봇이 없으면 서버는 조작자에게 `robot_connected: false` 를 주고, 3D 맵에는
**로봇이 아예 안 그려진다.** 이벤트 타임라인이 아무리 흘러도 지도는 빈 병원이다.
이 프로그램이 그 빈 자리에 붙어 pose·라이다·파티클·경로를 올린다.

## fake_robot.py 와 무엇이 다른가

fake_robot 은 HTTP 쪽(heartbeat·이벤트·세션)을 흉내낸다. 이쪽은 WebSocket
쪽(실시간 좌표·진단 레이어·주행 모드)이다. 둘은 서로를 모르고, 같이 떠야
대시보드가 온전해진다. 나누어 둔 것은 실기가 돌아왔을 때 **이쪽만 끄면**
되기 때문이다 — 진짜 로봇이 붙는 순간 서버가 옛 소켓을 닫는다
(routers/teleop.py 의 robot_socket).

## 지도와 타임라인이 어긋나지 않는다

로봇을 정해진 순서표대로 돌리지 않는다. `GET /sessions/active` 의
`current_visit` 를 따라간다 — 타임라인이 'X-ray 도착' 을 찍으면 지도의 로봇도
X-ray 실로 간다. 시나리오 파일을 읽는 대신 **서버가 아는 현재 단계**를 보므로,
시나리오를 바꾸든 나중에 진짜 세션이 생기든 그대로 맞는다 (SessionFollower).

## 조작 패드가 실제로 먹는다

조작자가 보내는 cmd_vel 을 받아 가상 주행체에 적분한다. 손을 떼면
MANUAL_HOLD_SEC 뒤에 순찰로 돌아간다. 눌렀는데 아무 일도 안 일어나는 화면은
데모에서 가장 나쁜 종류의 침묵이다.

## 좌표계

전부 **지도 좌표(m)** 다. 모델 좌표로 옮기는 일은 프론트엔드의 mapFrame.ts 가
한다 — 여기서 미리 돌려 보내면 두 번 돈다.

  - pose       {x, y, yaw}          지도 좌표·라디안
  - scan       [[각도, 거리], ...]   로봇 기준 극좌표 (화면이 pose 로 회전시킨다)
  - particles  [[x, y], ...]        지도 좌표
  - plan       [[x, y], ...]        지도 좌표

## 사용법

    pip install -r tools/demo_stack/requirements.txt
    python3 tools/demo_stack/fake_teleop.py --base-url https://mingkycarepro.site/api
"""

from __future__ import annotations

import argparse
import asyncio
import heapq
import json
import math
import random
import sys
import time
import urllib.request
import uuid
import zlib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import websockets
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WAYPOINTS_FILE = (REPO_ROOT / "mingky_ros" / "mingky_bringup" / "config"
                  / "waypoints" / "yun_map_highres_clean_waypoints.yaml")
# waypoint 좌표가 종속된 바로 그 지도다. 둘은 같이 움직여야 한다
# (waypoints 파일 머리말: "맵을 교체하면 전부 다시 측정해야 한다").
MAP_FILE = (REPO_ROOT / "mingky_ros" / "mingky_bringup" / "map"
            / "yun_map_highres_clean.yaml")

# 타임라인에서 가짜가 섞여 들어온 것을 알아볼 수 있어야 조사가 된다.
# fake_robot.py 가 같은 이유로 source_node 를 고정한다.
SOURCE_NODE = "fake_teleop"

# 안내가 없을 때 서 있는 자리. 실기도 세션이 끝나면 충전소로 복귀한다.
HOME = {
    "pinky-01": "charging_station_1",
    "pinky-02": "charging_station_2",
}

# 실기 주행 속도. 더 올리면 지도 위에서 순간이동처럼 보이고, 더 내리면
# 시나리오가 다음 단계로 넘어갈 때까지 로봇이 못 따라간다.
CRUISE_MPS = 0.28
TURN_RPS = 1.4
# 조작 명령이 끊긴 뒤 자동으로 돌아가기까지. 실기의 twist_mux timeout 과 같은 뜻.
MANUAL_HOLD_SEC = 1.0

# 라이다 빔 수와 사거리. 실기(LD19)는 더 촘촘하지만 화면에서 구분이 안 되는
# 만큼을 레이캐스트에 태울 이유가 없다. 사거리는 이 지도(4.8 x 3.7 m)에 맞췄다.
SCAN_BEAMS = 180
SCAN_RANGE_M = 3.0

POSE_HZ = 10.0
SCAN_HZ = 5.0
PARTICLE_HZ = 2.0
PLAN_HZ = 1.0
# MODE_STATUS_STALE_MS 가 4000 이다(useTeleopSocket.ts). 그보다 촘촘해야
# 화면의 적용 모드가 깜빡이지 않는다.
MODE_HZ = 1.0

ORDER_POLL_SEC = 2.0
# 안내 진행을 따라가는 주기. 시나리오가 한 단계에 몇 초를 쓰므로 이보다
# 촘촘할 이유가 없다.
SESSION_POLL_SEC = 2.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_waypoints():
    """waypoint 좌표와 `검사 이름 → waypoint` 매핑을 함께 읽는다.

    두 번째가 이 프로그램의 핵심이다. 세션의 `current_visit` 는 'X-ray' 같은
    표시 이름이고 지도는 `xray_room_goal` 을 안다. 그 사이를 잇는 정본이
    waypoint 파일의 `visit_waypoints` 다 — 여기서 이름을 추측하면 검사실
    표시 이름이 바뀌는 순간 로봇이 엉뚱한 데로 간다.
    """
    with WAYPOINTS_FILE.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    points = {}
    for name, value in (raw.get("waypoints") or {}).items():
        points[name] = (float(value["x"]), float(value["y"]),
                        float(value.get("yaw", 0.0)))

    visits = {}
    for visit, mapping in (raw.get("visit_waypoints") or {}).items():
        goal = (mapping or {}).get("goal")
        if goal in points:
            visits[visit] = goal
    return points, visits


def wrap(angle: float) -> float:
    """각도를 -π~π 로 접는다. 안 접으면 yaw 가 무한정 자라 화면이 헛돈다."""
    return (angle + math.pi) % (2 * math.pi) - math.pi


class OccupancyGrid:
    """Nav2 가 쓰는 것과 같은 점유격자.

    ## 왜 필요한가

    처음에는 목적지까지 직선으로 갔다. 지도에서 **로봇이 벽을 뚫고 지나갔다.**
    검사실은 저마다 방 안에 있어서 두 waypoint 를 잇는 직선은 거의 항상 벽을
    가로지른다. 실기가 그렇게 안 움직이는 이유는 Nav2 가 경로를 계획하기
    때문이고, 여기서도 같은 지도에 같은 계획을 해야 같은 그림이 나온다.

    맵 파일은 192x147 · 2.5 cm/px 로 작아서 A* 가 눈에 띄는 비용이 아니다.

    ## 라이다도 여기서 나온다

    벽을 아는 김에 광선도 쏜다. 예전에는 복도 폭쯤 되는 거리에 잡음만 얹었는데,
    그러면 로봇이 방 안에 있어도 라이다가 허공을 그린다. 같은 격자에 쏘면
    점들이 실제 벽 위에 얹힌다.
    """

    # ROS map_server 규약(trinary): 픽셀이 밝을수록 비어 있다.
    #   occupancy = (255 - pixel) / 255
    # occupied_thresh 를 넘으면 점유, free_thresh 아래면 자유, 사이는 미지다.

    def __init__(self, blocked, width, height, resolution, origin_x, origin_y):
        self.blocked = blocked          # bytearray, 1 이면 못 지나간다
        self.width = width
        self.height = height
        self.resolution = resolution
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.clearance = self._clearance()

    @classmethod
    def load(cls, yaml_path: Path) -> "OccupancyGrid":
        with yaml_path.open(encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}

        image = yaml_path.parent / meta["image"]
        resolution = float(meta["resolution"])
        origin = meta.get("origin") or [0.0, 0.0, 0.0]
        free_thresh = float(meta.get("free_thresh", 0.196))
        negate = int(meta.get("negate", 0))

        width, height, pixels = _read_pgm(image)

        # **확실히 빈 칸만 지나간다.** 점유와 미지를 똑같이 막는 것은 지도 밖으로
        # 걸어 나가는 것이 벽을 뚫는 것보다 나을 게 없어서다. 이 지도는 병원
        # 내부만 훑어 두었으므로 미지 = 아직 아무도 안 가 본 곳이다.
        blocked = bytearray(width * height)
        for i, pixel in enumerate(pixels):
            value = pixel if negate else (255 - pixel)
            blocked[i] = 0 if (value / 255.0) < free_thresh else 1

        return cls(blocked, width, height, resolution,
                   float(origin[0]), float(origin[1]))

    # --- 좌표 변환 ---

    def to_cell(self, x: float, y: float):
        col = int((x - self.origin_x) / self.resolution)
        # 이미지의 0 행이 지도의 위쪽(최대 y)이다. 뒤집지 않으면 경로가
        # 남북으로 대칭이 되어 엉뚱한 벽을 피한다.
        row = self.height - 1 - int((y - self.origin_y) / self.resolution)
        return col, row

    def to_point(self, col: int, row: int):
        x = self.origin_x + (col + 0.5) * self.resolution
        y = self.origin_y + (self.height - 1 - row + 0.5) * self.resolution
        return x, y

    def inside(self, col: int, row: int) -> bool:
        return 0 <= col < self.width and 0 <= row < self.height

    def is_blocked(self, col: int, row: int) -> bool:
        if not self.inside(col, row):
            return True
        return bool(self.blocked[row * self.width + col])

    # --- 여유 거리 ---

    def _clearance(self):
        """칸마다 가장 가까운 벽까지의 거리(칸 수). 한 번만 계산한다.

        경로를 벽에 딱 붙여 놓으면 화면에서 로봇이 벽을 스치며 간다. 이 값을
        비용에 얹어 복도 가운데로 밀어 준다. 벽을 아예 막는 것과 달리, 좁은
        곳에서는 붙어서라도 지나갈 수 있다 — waypoint 자체가 벽에서 0~2 cm 에
        찍혀 있어서(waypoints 파일 기준) 세게 막으면 목적지에 못 간다.
        """
        far = 255
        dist = bytearray([far]) * (self.width * self.height)
        queue = deque()
        for index, value in enumerate(self.blocked):
            if value:
                dist[index] = 0
                queue.append(index)

        while queue:
            index = queue.popleft()
            row, col = divmod(index, self.width)
            step = dist[index] + 1
            if step >= far:
                continue
            for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nc, nr = col + dc, row + dr
                if not self.inside(nc, nr):
                    continue
                nindex = nr * self.width + nc
                if dist[nindex] > step:
                    dist[nindex] = step
                    queue.append(nindex)
        return dist

    def nearest_free(self, col: int, row: int):
        """막힌 칸이면 가장 가까운 빈 칸으로 옮긴다.

        waypoint 가 벽에 바짝 붙어 찍혀 있어 반올림 한 칸 차이로 점유 칸에
        떨어지는 일이 있다. 그때 계획을 포기하면 그 목적지는 영영 못 간다.
        """
        if not self.is_blocked(col, row):
            return col, row
        for radius in range(1, 12):
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    if max(abs(dr), abs(dc)) != radius:
                        continue
                    nc, nr = col + dc, row + dr
                    if not self.is_blocked(nc, nr):
                        return nc, nr
        return None

    # --- 계획 ---

    def plan(self, start, goal):
        """A* 로 경로를 낸다. 지도 좌표 목록을 돌려준다.

        못 찾으면 빈 목록이다. 부르는 쪽은 그때 직선으로 떨어지지 않고
        **가만히 있어야 한다** — 못 가는 곳으로 벽을 뚫고 가느니 서 있는 편이
        낫다.
        """
        start_cell = self.nearest_free(*self.to_cell(*start))
        goal_cell = self.nearest_free(*self.to_cell(*goal))
        if start_cell is None or goal_cell is None:
            return []
        if start_cell == goal_cell:
            return [goal]

        width = self.width
        start_index = start_cell[1] * width + start_cell[0]
        goal_index = goal_cell[1] * width + goal_cell[0]

        def heuristic(index):
            row, col = divmod(index, width)
            return math.hypot(col - goal_cell[0], row - goal_cell[1])

        open_heap = [(heuristic(start_index), 0.0, start_index)]
        came_from = {}
        best = {start_index: 0.0}
        neighbours = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                      (1, 1, 1.4142), (1, -1, 1.4142),
                      (-1, 1, 1.4142), (-1, -1, 1.4142))

        while open_heap:
            _, cost, index = heapq.heappop(open_heap)
            if index == goal_index:
                return self._rebuild(came_from, index, goal)
            if cost > best.get(index, float("inf")):
                continue

            row, col = divmod(index, width)
            for dc, dr, step in neighbours:
                nc, nr = col + dc, row + dr
                if self.is_blocked(nc, nr):
                    continue
                # 대각선으로 벽 모서리를 자르지 않는다. 안 막으면 경로가
                # 문틀을 스쳐 지나가고 화면에서 벽을 통과한 것처럼 보인다.
                if dc and dr and (self.is_blocked(col + dc, row)
                                  or self.is_blocked(col, row + dr)):
                    continue
                nindex = nr * width + nc
                # 벽에 가까울수록 비싸게 매긴다. 3 칸(7.5 cm) 안쪽만 본다.
                near = max(0, 3 - self.clearance[nindex])
                new_cost = cost + step + near * 0.6
                if new_cost < best.get(nindex, float("inf")):
                    best[nindex] = new_cost
                    came_from[nindex] = index
                    heapq.heappush(
                        open_heap, (new_cost + heuristic(nindex), new_cost, nindex))
        return []

    def _rebuild(self, came_from, index, goal):
        cells = [index]
        while index in came_from:
            index = came_from[index]
            cells.append(index)
        cells.reverse()

        points = []
        for cell in cells:
            row, col = divmod(cell, self.width)
            points.append(self.to_point(col, row))
        # 마지막은 격자 가운데가 아니라 진짜 목적지여야 한다.
        points[-1] = (goal[0], goal[1])
        return self._smooth(points)

    def _smooth(self, points):
        """보이는 데까지 건너뛴다. 격자 경로 그대로면 계단처럼 꺾인다."""
        if len(points) <= 2:
            return points
        smoothed = [points[0]]
        anchor = 0
        for i in range(2, len(points)):
            if not self.line_of_sight(points[anchor], points[i]):
                smoothed.append(points[i - 1])
                anchor = i - 1
        smoothed.append(points[-1])
        return smoothed

    def line_of_sight(self, a, b) -> bool:
        """두 점 사이가 트여 있나. 격자를 촘촘히 훑어 본다."""
        distance = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = int(distance / (self.resolution * 0.5)) + 1
        for i in range(steps + 1):
            t = i / steps
            col, row = self.to_cell(a[0] + (b[0] - a[0]) * t,
                                    a[1] + (b[1] - a[1]) * t)
            if self.is_blocked(col, row):
                return False
        return True

    # --- 라이다 ---

    def raycast(self, x: float, y: float, angle: float, max_range: float) -> float:
        """벽에 닿을 때까지의 거리. 못 닿으면 max_range."""
        step = self.resolution
        steps = int(max_range / step)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        for i in range(1, steps + 1):
            distance = i * step
            col, row = self.to_cell(x + cos_a * distance, y + sin_a * distance)
            if self.is_blocked(col, row):
                return distance
        return max_range


def _read_pgm(path: Path):
    """P5(binary) PGM 을 읽는다. 헤더에 주석이 섞여 있을 수 있다.

    Pillow 를 쓰지 않는 것은 mock_camera.py 와 같은 이유다 — 이 한 파일을
    읽으려고 의존성을 늘리지 않는다. P5 는 토큰 네 개와 바이트 배열이 전부다.
    """
    data = path.read_bytes()
    if not data.startswith(b"P5"):
        raise ValueError(f"P5 PGM 이 아니다: {path}")

    tokens = []
    index = 2
    while len(tokens) < 3:
        while index < len(data) and data[index:index + 1].isspace():
            index += 1
        if data[index:index + 1] == b"#":
            while index < len(data) and data[index] != 0x0A:
                index += 1
            continue
        start = index
        while index < len(data) and not data[index:index + 1].isspace():
            index += 1
        tokens.append(int(data[start:index]))

    width, height, maxval = tokens
    if maxval > 255:
        raise ValueError(f"16비트 PGM 은 다루지 않는다: {path}")
    index += 1                      # 헤더 뒤 공백 한 칸
    return width, height, data[index:index + width * height]


class VirtualRobot:
    """지도 위를 도는 가상 주행체."""

    def __init__(self, robot_id: str, home, grid: "OccupancyGrid"):
        self.robot_id = robot_id
        self.home = home
        self.grid = grid
        # 현재 목적지. SessionFollower 가 안내 진행에 맞춰 갈아끼운다.
        self.goal = home
        self.goal_name = "충전소"
        # 목적지까지의 계획 경로(지도 좌표)와 지금 향하는 지점.
        self.path = []
        self.path_index = 0
        self.x, self.y, self.yaw = home
        self.mode = "auto"
        # 조작자가 마지막으로 보낸 속도와 그 시각.
        self.manual = (0.0, 0.0)
        self.manual_at = 0.0
        # 로봇마다 다른 잡음을 준다. 두 대의 라이다가 같은 무늬로 떨면 화면에서
        # 바로 티가 난다.
        #
        # 내장 hash() 를 쓰지 않는 것은 문자열 해시가 PYTHONHASHSEED 로 매
        # 실행마다 달라지기 때문이다. crc32 는 어디서 돌리든 같은 값이라,
        # 이상하게 보이는 화면을 나중에 다시 만들어 볼 수 있다.
        self._rng = random.Random(zlib.crc32(robot_id.encode()))

    @property
    def target(self):
        """지금 향하는 지점. 최종 목적지가 아니라 경로의 다음 마디다."""
        if self.path_index < len(self.path):
            point = self.path[self.path_index]
            return point[0], point[1], self.goal[2]
        return self.goal

    def set_goal(self, point, name: str) -> None:
        if self.goal == point:
            return
        self.goal = point
        self.goal_name = name
        self.replan()
        print(f"[{self.robot_id}] 목적지 → {name} "
              f"(경로 {len(self.path)}마디)", flush=True)

    def replan(self) -> None:
        """지금 자리에서 목적지까지 다시 계획한다.

        직선으로 떨어지지 않는다. 계획이 안 나오면 빈 경로로 두고 서 있는다 —
        못 가는 곳으로 벽을 뚫고 가느니 안 움직이는 편이 낫고, 화면에서도 그게
        '길이 없다' 로 읽힌다.
        """
        self.path = self.grid.plan((self.x, self.y), (self.goal[0], self.goal[1]))
        self.path_index = 0
        if not self.path:
            print(f"[{self.robot_id}] 경로를 못 찾았다 — 제자리에서 대기",
                  file=sys.stderr, flush=True)

    def step(self, dt: float, now: float) -> None:
        if self.mode == "estop":
            return

        # --- 수동 조작이 살아 있으면 그쪽이 이긴다 ---
        if now - self.manual_at < MANUAL_HOLD_SEC:
            linear, angular = self.manual
            self.yaw = wrap(self.yaw + angular * dt)
            nx = self.x + linear * math.cos(self.yaw) * dt
            ny = self.y + linear * math.sin(self.yaw) * dt
            # 사람이 몰아도 벽은 벽이다. 실기라면 여기서 범퍼가 서고 twist_mux
            # 워치독이 모터를 끊는다.
            if not self.grid.is_blocked(*self.grid.to_cell(nx, ny)):
                self.x, self.y = nx, ny
            # 손으로 끌고 다닌 뒤에는 예전 경로가 무의미하다. 놓는 순간 다시
            # 계획하도록 비워 둔다.
            self.path = []
            self.path_index = 0
            return

        if self.mode == "manual":
            # 수동 모드인데 아무도 안 누르고 있으면 서 있는 것이 맞다.
            return

        # 경로가 없으면(목적지 도착 직후·수동 조작 직후) 다시 계획한다.
        if not self.path and math.hypot(self.goal[0] - self.x,
                                        self.goal[1] - self.y) > 0.08:
            self.replan()

        tx, ty, tyaw = self.target
        dx, dy = tx - self.x, ty - self.y
        distance = math.hypot(dx, dy)

        if distance < 0.05:
            if self.path_index < len(self.path) - 1:
                # 경로의 다음 마디로. 아직 목적지가 아니다.
                self.path_index += 1
                return
            # 도착. 목적지가 정해 둔 방향으로 돌아서서 기다린다. 다음 목적지는
            # 순서표가 아니라 **안내 진행**이 준다 (SessionFollower).
            self.yaw = tyaw
            self.path = []
            self.path_index = 0
            return

        # 먼저 돌고 나서 간다. 실기의 Nav2 도 큰 각도차에서는 제자리 회전을 한다.
        heading = math.atan2(dy, dx)
        error = wrap(heading - self.yaw)
        if abs(error) > 0.25:
            self.yaw = wrap(self.yaw + math.copysign(
                min(TURN_RPS * dt, abs(error)), error))
            return

        self.yaw = wrap(self.yaw + max(-TURN_RPS * dt,
                                       min(TURN_RPS * dt, error)))
        travel = min(CRUISE_MPS * dt, distance)
        self.x += travel * math.cos(self.yaw)
        self.y += travel * math.sin(self.yaw)

    # --- 진단 레이어 ---

    def scan(self) -> list:
        """라이다 한 바퀴. 로봇 기준 극좌표 [각도, 거리] 다.

        지도에 실제로 광선을 쏜다. 예전에는 복도 폭쯤 되는 거리에 잡음만
        얹었는데, 그러면 로봇이 방 안에 있어도 점들이 허공에 뜬다. 같은
        격자를 쓰니 라이다가 벽 위에 얹히고, 경로·파티클과도 아귀가 맞는다.

        빔은 180 개다. 실기(LD19)는 한 바퀴에 그보다 촘촘하지만, 화면에서
        구분이 안 되는 만큼을 CPU 로 태울 이유가 없다.
        """
        points = []
        for i in range(SCAN_BEAMS):
            angle = wrap(2 * math.pi * i / SCAN_BEAMS)
            distance = self.grid.raycast(
                self.x, self.y, wrap(self.yaw + angle), SCAN_RANGE_M)
            if distance >= SCAN_RANGE_M:
                # 안 맞은 빔은 버린다. 최대 거리에 점을 찍으면 화면에 있지도
                # 않은 원형 벽이 생긴다.
                continue
            # 실기 라이다의 흔들림. 이게 없으면 선이 자로 그은 듯해 가짜로 보인다.
            noise = self._rng.uniform(-0.015, 0.015)
            points.append([round(angle, 4), round(max(0.05, distance + noise), 3)])
        return points

    def particles(self, count: int = 220) -> list:
        """AMCL 파티클 구름. 수렴한 상태를 흉내내 pose 주위로 좁게 편다."""
        spread = 0.06
        return [[round(self.x + self._rng.gauss(0, spread), 4),
                 round(self.y + self._rng.gauss(0, spread), 4)]
                for _ in range(count)]

    def plan(self) -> list:
        """전역 경로 — 지금 자리에서 목적지까지 **실제로 갈 길**.

        A* 가 낸 경로의 남은 부분을 그대로 올린다. 예전에는 목적지까지 직선을
        그렸는데, 그 파란 선도 로봇과 같이 벽을 뚫고 지나갔다.
        """
        if not self.path:
            return []
        remaining = self.path[self.path_index:]
        return [[round(self.x, 4), round(self.y, 4)]] + [
            [round(px, 4), round(py, 4)] for px, py in remaining]


class SessionFollower:
    """지도의 로봇을 **타임라인이 말하는 곳**으로 보낸다.

    이것이 없으면 화면이 갈라진다. 이벤트 목록은 'X-ray 도착' 을 찍는데 지도의
    로봇은 자기 순서표대로 엉뚱한 데를 돌고, 두 개를 같이 보는 사람은 바로
    어긋난 것을 알아챈다. 데모에서 제일 값싸게 들통나는 부분이다.

    ## 어디서 진실을 읽나

    `GET /sessions/active` 의 `current_visit` 다. 시나리오 파일을 읽지 않는
    것이 요점이다 — 시나리오를 바꾸든, 나중에 진짜 로봇이 세션을 만들든,
    **서버가 아는 현재 단계**를 따라가면 항상 맞는다.

    이름을 waypoint 로 옮기는 것은 waypoint 정본의 `visit_waypoints` 가 한다.

    ## 안내가 없으면 충전소

    실기도 세션이 끝나면 복귀한다. 시나리오 회차 사이에 로봇이 충전소로
    돌아가는 것이 그래서 맞는 그림이고, 다음 회차가 시작하면 다시 나간다.
    """

    def __init__(self, base_url: str, robots: dict, points: dict, visits: dict):
        self.base_url = base_url.rstrip("/")
        self.robots = robots
        self.points = points
        self.visits = visits
        self._warned = set()

    def poll_once(self) -> None:
        req = urllib.request.Request(f"{self.base_url}/sessions/active")
        with urllib.request.urlopen(req, timeout=5) as response:
            sessions = json.loads(response.read() or b"[]")

        guided = {}
        for session in sessions:
            robot_id = session.get("robot_id")
            visit = session.get("current_visit")
            if robot_id in self.robots and visit:
                guided[robot_id] = visit

        for robot_id, robot in self.robots.items():
            visit = guided.get(robot_id)
            if visit is None:
                robot.set_goal(robot.home, "충전소")
                continue

            name = self.visits.get(visit)
            if name is None:
                # 매핑에 없는 검사 이름. 로봇은 그냥 하던 곳에 둔다 —
                # 추측해서 엉뚱한 방으로 보내는 것보다 안 움직이는 게 낫다.
                if visit not in self._warned:
                    self._warned.add(visit)
                    print(f"[{robot_id}] visit_waypoints 에 없는 검사: {visit}",
                          file=sys.stderr, flush=True)
                continue
            robot.set_goal(self.points[name], visit)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                await loop.run_in_executor(None, self.poll_once)
            except Exception as exc:
                # 목적지는 그대로 둔다. 폴링이 한 번 흔들렸다고 로봇을 충전소로
                # 되돌리면 안내 중인 화면이 이유 없이 튄다.
                print(f"[세션 확인 실패] {exc}", flush=True)
            await asyncio.sleep(SESSION_POLL_SEC)


class OrderPoller:
    """관제가 내린 명령을 가져와 반영한다. 모드 버튼이 실제로 먹게 하는 부분."""

    def __init__(self, base_url: str, robot: VirtualRobot):
        self.base_url = base_url.rstrip("/")
        self.robot = robot

    def _request(self, method: str, path: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            body = response.read()
            return json.loads(body) if body else None

    def _emit_mode_changed(self, mode: str, previous: str) -> None:
        self._request("POST", "/events", [{
            "event_id": str(uuid.uuid4()),
            "robot_id": self.robot.robot_id,
            "session_id": 0,
            "occurred_at": now_iso(),
            "level": "info",
            "event_code": "robot.mode_changed",
            "source_node": SOURCE_NODE,
            "payload": {"mode": mode, "previous": previous,
                        "source": "fake_teleop"},
        }])

    def poll_once(self) -> None:
        order = self._request(
            "GET", f"/robots/{self.robot.robot_id}/orders/next")
        if not order:
            return

        command = order.get("command")
        argument = order.get("argument", "")

        if command == "set_mode" and argument in ("auto", "manual", "estop"):
            previous = self.robot.mode
            self.robot.mode = argument
            print(f"[{self.robot.robot_id}] 모드 {previous} → {argument}",
                  flush=True)
            # 정본이 요구하는 이벤트를 남긴다. 대시보드의 '주행 모드' 표시는
            # 이 이벤트를 읽는다(useRobotMode.ts).
            if previous != argument:
                self._emit_mode_changed(argument, previous)

        # ack 는 명령을 알아들었든 아니든 보낸다. 안 보내면 같은 명령이 큐에
        # 남아 영원히 다시 온다(routers/orders.py).
        self._request(
            "POST",
            f"/robots/{self.robot.robot_id}/orders/{order['order_id']}/ack",
            {"order_id": order["order_id"]})

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                await loop.run_in_executor(None, self.poll_once)
            except Exception as exc:
                print(f"[{self.robot.robot_id}] 명령 확인 실패: {exc}", flush=True)
            await asyncio.sleep(ORDER_POLL_SEC)


async def pump(ws, robot: VirtualRobot) -> None:
    """가상 주행체를 굴리며 레이어를 올린다."""
    last = time.monotonic()
    next_at = {"scan": 0.0, "particles": 0.0, "plan": 0.0, "mode": 0.0}
    period = {"scan": 1 / SCAN_HZ, "particles": 1 / PARTICLE_HZ,
              "plan": 1 / PLAN_HZ, "mode": 1 / MODE_HZ}

    while True:
        now = time.monotonic()
        robot.step(now - last, now)
        last = now

        await ws.send(json.dumps({
            "type": "pose", "x": round(robot.x, 4),
            "y": round(robot.y, 4), "yaw": round(robot.yaw, 4)}))

        if now >= next_at["scan"]:
            next_at["scan"] = now + period["scan"]
            await ws.send(json.dumps({"type": "scan", "points": robot.scan()}))
        if now >= next_at["particles"]:
            next_at["particles"] = now + period["particles"]
            await ws.send(json.dumps(
                {"type": "particles", "points": robot.particles()}))
        if now >= next_at["plan"]:
            next_at["plan"] = now + period["plan"]
            await ws.send(json.dumps({"type": "plan", "points": robot.plan()}))
        if now >= next_at["mode"]:
            next_at["mode"] = now + period["mode"]
            # fresh 가 false 면 화면이 '적용을 확인하지 못함' 으로 그린다.
            await ws.send(json.dumps({
                "type": "mode_status", "applied_mode": robot.mode,
                "fresh": True}))

        await asyncio.sleep(1 / POSE_HZ)


async def listen(ws, robot: VirtualRobot) -> None:
    """조작자가 보내는 것을 받는다. cmd_vel 과 set_pose 두 가지다."""
    async for raw in ws:
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            continue

        kind = message.get("type")
        if kind == "cmd_vel":
            robot.manual = (float(message.get("linear", 0.0)),
                            float(message.get("angular", 0.0)))
            robot.manual_at = time.monotonic()
        elif kind == "set_pose":
            robot.x = float(message.get("x", robot.x))
            robot.y = float(message.get("y", robot.y))
            robot.yaw = float(message.get("yaw", robot.yaw))
            print(f"[{robot.robot_id}] 위치 재지정 "
                  f"({robot.x:.2f}, {robot.y:.2f})", flush=True)


async def bridge(base_url: str, robot: VirtualRobot) -> None:
    """소켓 하나를 붙들고, 끊기면 다시 붙는다."""
    ws_url = (base_url.replace("https://", "wss://").replace("http://", "ws://")
              .rstrip("/") + f"/robots/{robot.robot_id}/teleop/robot")

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20,
                                          ping_timeout=20) as ws:
                print(f"[{robot.robot_id}] teleop 연결 — {ws_url}", flush=True)
                await asyncio.gather(pump(ws, robot), listen(ws, robot))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[{robot.robot_id}] teleop 끊김: {exc} — 3초 뒤 재접속",
                  flush=True)
        # 실기와 같은 태도다. 회선이 흔들리는 것을 정상으로 보고 다시 건다.
        await asyncio.sleep(3)


async def run(base_url: str, robot_ids: list) -> None:
    points, visits = load_waypoints()
    if not visits:
        print("[경고] visit_waypoints 를 못 읽었다 — 로봇이 충전소에만 있는다",
              file=sys.stderr)

    grid = OccupancyGrid.load(MAP_FILE)
    free = sum(1 for value in grid.blocked if not value)
    print(f"[지도] {grid.width}x{grid.height} · {grid.resolution*100:g} cm/px · "
          f"통행 가능 {free}칸 ← {MAP_FILE.name}", flush=True)

    robots = {}
    tasks = []
    for robot_id in robot_ids:
        home = HOME.get(robot_id)
        if home is None:
            print(f"[건너뜀] {robot_id} 의 대기 자리가 HOME 에 없다",
                  file=sys.stderr)
            continue
        if home not in points:
            print(f"[건너뜀] {robot_id} — waypoint 없음: {home}",
                  file=sys.stderr)
            continue

        robot = VirtualRobot(robot_id, points[home], grid)
        robots[robot_id] = robot
        tasks.append(bridge(base_url, robot))
        tasks.append(OrderPoller(base_url, robot).run())

    if not tasks:
        print("[실패] 띄울 로봇이 없다", file=sys.stderr)
        return

    # 세션 폴링은 로봇마다가 아니라 한 번이다. /sessions/active 가 전부를
    # 돌려주므로 로봇 수만큼 부를 이유가 없다.
    tasks.append(SessionFollower(base_url, robots, points, visits).run())

    await asyncio.gather(*tasks)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="백엔드 주소. 배포에서는 https://…/api")
    parser.add_argument("--robots", default="pinky-01,pinky-02",
                        help="쉼표로 나눈 로봇 id")
    args = parser.parse_args(argv)

    robot_ids = [value.strip() for value in args.robots.split(",")
                 if value.strip()]
    try:
        asyncio.run(run(args.base_url, robot_ids))
    except KeyboardInterrupt:
        print("[가짜 teleop] 종료", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
