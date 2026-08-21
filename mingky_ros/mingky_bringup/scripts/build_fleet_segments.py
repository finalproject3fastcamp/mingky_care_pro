#!/usr/bin/env python3
"""지도를 '두 대가 스쳐 지날 수 있는 곳' 과 '외길' 로 나눈다.

핑키 2대를 동시에 돌리려면 서로를 피하게 해야 하는데, **이 지도에서는
'만나면 양보' 가 성립하지 않는다.** 실측하면 이렇다.

    주행 공간의 중앙값 여유   0.125 m  (= 자유폭 0.25 m)
    로봇 두 대의 폭            0.24 m
    두 대가 스쳐 지날 수 있는 면적   33.5%

절반이 넘는 구간에서 두 대가 물리적으로 교차할 수 없다. 그런 곳에서 한 대가
멈춰 비켜주면 상대가 지나가지 못하므로, 양보가 곧 봉쇄다. 그래서 조정의
단위가 '만났을 때 누가 물러나나' 가 아니라 **'어느 구간에 지금 누가
들어가 있나'** 여야 한다. 이 스크립트가 그 구간을 만든다.

## 무엇을 만드나

    zone     두 대가 교차하거나 한 대가 기다릴 수 있는 넓은 곳
    segment  한 대만 지나가는 외길. **여기가 배타 점유 단위다**

zone 을 기다리는 자리로 쓰는 것이 요점이다. 외길 안에서 기다리면 그게
봉쇄다.

## 외길을 왜 통째로 하나로 두지 않나

이 지도의 외길은 전부 이어져 있다. 하나로 묶으면 뮤텍스 하나가 주행 공간의
20% 를 잠그고, 그러면 **충전소에 주차한 로봇이 X-ray 로 가는 복도까지
막는다.** 그래서 갈라지지 않는 한 줄기 단위로 쪼갠다 — 서로 다른 줄기에
있는 두 대는 만날 일이 없다.

쪼개는 방법은 **뼈대의 가지** 다. 외길을 한 셀 두께로 세선화하고 분기점에서
자른다 (`split_into_segments`). 이웃이 하나뿐인 구간이 막다른 길이고,
충전소 진입로가 거기 해당한다 — 두 대가 동시에 들어가면 빠져나올 방법이
없어 예약이 가장 중요한 곳이다.

## 왜 파일로 굽나

맵이 바뀌면 전부 다시 계산해야 하는 값이라, waypoint 와 같은 성격이다
(config/waypoints/). 실행할 때마다 계산하면 로봇과 서버가 서로 다른 구간
지도를 들고 판정하는 날이 온다. **굽고 커밋하고, 맵을 바꾸면 다시 굽는다.**

## 사용

    ./build_fleet_segments.py                  # 검사만 하고 요약을 찍는다
    ./build_fleet_segments.py --write          # config/fleet/ 에 굽는다
    ./build_fleet_segments.py --ascii          # 지도를 글자로 그려 눈으로 본다
"""

import argparse
import collections
import hashlib
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML 이 필요합니다: pip install pyyaml")


# 로봇 폭. nav2_params.yaml 의 footprint [[0.06,0.06],...] 에서 온다.
# 여기 값을 바꾸려면 저쪽을 먼저 바꿔라 — 정본은 Nav2 설정이다.
ROBOT_WIDTH = 0.12

# 두 대가 스쳐 지날 때 남길 여유(합계). 4 cm 는 양쪽 벽과 로봇 사이에 2 cm
# 씩이라는 뜻이고, 이 로봇의 위치추정 오차를 생각하면 이미 빠듯하다.
# 올리면 zone 이 줄고 조정이 보수적이 된다.
PASS_MARGIN = 0.04

# 한 대가 지나가는 데 필요한 여유. **Nav2 판정과 같은 값이어야 한다** —
# 내접 반경(footprint 0.06) + footprint_padding(0.01) = 0.07 이다.
#
# 처음에 여기를 0.02 로 잡아 0.08 을 썼다가 충전소가 그래프에서 통째로
# 떨어져 나왔다. 진입로 어딘가가 0.07~0.08 사이라, Nav2 는 지나갈 수 있다고
# 보는데 이 스크립트만 못 지나간다고 본 것이다. **여기를 Nav2 보다 엄하게
# 잡으면 실재하는 통로가 지도에서 사라지고, 예약층은 그 통로를 영영 모른다.**
SOLO_MARGIN = 0.01

