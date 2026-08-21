"""구간 지도를 읽는다 — 좌표가 어느 구간이고, 어디로 어떻게 이어지는가.

정본은 `mingky_ros/mingky_bringup/config/fleet/<map>_segments.yaml` 이고
`build_fleet_segments.py` 가 굽는다. 이 모듈은 **읽기만 한다** — 같은 계산을
여기서 한 번 더 하면 로봇과 서버가 서로 다른 지도를 들고 판정하는 날이 온다
(맵·waypoint 를 파일에서 읽는 것과 같은 규칙: routers/maps.py 참고).

## 무엇이 들어 있나

    zone     두 대가 스쳐 지나거나 한 대가 기다릴 수 있는 넓은 곳
    segment  한 대만 지나가는 외길. 배타 점유 단위다

이 지도는 주행 공간의 중앙값 자유폭이 0.25 m 인데 로봇 두 대는 0.24 m 다.
그래서 '만나면 양보' 가 성립하지 않고, **애초에 같은 외길에 둘이 들어가지
않게** 하는 것이 유일한 방법이다. 그 판정에 쓰는 자료가 이것이다.

## 왜 기동 시 한 번만 읽나

맵이 바뀌면 재배포한다 (routers/maps.py 와 같은 판단). 요청마다 12 KB YAML 을
파싱할 이유가 없다. `registry` · `topic_watch` 와 같은 lifespan 로드 패턴이다.

## 없으면 어떻게 되나

**기동을 막지 않는다.** 구간 지도가 없으면 조정이 꺼진 채로 돌고, 그건 이
기능을 붙이기 전의 동작과 같다. 조정층은 안전장치가 아니라 교착 예방층이라
(LiDAR·MPPI·워치독이 안전을 맡는다) 없다고 로봇이 위험해지지 않는다.
알림 웹훅이 URL 없으면 꺼진 채 도는 것과 같은 규칙이다 (notify.py).
"""

from __future__ import annotations

import collections
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("mingky")

SEGMENTS_FILE = Path(os.environ.get(
    "FLEET_SEGMENTS_FILE",
    "/srv/mingky/fleet/yun_map_highres_clean_segments.yaml",
))


@dataclass(frozen=True)
class Area:
    """zone 하나 또는 segment 하나."""

    area_id: str
    kind: str                      # "zone" | "segment"
    area_m2: float
    connects: tuple[str, ...]
    waypoints: tuple[str, ...]
    dead_end: bool = False

    @property
    def exclusive(self) -> bool:
        """한 번에 한 대만 들어갈 수 있는가.

        zone 은 두 대가 스쳐 지날 수 있으니 배타가 아니다. **여기서 기다리게
        하려고 zone 을 구분한 것**이므로, zone 을 배타로 만들면 기다릴 자리가
        사라져 설계가 무너진다.
        """
        return self.kind == "segment"