# zone 은 **로봇이 실제로 물러나 서 있을 수 있어야** 한다. 로봇 발자국의 몇
# 배인가로 잰다 — 한 대가 비켜서고 다른 한 대가 지나갈 통로가 남아야 하므로
# 최소 넷은 필요하다. 이보다 작은 덩어리를 zone 이라 부르면 "여기서
# 기다려라" 가 거짓말이 된다 (실측에서 0.013 m² 짜리가 하나 잡혔는데,
# 로봇 발자국 0.0144 m² 보다도 작았다).
MIN_ZONE_FOOTPRINTS = 4

# segment 는 **로봇 한 대가 온전히 들어갈 만큼** 은 되어야 한다. 로봇보다
# 짧으면 한 대가 두세 구간을 동시에 밟고, 그러면 서로 다른 구간을 쥔 두 대가
# 사실은 맞닿아 있으면서 '충돌 없음' 으로 판정된다.
#
# 그래도 완전히는 못 막는다. 구간을 아무리 키워도 경계에 선 로봇은 이웃
# 구간에 걸쳐 있다. 그래서 **예약층은 구간 하나를 잡을 때 이웃 구간도 함께
# 막아야 한다** — 그러라고 connects 를 굽는다. 여기서는 그 부담을 줄일 뿐이다.
MIN_SEGMENT_FOOTPRINTS = 2


def read_pgm(path: Path):
    """P5 (binary) PGM. GIMP 가 넣는 주석 줄도 건너뛴다 (check_waypoints 와 동일)."""
    data = path.read_bytes()
    if data[:2] != b"P5":
        raise ValueError(f"P5 PGM 이 아닙니다: {path}")
    fields, i = [], 2
    while len(fields) < 3:
        while data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            while data[i:i + 1] != b"\n":
                i += 1
            continue
        j = i
        while not data[j:j + 1].isspace():
            j += 1
        fields.append(int(data[i:j]))
        i = j
    i += 1
    width, height, _ = fields
    return width, height, data[i:i + width * height]


def find_default(*relative: str) -> Path | None:
    """설치된 share 를 먼저 보고 없으면 소스 트리를 거슬러 올라간다.

    check_waypoints.py 와 같은 규칙이다. ros2 run 으로 부르면 이 파일이
    install/.../lib 에 있어 소스 트리 탐색만으로는 map/ 을 못 찾는다.
    """
    try:
        from ament_index_python.packages import get_package_share_directory

        share = Path(get_package_share_directory("mingky_bringup"))
        for rel in relative:
            candidate = share / rel
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


class Grid:
    """맵 한 장을 셀 단위로 들고 있는 것. 좌표 변환과 거리변환을 담당한다."""

    NEIGHBORS8 = ((1, 0), (-1, 0), (0, 1), (0, -1),
                  (1, 1), (1, -1), (-1, 1), (-1, -1))
    NEIGHBORS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

    def __init__(self, map_yaml: Path):
        meta = yaml.safe_load(map_yaml.read_text())
        self.meta = meta
        self.res = float(meta["resolution"])
        self.ox, self.oy = float(meta["origin"][0]), float(meta["origin"][1])
        image = map_yaml.parent / meta["image"]
        self.image_path = image
        self.w, self.h, pixels = read_pgm(image)
        self.map_name = map_yaml.stem
        self.map_sha = hashlib.sha256(image.read_bytes()).hexdigest()[:16]

        free_thresh = float(meta.get("free_thresh", 0.196))
        # 미지 영역은 '못 가는 곳' 으로 본다. 매핑 안 된 데를 통로로 세면
        # 있지도 않은 우회로를 믿고 판정하게 된다.
        self.free = bytearray(self.w * self.h)
        for k in range(self.w * self.h):
            if (255 - pixels[k]) / 255.0 <= free_thresh:
                self.free[k] = 1

        self.clearance = self._distance_to_blocked()

    def _distance_to_blocked(self):
        """각 셀에서 가장 가까운 '못 가는 셀' 까지의 거리(m).

        8방향 BFS 라 대각선을 1 로 세어 실제보다 조금 크게 나온다. 여유를
        후하게 보는 방향이라 안전 쪽으로 틀리지 않으므로, 뒤에서 임계에
        여유를 얹는 것으로 갚는다.
        """
        INF = 1 << 30
        dist = [INF] * (self.w * self.h)
        queue = collections.deque()
        for k in range(self.w * self.h):
            if not self.free[k]:
                dist[k] = 0
                queue.append(k)
        while queue:
            k = queue.popleft()
            x, y = k % self.w, k // self.w
            nd = dist[k] + 1
            for dx, dy in self.NEIGHBORS8:
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.w and 0 <= ny < self.h:
                    nk = ny * self.w + nx
                    if dist[nk] == INF:
                        dist[nk] = nd
                        queue.append(nk)
        return [d * self.res if d < INF else 0.0 for d in dist]

    def world(self, k: int) -> tuple[float, float]:
        x, y = k % self.w, k // self.w
        # 이미지 첫 행이 가장 큰 y 다 (check_waypoints 의 occupied_cells 와 동일).
        return (self.ox + (x + 0.5) * self.res,
                self.oy + (self.h - 1 - y + 0.5) * self.res)

    def cell(self, wx: float, wy: float) -> int | None:
        x = int((wx - self.ox) / self.res)
        y = self.h - 1 - int((wy - self.oy) / self.res)
        if 0 <= x < self.w and 0 <= y < self.h:
            return y * self.w + x
        return None

    def components(self, mask, connectivity=NEIGHBORS4):
        """mask 가 참인 셀들의 연결 덩어리. 큰 것부터 돌려준다."""
        seen = bytearray(self.w * self.h)
        out = []
        for start in range(self.w * self.h):
            if not mask[start] or seen[start]:
                continue
            cells, queue = [], collections.deque([start])
            seen[start] = 1
            while queue:
                k = queue.popleft()
                cells.append(k)
                x, y = k % self.w, k // self.w
                for dx, dy in connectivity:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.w and 0 <= ny < self.h:
                        nk = ny * self.w + nx
                        if mask[nk] and not seen[nk]:
                            seen[nk] = 1
                            queue.append(nk)
            out.append(cells)
        return sorted(out, key=len, reverse=True)


def thin(grid: Grid, mask) -> set:
    """Zhang-Suen 세선화. 외길 덩어리를 한 셀 두께의 뼈대로 줄인다.

    분기점을 찾으려고 쓴다. 통로가 2~4 셀 두께라 셀 하나를 지워도 끊기지
    않으므로 단절점 탐색이 안 먹고, 뼈대로 줄여야 'T 자로 갈라지는 곳' 이
    이웃 3개짜리 셀로 드러난다.
    """
    px = {k for k in range(grid.w * grid.h) if mask[k]}
    w = grid.w

    def nb(k):
        x, y = k % w, k // w
        # P2..P9 = 위에서 시계방향. Zhang-Suen 의 표준 순서다.
        order = ((0, -1), (1, -1), (1, 0), (1, 1),
                 (0, 1), (-1, 1), (-1, 0), (-1, -1))
        out = []
        for dx, dy in order:
            nx, ny = x + dx, y + dy
            out.append(1 if (0 <= nx < grid.w and 0 <= ny < grid.h
                             and (ny * w + nx) in px) else 0)
        return out

    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            doomed = []
            for k in px:
                n = nb(k)
                total = sum(n)
                if not 2 <= total <= 6:
                    continue
                transitions = sum(
                    1 for i in range(8)
                    if n[i] == 0 and n[(i + 1) % 8] == 1)
                if transitions != 1:
                    continue
                if step == 0:
                    if n[0] * n[2] * n[4] or n[2] * n[4] * n[6]:
                        continue
                else:
                    if n[0] * n[2] * n[6] or n[0] * n[4] * n[6]:
                        continue
                doomed.append(k)
            if doomed:
                px.difference_update(doomed)
                changed = True
    return px


def branch_labels(grid: Grid, skeleton: set) -> dict:
    """뼈대를 분기점에서 잘라 가지별로 번호를 매긴다.

    가지 하나가 곧 '갈라지지 않는 통로 한 줄기' 다. 서로 다른 가지에 있는
    두 대는 마주칠 일이 없으므로, 여기가 배타 점유의 자연스러운 단위다.
    """
    w = grid.w

    def neighbors(k):
        x, y = k % w, k // w
        for dx, dy in Grid.NEIGHBORS8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < grid.w and 0 <= ny < grid.h:
                nk = ny * w + nx
                if nk in skeleton:
                    yield nk

    junction = {k for k in skeleton if sum(1 for _ in neighbors(k)) >= 3}
    body = skeleton - junction

    label, n = {}, 0
    for start in sorted(body):
        if start in label:
            continue
        n += 1
        queue = collections.deque([start])
        label[start] = n
        while queue:
            k = queue.popleft()
            for nk in neighbors(k):
                if nk in body and nk not in label:
                    label[nk] = n
                    queue.append(nk)
    # 분기점 자체는 어느 가지에도 안 준다. 교차로를 한쪽 가지에 붙이면 그
    # 가지를 쥔 로봇이 교차로를 통째로 막아 다른 가지도 못 쓰게 된다.
    return label