@dataclass(frozen=True)
class FleetMap:
    map_name: str
    map_sha256: str
    robot_width: float
    areas: dict[str, Area]
    # 좌표 조회용 래스터. 값 0 은 '못 가는 곳'.
    width: int
    height: int
    resolution: float
    origin: tuple[float, float]
    _rows: tuple[tuple[tuple[int, str | None], ...], ...] = field(repr=False,
                                                                 default=())

    def area_at(self, x: float, y: float) -> str | None:
        """맵 좌표(m)가 속한 구간. 못 가는 곳이거나 맵 밖이면 None.

        None 을 '오류' 로 다루면 안 된다. 로봇이 벽에 바짝 붙어 있거나
        위치추정이 잠깐 튀면 정상적으로 나온다 — 그때는 조정을 하지 않을 뿐
        이다 (모르는 것을 안다고 하지 않는다).
        """
        cx = int((x - self.origin[0]) / self.resolution)
        # 이미지 첫 행이 가장 큰 y 다 (Nav2 map_server 규약).
        cy = self.height - 1 - int((y - self.origin[1]) / self.resolution)
        if not (0 <= cx < self.width and 0 <= cy < self.height):
            return None
        pos = 0
        for length, area_id in self._rows[cy]:
            if pos <= cx < pos + length:
                return area_id
            pos += length
        return None

    def area_of_waypoint(self, name: str) -> str | None:
        for area in self.areas.values():
            if name in area.waypoints:
                return area.area_id
        return None

    def route(self, start: str, goal: str) -> list[str] | None:
        """구간 그래프 위의 최단 경로. 시작 구간을 포함한다.

        셀 단위 경로계획이 아니다 — 그건 Nav2 가 한다. 여기서 필요한 것은
        **어느 구간들을 지나게 되는가** 이고, 그 순서만 알면 무엇을 미리
        잡아야 하는지가 나온다.

        길이가 아니라 구간 수로 최단을 고른다. 실제 주행 거리와 다를 수
        있지만, 예약은 '몇 개를 잡아야 하나' 의 문제라 이쪽이 맞다.
        """
        if start not in self.areas or goal not in self.areas:
            return None
        if start == goal:
            return [start]
        prev: dict[str, str | None] = {start: None}
        queue = collections.deque([start])
        while queue:
            current = queue.popleft()
            for nxt in self.areas[current].connects:
                if nxt in prev:
                    continue
                prev[nxt] = current
                if nxt == goal:
                    path = [goal]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return list(reversed(path))
                queue.append(nxt)
        return None

    def blocked_by(self, area_id: str) -> set[str]:
        """이 구간을 쥐면 함께 막히는 구간들.

        구간이 로봇보다 조금 클 뿐이라 경계에 선 로봇은 이웃에 걸쳐 있다.
        이웃까지 막지 않으면 서로 다른 구간을 쥔 두 대가 사실은 맞닿아
        있으면서 '충돌 없음' 으로 판정된다 (build_fleet_segments.py 의
        MIN_SEGMENT_FOOTPRINTS 주석).

        **zone 은 막지 않는다.** zone 은 비켜설 수 있는 곳이라 걸쳐 있어도
        상대가 지나갈 수 있고, 막으면 기다릴 자리가 사라진다.
        """
        area = self.areas.get(area_id)
        if area is None or not area.exclusive:
            # zone 에 있는 로봇은 아무것도 막지 않는다. 비켜설 수 있는 곳이라
            # 이웃 외길에 걸칠 일이 없고, 막으면 **기다릴 자리가 사라진다** —
            # zone 에서 기다리게 하는 것이 이 설계의 전부다.
            return set()
        out = {area_id}
        for nxt in area.connects:
            neighbor = self.areas.get(nxt)
            if neighbor is not None and neighbor.exclusive:
                out.add(nxt)
        return out


_loaded: FleetMap | None = None


def load(path: Path | None = None) -> FleetMap | None:
    """기동 시 한 번. 없거나 깨졌으면 None 이고 조정은 꺼진 채로 돈다."""
    global _loaded
    target = path or SEGMENTS_FILE
    try:
        doc = yaml.safe_load(Path(target).read_text(encoding="utf-8"))
        _loaded = parse(doc)
    except FileNotFoundError:
        log.warning("구간 지도가 없습니다 (%s). 군집 조정은 꺼진 채로 돕니다.",
                    target)
        _loaded = None
    except Exception as exc:
        # 깨진 파일로 조정하면 없느니만 못하다. 끄고 크게 남긴다.
        log.error("구간 지도를 읽지 못했습니다 (%s): %s. 조정을 끕니다.",
                  target, exc)
        _loaded = None
    return _loaded


def parse(doc: dict) -> FleetMap:
    """YAML 문서를 FleetMap 으로. 파일을 모른다 — 문서만 받는다.

    `slo.judge` · `fleet_config.summarize` 와 같은 규칙이다. 테스트가 디스크
    없이 이 함수만 부를 수 있어야 판정 로직을 실기 없이 검증할 수 있다.
    """
    areas: dict[str, Area] = {}
    for kind, key in (("zone", "zones"), ("segment", "segments")):
        for area_id, raw in (doc.get(key) or {}).items():
            areas[area_id] = Area(
                area_id=area_id,
                kind=kind,
                area_m2=float(raw.get("area_m2") or 0.0),
                connects=tuple(raw.get("connects") or ()),
                waypoints=tuple(raw.get("waypoints") or ()),
                dead_end=bool(raw.get("dead_end")),
            )

    grid = doc.get("grid") or {}
    legend = {int(k): v for k, v in (grid.get("legend") or {}).items()}
    rows = []
    for row in grid.get("rows") or ():
        runs = []
        for token in str(row).split():
            length, value = token.split(":")
            runs.append((int(length), legend.get(int(value))))
        rows.append(tuple(runs))

    return FleetMap(
        map_name=str(doc.get("map") or ""),
        map_sha256=str(doc.get("map_sha256") or ""),
        robot_width=float(doc.get("robot_width") or 0.0),
        areas=areas,
        width=int(grid.get("width") or 0),
        height=int(grid.get("height") or 0),
        resolution=float(grid.get("resolution") or 0.0),
        origin=tuple(grid.get("origin") or (0.0, 0.0))[:2],
        _rows=tuple(rows),
    )


def get() -> FleetMap | None:
    return _loaded


def reset() -> None:
    global _loaded
    _loaded = None