def split_into_segments(grid: Grid, narrow, min_cells: int):
    """외길을 '갈라지지 않는 한 줄기' 단위로 쪼갠다.

    ## 왜 뼈대인가

    zone 이 세 개뿐이라 zone 경계로만 자르면 외길이 한 덩어리로 남는다.
    그러면 충전소에 주차한 로봇이 X-ray 로 가는 복도까지 통째로 잠근다 —
    실제로 그렇게 만들어 봤더니 주행 공간의 20% 가 뮤텍스 하나가 됐다.

    그래서 외길을 한 셀 두께의 뼈대로 줄이고 **분기점에서 자른다.** 가지
    하나가 곧 갈라지지 않는 통로 한 줄기이고, 서로 다른 가지에 있는 두 대는
    마주칠 일이 없다. 분기점 자체는 어느 가지에도 주지 않는다 (branch_labels
    주석 참고).

    ## 짧은 가지는 버리지 않고 합친다

    로봇(0.12 m)보다 짧은 구간은 로봇 한 대가 두 구간을 동시에 밟는다는
    뜻이라 점유 단위로 쓸 수 없다. 그렇다고 버리면 그 자리가 아무도
    예약하지 않는 사각지대가 되는데, 하필 그런 조각이 교차로 언저리에
    생긴다. 맞닿은 구간 중 가장 큰 쪽으로 흡수시킨다.
    """
    narrow_mask = bytearray(grid.w * grid.h)
    for k in range(grid.w * grid.h):
        if narrow[k]:
            narrow_mask[k] = 1
    branch = branch_labels(grid, thin(grid, narrow_mask))

    # 뼈대에 없는 외길 셀은 가장 가까운 가지에 붙인다.
    branch_of = dict(branch)
    queue = collections.deque(branch)
    while queue:
        k = queue.popleft()
        x, y = k % grid.w, k // grid.w
        for dx, dy in Grid.NEIGHBORS8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < grid.w and 0 <= ny < grid.h:
                nk = ny * grid.w + nx
                if narrow[nk] and nk not in branch_of:
                    branch_of[nk] = branch_of[k]
                    queue.append(nk)

    keyed = {}
    for k in range(grid.w * grid.h):
        if narrow[k]:
            keyed.setdefault(branch_of.get(k), []).append(k)

    raw = []
    for cells in keyed.values():
        mask = bytearray(grid.w * grid.h)
        for k in cells:
            mask[k] = 1
        raw.extend(grid.components(mask))

    raw.sort(key=len, reverse=True)
    owner = {k: n for n, group in enumerate(raw) for k in group}
    merged = {n: list(g) for n, g in enumerate(raw)}
    for n in range(len(raw) - 1, -1, -1):
        if n not in merged or len(merged[n]) >= min_cells:
            continue
        touch = collections.Counter()
        for k in merged[n]:
            x, y = k % grid.w, k // grid.w
            for dx, dy in Grid.NEIGHBORS8:
                nx, ny = x + dx, y + dy
                if 0 <= nx < grid.w and 0 <= ny < grid.h:
                    other = owner.get(ny * grid.w + nx)
                    if other is not None and other != n and other in merged:
                        touch[other] += 1
        if not touch:
            continue
        into = max(touch, key=lambda o: (len(merged[o]), -o))
        for k in merged.pop(n):
            owner[k] = into
            merged[into].append(k)

    return [{"cells": cells}
            for cells in sorted(merged.values(), key=len, reverse=True)]


def adjacency(grid: Grid, areas: dict) -> dict:
    """구간끼리 어디가 어디에 붙어 있는가.

    예약층이 실제로 쓰는 것이 이 그래프다 — "이 구간을 못 잡으면 어디서
    기다려야 하나" 가 여기서 나온다. 그리고 **막다른 길 판정도 여기서**
    해야 한다. zone 에 직접 닿는지만 보면 통로 한가운데 있는 구간이 전부
    막다른 길로 잡힌다 (실제로 25개 중 22개가 그렇게 나왔다).
    """
    owner = {}
    for area_id, cells in areas.items():
        for k in cells:
            owner[k] = area_id
    graph = {area_id: set() for area_id in areas}
    for k, area_id in owner.items():
        x, y = k % grid.w, k // grid.w
        for dx, dy in Grid.NEIGHBORS8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < grid.w and 0 <= ny < grid.h:
                other = owner.get(ny * grid.w + nx)
                if other is not None and other != area_id:
                    graph[area_id].add(other)
    return {a: sorted(n) for a, n in graph.items()}


def build(grid: Grid, waypoints: dict, pass_clear: float, solo_clear: float,
          robot_width: float):
    cell_m2 = grid.res * grid.res
    footprint_cells = max(1, round(robot_width * robot_width / cell_m2))
    min_zone = footprint_cells * MIN_ZONE_FOOTPRINTS
    min_segment = footprint_cells * MIN_SEGMENT_FOOTPRINTS

    drivable = [1 if grid.clearance[k] >= solo_clear else 0
                for k in range(grid.w * grid.h)]
    wide = [1 if drivable[k] and grid.clearance[k] >= pass_clear else 0
            for k in range(grid.w * grid.h)]
    narrow = [1 if drivable[k] and not wide[k] else 0
              for k in range(grid.w * grid.h)]

    zones, zone_of = [], {}
    for cells in grid.components(wide):
        if len(cells) < min_zone:
            # 로봇이 물러나 설 수 없는 넓이다. zone 이라 부르면 "여기서
            # 기다려라" 가 거짓말이 되므로 외길로 되돌린다.
            for k in cells:
                narrow[k] = 1
            continue
        zid = f"zone-{len(zones) + 1}"
        for k in cells:
            zone_of[k] = zid
        zones.append({"id": zid, "cells": cells})

    raw_segments = split_into_segments(grid, narrow, min_segment)
    segments = []
    for n, seg in enumerate(raw_segments, start=1):
        seg["id"] = f"seg-{n}"
        for k in seg["cells"]:
            zone_of.setdefault(k, seg["id"])
        segments.append(seg)

    # 남은 주행 가능 셀. 덮지 않으면 아무도 예약하지 않는 사각지대가 된다.
    #
    # **넓고 좁음을 넘어 흡수하지 않는다.** 좁은 셀을 zone 이 삼키면
    # "여기서는 두 대가 스쳐 지날 수 있다" 가 거짓이 된다 — 실제로 한 번
    # 그렇게 만들었더니 충전소(여유 0.125 m)가 교차 가능 zone 으로 분류되어,
    # 같은 출력 안에서 '통행을 막는다' 와 모순됐다.
    leftover = [k for k in range(grid.w * grid.h)
                if drivable[k] and k not in zone_of]
    absorbed = len(leftover)
    if leftover:
        by_id = {z["id"]: z for z in zones}
        by_id.update({s["id"]: s for s in segments})
        wide_ids = {z["id"] for z in zones}
        seen = set(zone_of)
        queue = collections.deque(zone_of)
        while queue:
            k = queue.popleft()
            x, y = k % grid.w, k // grid.w
            for dx, dy in Grid.NEIGHBORS8:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < grid.w and 0 <= ny < grid.h):
                    continue
                nk = ny * grid.w + nx
                if not drivable[nk] or nk in seen:
                    continue
                owner = zone_of[k]
                # 넓은 셀은 zone 으로만, 좁은 셀은 segment 로만 흡수된다.
                if bool(wide[nk]) != (owner in wide_ids):
                    continue
                seen.add(nk)
                zone_of[nk] = owner
                queue.append(nk)
        for k in leftover:
            owner = zone_of.get(k)
            if owner in by_id:
                by_id[owner]["cells"].append(k)
        absorbed = sum(1 for k in leftover if k in zone_of)

    areas = {item["id"]: item["cells"] for item in zones + segments}
    graph = adjacency(grid, areas)
    for item in zones + segments:
        item["connects"] = graph[item["id"]]
        # 이웃이 하나뿐이면 되돌아 나오는 길밖에 없다. 두 대가 동시에
        # 들어가면 빠져나올 방법이 없는 곳이라 예약이 가장 중요하다.
        item["dead_end"] = len(graph[item["id"]]) <= 1

    area_of = {}
    for item in zones + segments:
        area_of[item["id"]] = len(item["cells"]) * cell_m2

    # waypoint 를 구간에 붙인다. 정확히 그 셀이 어디 속하는지가 판정의 근거다.
    #
    # 주행 가능 판정에 못 미치는 waypoint 도 **버리지 않고** 가장 가까운
    # 구간에 붙인다. 버리면 그 자리는 예약층이 보호하지 않게 되는데, 벽에
    # 가까워 위태로운 지점일수록 보호가 더 필요하다. 대신 marginal 로 표시해
    # 화면과 사람이 알아볼 수 있게 남긴다.
    placed = {}
    for name, p in sorted(waypoints.items()):
        k = grid.cell(float(p["x"]), float(p["y"]))
        clear = grid.clearance[k] if k is not None else 0.0
        area = zone_of.get(k) if k is not None else None
        if area is None and k is not None:
            area = _nearest_area(grid, zone_of, k)
        placed[name] = {
            "area": area,
            "clearance": round(clear, 3),
            # 여기 로봇이 서 있으면 상대가 지나갈 수 없다.
            "blocks_passage": bool(clear < pass_clear),
            # Nav2 가 통과 불가로 볼 수 있는 위치. check_waypoints.py 가
            # 같은 것을 더 정확히 본다 — 여기서는 표시만 한다.
            "marginal": bool(clear < solo_clear),
        }
    stats = {"absorbed_cells": absorbed,
             "footprint_cells": footprint_cells,
             "min_zone_cells": min_zone}
    return zones, segments, placed, area_of, drivable, wide, narrow, stats


def _nearest_area(grid: Grid, zone_of: dict, start: int) -> str | None:
    """구간에 못 붙은 셀에서 가장 가까운 구간을 찾는다 (막힌 셀도 지나간다)."""
    seen = {start}
    queue = collections.deque([start])
    while queue:
        k = queue.popleft()
        x, y = k % grid.w, k // grid.w
        for dx, dy in Grid.NEIGHBORS8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < grid.w and 0 <= ny < grid.h:
                nk = ny * grid.w + nx
                if nk in seen:
                    continue
                if nk in zone_of:
                    return zone_of[nk]
                seen.add(nk)
                queue.append(nk)
    return None


def grid_raster(grid: Grid, zones, segments) -> dict:
    """셀마다 어느 구간인지를 런렝스로 접는다.

    좌표(m) → 셀 → 구간을 소비자가 계산할 수 있게 하는 것이 목적이다.
    구간별 셀 목록을 그대로 쓰면 파일이 수만 줄이 되고, 좌표에서 구간을
    찾으려면 전부 훑어야 한다. 행마다 `개수:번호` 로 접으면 이 맵에서
    150 줄 남짓이고 조회는 O(행의 런 수) 다.

    번호 0 은 '못 가는 곳' 이다. 이름표는 legend 에 있다.
    """
    index, legend = {}, {}
    for n, item in enumerate(list(zones) + list(segments), start=1):
        legend[n] = item["id"]
        for k in item["cells"]:
            index[k] = n

    rows = []
    for y in range(grid.h):
        runs, run_value, run_len = [], index.get(y * grid.w, 0), 0
        for x in range(grid.w):
            value = index.get(y * grid.w + x, 0)
            if value == run_value:
                run_len += 1
            else:
                runs.append(f"{run_len}:{run_value}")
                run_value, run_len = value, 1
        runs.append(f"{run_len}:{run_value}")
        rows.append(" ".join(runs))

    return {
        "width": grid.w,
        "height": grid.h,
        "resolution": grid.res,
        # Nav2 map yaml 과 같은 규약. 이미지 첫 행이 가장 큰 y 다.
        "origin": [grid.ox, grid.oy],
        "legend": legend,
        "rows": rows,
    }


def render_ascii(grid: Grid, zones, segments, step=3):
    """구간을 글자로 그린다. 눈으로 확인하는 것이 이 스크립트의 절반이다.

    3셀(7.5 cm)을 글자 하나로 줄이면서 **zone 을 우선해 표시한다.** 경계가
    섞인 칸에서 어느 쪽을 보여줄지 정해야 하는데, 좁은 곳을 넓게 보여주는
    쪽이 위험한 오해라 그 반대를 택할 수도 있었다. 여기서는 큰 그림을
    읽는 것이 목적이라 zone 을 남긴다 — **면적 판정에 이 그림을 쓰지 마라.**
    숫자는 위 요약표에 있다.
    """
    letters = "0123456789abcdefghijklmnopqrstuvwxyz"
    label = {}
    for n, z in enumerate(zones):
        for k in z["cells"]:
            label[k] = str(n + 1)          # zone 은 숫자
    for n, s in enumerate(segments):
        for k in s["cells"]:
            label[k] = letters[10 + n % 26]  # segment 는 알파벳

    rows = []
    for cy in range(grid.h // step):
        line = []
        for cx in range(grid.w // step):
            found = " "
            for dy in range(step):
                for dx in range(step):
                    y, x = cy * step + dy, cx * step + dx
                    if y < grid.h and x < grid.w:
                        ch = label.get(y * grid.w + x)
                        if ch is not None and (found == " " or ch.isdigit()):
                            found = ch
            line.append(found)
        rows.append("".join(line).rstrip())
    while rows and not rows[0].strip():
        rows.pop(0)
    while rows and not rows[-1].strip():
        rows.pop()
    return rows


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--map", type=Path, default=None)
    p.add_argument("--waypoints", type=Path, default=None)
    p.add_argument("--robot-width", type=float, default=ROBOT_WIDTH)
    p.add_argument("--pass-margin", type=float, default=PASS_MARGIN)
    p.add_argument("--solo-margin", type=float, default=SOLO_MARGIN)
    p.add_argument("--write", action="store_true",
                   help="config/fleet/ 에 굽는다. 없으면 검사만 한다")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--ascii", action="store_true", help="지도를 글자로 그린다")
    args = p.parse_args()

    map_yaml = args.map or find_default("map/yun_map_highres_clean.yaml")
    if map_yaml is None or not map_yaml.is_file():
        print("맵을 찾지 못했습니다. --map 으로 지정하세요.", file=sys.stderr)
        return 2
    grid = Grid(map_yaml)

    wp_path = args.waypoints or find_default(
        f"config/waypoints/{grid.map_name}_waypoints.yaml")
    if wp_path is None or not wp_path.is_file():
        print("waypoint 파일을 찾지 못했습니다. --waypoints 로 지정하세요.",
              file=sys.stderr)
        return 2
    wp_doc = yaml.safe_load(wp_path.read_text())
    waypoints = wp_doc.get("waypoints") or {}

    pass_clear = args.robot_width + args.pass_margin / 2
    solo_clear = args.robot_width / 2 + args.solo_margin

    zones, segments, placed, area_of, drivable, wide, narrow, stats = build(
        grid, waypoints, pass_clear, solo_clear, args.robot_width)

    cell_m2 = grid.res * grid.res
    n_drive = sum(drivable)
    print(f"맵  {grid.map_name}  {grid.w}x{grid.h} px "
          f"({grid.w * grid.res:.2f} x {grid.h * grid.res:.2f} m)  sha={grid.map_sha}")
    print(f"로봇 폭 {args.robot_width:.2f} m  ·  교차 기준 여유 {pass_clear:.3f} m  "
          f"·  통행 기준 여유 {solo_clear:.3f} m")
    print(f"주행 가능 {n_drive}셀 ({n_drive * cell_m2:.3f} m²)"
          f"  ·  부스러기 {stats['absorbed_cells']}셀은 이웃 구간에 흡수\n")

    zone_area = sum(area_of[z["id"]] for z in zones)
    seg_area = sum(area_of[s["id"]] for s in segments)
    print(f"zone (두 대 교차·대기 가능) {len(zones)}개 — {zone_area:.3f} m²")
    for z in zones:
        wps = [n for n, v in placed.items() if v["area"] == z["id"]]
        cx, cy = grid.world(z["cells"][len(z["cells"]) // 2])
        print(f"  {z['id']:8s} {area_of[z['id']]:.3f} m²  waypoint {len(wps):2d}개"
              + (f"  {', '.join(sorted(wps)[:3])}" if wps else ""))

    print(f"\nsegment (외길 · 배타 점유 단위) {len(segments)}개 — {seg_area:.3f} m²")
    for s in segments:
        wps = [n for n, v in placed.items() if v["area"] == s["id"]]
        tag = ("막다른 길" if s["dead_end"]
               else f"이웃 {len(s['connects'])}")
        print(f"  {s['id']:8s} {area_of[s['id']]:.3f} m²  {tag:10s}"
              + (f"  [{', '.join(sorted(wps))}]" if wps else ""))

    blockers = sorted(n for n, v in placed.items() if v["blocks_passage"])
    print(f"\n서 있으면 통행을 막는 waypoint {len(blockers)}/{len(placed)}")
    for name in blockers:
        v = placed[name]
        print(f"  {name:34s} 여유 {v['clearance']:.3f} m  ({v['area']})")

    # 그래프가 끊겨 있으면 예약층이 못 가는 길을 만들어 낸다. 임계를 조금만
    # 엄하게 잡아도 통로가 끊기므로(SOLO_MARGIN 주석 참고) 반드시 확인한다.
    areas = {z["id"]: z for z in zones}
    areas.update({s["id"]: s for s in segments})
    islands = []
    if areas:
        seen = {next(iter(areas))}
        queue = collections.deque(seen)
        while queue:
            a = queue.popleft()
            for b in areas[a]["connects"]:
                if b not in seen:
                    seen.add(b)
                    queue.append(b)
        islands = sorted(set(areas) - seen)
    if islands:
        print(f"\n[경고] 나머지와 이어지지 않은 구간 {len(islands)}개 — "
              "이 임계에서는 도달할 수 없는 곳입니다")
        for area_id in islands:
            wps = areas[area_id]["waypoints"] if "waypoints" in areas[area_id] else [
                n for n, v in placed.items() if v["area"] == area_id]
            print(f"  {area_id:8s} {area_of[area_id]:.3f} m²"
                  + (f"  [{', '.join(sorted(wps))}]" if wps else ""))
        print("  --solo-margin 을 낮춰 Nav2 판정과 맞춰 보세요.")

    marginal = sorted(n for n, v in placed.items() if v["marginal"])
    if marginal:
        print(f"\n[주의] 통행 기준 여유({solo_clear:.3f} m)에 못 미치는 "
              f"waypoint {len(marginal)}개 — 가장 가까운 구간에 붙였습니다")
        for name in marginal:
            v = placed[name]
            print(f"  {name:34s} 여유 {v['clearance']:.3f} m  → {v['area']}")
        print("  Nav2 도달 가능 여부는 check_waypoints.py 가 정확히 봅니다.")

    orphan = sorted(n for n, v in placed.items() if v["area"] is None)
    if orphan:
        print(f"\n[경고] 어느 구간에도 못 붙은 waypoint {len(orphan)}개 — "
              "맵 밖이거나 완전히 고립된 위치입니다")
        for name in orphan:
            print(f"  {name:34s} 여유 {placed[name]['clearance']:.3f} m")

    if args.ascii:
        print("\n숫자 = zone(교차·대기 가능)   알파벳 = segment(외길)   공백 = 못 감")
        print("\n".join(render_ascii(grid, zones, segments)))

    doc = {
        "map": grid.map_name,
        "map_sha256": grid.map_sha,
        "robot_width": args.robot_width,
        "pass_clearance": round(pass_clear, 4),
        "solo_clearance": round(solo_clear, 4),
        # 좌표를 구간으로 바꾸는 데 필요한 것. 이게 없으면 예약층이 이 파일을
        # 읽고도 "이 로봇이 지금 어느 구간인가" 를 답하지 못해, 결국 위
        # 알고리즘을 한 벌 더 구현하게 된다. 정본은 하나여야 한다.
        "grid": grid_raster(grid, zones, segments),
        "zones": {
            z["id"]: {
                "kind": "zone",
                "area_m2": round(area_of[z["id"]], 4),
                "connects": z["connects"],
                "waypoints": sorted(n for n, v in placed.items()
                                    if v["area"] == z["id"]),
            } for z in zones
        },
        "segments": {
            s["id"]: {
                "kind": "segment",
                "area_m2": round(area_of[s["id"]], 4),
                "connects": s["connects"],
                "dead_end": s["dead_end"],
                "waypoints": sorted(n for n, v in placed.items()
                                    if v["area"] == s["id"]),
            } for s in segments
        },
        "waypoints": {n: dict(v) for n, v in sorted(placed.items())},
    }

    if args.write:
        out = args.out or (wp_path.parents[1] / "fleet"
                           / f"{grid.map_name}_segments.yaml")
        out.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# 생성물이다. 손으로 고치지 말고 아래 명령으로 다시 구워라.\n"
            "#\n"
            "#     ros2 run mingky_bringup build_fleet_segments.py --write\n"
            "#\n"
            "# 무엇이고 왜 있는지는 scripts/build_fleet_segments.py 주석에 있다.\n"
            "# 요약하면 — 이 지도는 절반 이상이 외길이라 '만나면 양보' 가\n"
            "# 성립하지 않는다. 그래서 조정 단위를 구간으로 두고, 넓은 곳\n"
            "# (zone) 에서 기다렸다가 외길(segment) 을 한 대씩 지난다.\n"
            "#\n"
            f"# map_sha256 이 맵 파일과 다르면 이 파일은 낡은 것이다.\n")
        out.write_text(header + yaml.safe_dump(
            doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"\n구웠습니다: {out}")
    else:
        print("\n(검사만 했습니다. 굽려면 --write)")

    return 1 if orphan else 0


if __name__ == "__main__":
    sys.exit(main())
