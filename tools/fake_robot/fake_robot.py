#!/usr/bin/env python3
"""ROS 없이 로봇을 흉내내는 하네스.

로봇↔서버 인터페이스가 순수 HTTP 다(monitoring-spec.md §3.2). 로봇을 흉내내는
데 ROS 도, 실기도 필요 없다. 이 스크립트가 heartbeat 를 보내고 시나리오를 읽어
session.started → nav.goal_sent → nav.goal_succeeded → … 를 시간 지연과 함께
뿌리면 대시보드는 진짜 로봇과 구분하지 못한다.

## 왜 필요한가 (§9.1)

  - 프론트·백엔드 담당자가 로봇 대기 없이 개발한다
  - 실패 경로를 마음대로 만든다. comm_lost 를 보려고 Wi-Fi 를 끊을 필요가 없고,
    오배선처럼 실기로는 만들기 곤란한 것도 한 줄로 재현된다
  - 그대로 통합 테스트 픽스처가 된다 (로드맵 8)

## 팔(manipulator)도 흉내낸다

§6.2 의 manipulator.* 정본이 서면서 흉내낼 규약이 생겼다. 실기(OMX)는 아직 이
코드를 내보내지 않으므로 지금은 이 하네스가 유일한 발행 측이고, 프론트·백엔드는
팔 게이트웨이를 기다리지 않고 조제 패널을 만들 수 있다.

주의할 것은 팔의 어휘가 mobile 과 다르다는 점이다. 팔에는 arming 도 QR 스캔도
없고(activation.* 는 mobile 전용), 세션에 딸리지 않는 조제는 session_id 가 0 이다.
heartbeat 는 공통 축이라 팔에도 보내지만, OMX 는 관제 PC 에 USB 직결이라
link_state 가 뜻하는 바가 mobile 과 다르다(§4.3).

## 정본 준수 검사

시나리오의 이벤트 코드를 config/event_codes.yaml 과 대조한다. 코드가 있는지,
level 이 정본과 맞는지, 그리고 **그 로봇 타입이 낼 수 있는 코드인지**까지 본다.
정본이 바뀌면 가짜 로봇이 먼저 깨지므로 하네스가 정본 준수 검사 역할을 겸한다.

백엔드 모듈을 import 하지 않는다. 이건 로봇 쪽을 흉내내는 별개 프로그램이고,
ROS 게이트웨이(mingky_guide_manager/event_publisher.py)도 같은 파일을 따로
읽는다. 서버 코드에 의존하면 서버와 함께 틀리게 된다.

## 사용법

    # 정본 대조만 (서버 불필요)
    python tools/fake_robot/fake_robot.py scenarios/session_complete.yaml --check

    # 실제 재생
    python tools/fake_robot/fake_robot.py scenarios/session_complete.yaml \
        --base-url http://localhost:8000
"""

import argparse
import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANON = REPO_ROOT / "config" / "event_codes.yaml"

# 게이트웨이의 실제 주기와 맞춘다. 더 느리면 arming 전제조건(link_state)이
# 시나리오 시작 전에 안 갖춰지고, 더 빠르면 없는 부하를 만든다.
HEARTBEAT_INTERVAL_SEC = 3.0

# 회차가 연달아 실패할 때 대기 간격을 몇 배까지 늘리나. loop_delay 가 5 초면
# 최대 30 초 간격이 된다 — 복구를 포기하지 않으면서 서버를 두드리지도 않는다.
BACKOFF_MAX_STEPS = 6

# 로봇이 events 에 싣는 source_node. 진짜 로봇과 구분되어야 한다 —
# 타임라인에서 가짜가 섞여 들어온 것을 알아볼 수 있어야 조사가 된다.
SOURCE_NODE = "fake_robot"


# --- 정본 ---------------------------------------------------------------------

class Canon:
    """config/event_codes.yaml. 발행 가능한 코드의 정본."""

    def __init__(self, codes: dict):
        self._codes = codes

    @classmethod
    def load(cls, path: Path = DEFAULT_CANON) -> "Canon":
        with Path(path).open(encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})

    def is_known(self, code: str) -> bool:
        return code in self._codes

    def level_of(self, code: str) -> str:
        return (self._codes.get(code) or {}).get("level", "info")

    def allowed_robot_types(self, code: str) -> list[str]:
        return (self._codes.get(code) or {}).get("robot_types", [])


# --- 시나리오 -----------------------------------------------------------------

@dataclass
class Robot:
    robot_id: str
    robot_type: str = "mobile"
    battery_percent: int = 90
    voltage: float = 11.8
    # heartbeat 에 싣는 통합 launch 상태.
    #
    # 기본이 active 인 것은 그게 정상 가동 중인 게이트웨이가 보고하는 값이기
    # 때문이다. 예전처럼 빈 본문을 보내면 서버는 unknown 으로 기억하고,
    # 제어 명령(goto·localize 등)을 "robot system is unknown" 409 로 막는다 —
    # 개입 시나리오 자체를 막는다.
    system_state: str = "active"
    # heartbeat 에 싣는 토픽 나이·주기 (§7.2). `{토픽: {age_sec, hz}}`.
    #
    # 라이다 USB 를 뽑지 않고도 `/scan` 두절을 재현하려면 이 값이 필요하다.
    # 실기에서 이 장애를 만들려면 사람이 로봇 앞에 서서 케이블을 뽑아야 하고,
    # 그건 CI 에서 못 한다.
    topics: dict = field(default_factory=dict)


@dataclass
class Step:
    action: str
    robot: str = ""
    # action 별 인자. 스키마를 여기서 강제하지 않고 액션 구현이 꺼내 쓴다.
    args: dict = field(default_factory=dict)
    # 이 스텝 전에 쉬는 시간. 주행이 즉시 끝나면 대시보드에서 진행을 볼 수 없다.
    wait: float = 0.0


@dataclass
class Scenario:
    name: str
    robots: list[Robot]
    steps: list[Step]
    path: Path | None = None

    def robot(self, robot_id: str) -> Robot | None:
        for robot in self.robots:
            if robot.robot_id == robot_id:
                return robot
        return None


def _robot(entry: dict) -> Robot:
    """YAML 은 id/type 으로 쓰고 코드는 robot_id/robot_type 을 쓴다.

    시나리오는 사람이 손으로 쓰는 파일이라 짧은 쪽이 맞고, 코드 안에서는
    id/type 이 내장 이름과 겹쳐서 읽기 나빠진다. 여기서 한 번만 옮긴다.
    """
    entry = dict(entry)
    unknown = set(entry) - {
        "id", "type", "battery_percent", "voltage", "system_state", "topics"}
    if unknown:
        raise ValueError(f"robots 에 모르는 키: {', '.join(sorted(unknown))}")
    return Robot(
        robot_id=entry["id"],
        robot_type=entry.get("type", "mobile"),
        battery_percent=entry.get("battery_percent", 90),
        voltage=entry.get("voltage", 11.8),
        system_state=entry.get("system_state", "active"),
        topics=_topics(entry.get("topics") or {}),
    )


def _topics(raw: dict) -> dict:
    """시나리오의 `topics` 를 heartbeat 본문 형태로 옮긴다.

    사람이 쓰는 쪽은 `/scan: 0.1` 로 짧게 적고(나이만), 주기까지 재현할 때만
    `/scan: {age_sec: 0.1, hz: 10}` 로 쓴다. 게이트웨이가 보내는 형태는
    후자 하나뿐이므로 여기서 한 번에 맞춘다.
    """
    result = {}
    for topic, value in raw.items():
        if isinstance(value, dict):
            result[topic] = {"age_sec": value.get("age_sec"),
                             "hz": value.get("hz")}
        else:
            result[topic] = {"age_sec": float(value), "hz": None}
    return result


def load_scenario(path) -> Scenario:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    robots = [_robot(entry) for entry in raw.get("robots", [])]
    steps = []
    for entry in raw.get("steps", []):
        entry = dict(entry)
        steps.append(Step(
            action=entry.pop("action"),
            robot=entry.pop("robot", ""),
            wait=float(entry.pop("wait", 0.0)),
            args=entry,
        ))
    return Scenario(name=raw.get("name", path.stem), robots=robots,
                    steps=steps, path=path)


# --- 정본 대조 ----------------------------------------------------------------

def validate(scenario: Scenario, canon: Canon) -> list[str]:
    """시나리오가 정본과 어긋나는 지점을 모아 돌려준다.

    첫 문제에서 멈추지 않는다. 하나 고치고 다시 돌려서 다음 하나를 보는 것보다
    한 번에 다 보는 쪽이 빠르다.

    의도적으로 오배선을 만드는 시나리오도 있으므로(type_mismatch), 스텝에
    expect_mismatch 를 달면 타입 검사만 건너뛴다. 코드 존재와 level 은 그대로
    본다 — 오배선 시나리오라고 아무 코드나 쓸 수 있는 것은 아니다.
    """
    problems = []
    known_ids = {robot.robot_id for robot in scenario.robots}

    for index, step in enumerate(scenario.steps):
        where = f"step[{index}] {step.action}"

        if step.robot and step.robot not in known_ids:
            problems.append(f"{where}: robots 에 없는 로봇 '{step.robot}'")

        if step.action != "event":
            continue

        code = step.args.get("code")
        if not code:
            problems.append(f"{where}: code 가 없다")
            continue

        if not canon.is_known(code):
            problems.append(f"{where}: 정본에 없는 코드 '{code}'")
            continue

        level = step.args.get("level")
        if level and level != canon.level_of(code):
            problems.append(
                f"{where}: '{code}' 의 level 은 정본에서 "
                f"{canon.level_of(code)} 인데 {level} 로 적혀 있다")

        if step.args.get("expect_mismatch"):
            continue

        robot = scenario.robot(step.robot)
        allowed = canon.allowed_robot_types(code)
        if robot and allowed and robot.robot_type not in allowed:
            problems.append(
                f"{where}: '{code}' 는 {allowed} 전용인데 "
                f"{robot.robot_id} 는 {robot.robot_type} 이다")

    return problems


# --- HTTP ---------------------------------------------------------------------

class HttpError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def request(base_url: str, method: str, path: str, payload=None, timeout=5.0,
            extra_headers=None):
    """의존성 없이 stdlib 만 쓴다.

    하네스를 돌리려고 requirements 를 늘리지 않는다. 로봇을 흉내내는 데 필요한
    건 JSON 을 POST 하는 것뿐이다.

    extra_headers 는 제어 명령의 X-Actor 때문에 있다. 헤더 값을 UTF-8 로
    encode 해서 넘긴다 — 서버가 latin-1 로 디코딩하는 것을 되살리는 쪽이
    backend/app/actor.py 라, 여기서는 브라우저와 같은 바이트를 보내야 그
    경로가 실제로 검증된다.
    """
    data = None
    headers = {}
    for key, value in (extra_headers or {}).items():
        headers[key] = value.encode("utf-8") if isinstance(value, str) else value
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, exc.read().decode("utf-8", "replace")) from None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --- 재생 ---------------------------------------------------------------------

class Harness:
    """시나리오를 실제 백엔드에 재생한다."""

    def __init__(self, scenario: Scenario, canon: Canon, base_url: str,
                 verbose: bool = True):
        self.scenario = scenario
        self.canon = canon
        self.base_url = base_url
        self.verbose = verbose
        # qr_scan 이 돌려준 session_id. 이후 이벤트에 자동으로 달린다.
        self.sessions: dict[str, int] = {}
        # 반복 재생(--loop)의 회차. 0 부터 센다. 마커 회전이 이 값을 읽는다.
        self.iteration = 0
        # 마지막 ingest 응답. E2E 어서션이 읽는다.
        self.last_ingest: dict | None = None
        self._stop = threading.Event()
        self._beats: list[threading.Thread] = []

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    # heartbeat 는 이벤트 큐를 타지 않는 별도 경로다. 끊기면 백엔드가 멀쩡한
    # 로봇에 comm_lost 를 찍고, arming 이 link_unknown 으로 거부된다.
    def _beat(self, robot: Robot) -> None:
        while not self._stop.is_set():
            try:
                request(self.base_url, "POST",
                        f"/robots/{robot.robot_id}/heartbeat",
                        # 토픽은 매번 현재값을 읽는다. topics 액션이 시나리오
                        # 중간에 갈아끼우면 다음 heartbeat 부터 반영된다.
                        {"system_state": robot.system_state,
                         "topics": robot.topics})
            except Exception as exc:      # 진짜 로봇도 실패하면 그냥 버린다
                self.log(f"  heartbeat 실패 {robot.robot_id}: {exc}")
            self._stop.wait(HEARTBEAT_INTERVAL_SEC)

    def start_heartbeats(self) -> None:
        for robot in self.scenario.robots:
            thread = threading.Thread(target=self._beat, args=(robot,), daemon=True)
            thread.start()
            self._beats.append(thread)

    def stop_heartbeats(self) -> None:
        self._stop.set()
        for thread in self._beats:
            thread.join(timeout=2)

    # --- 액션 ---

    def do_battery(self, robot: Robot, args: dict) -> None:
        body = {
            "battery_percent": args.get("percent", robot.battery_percent),
            "voltage": args.get("voltage", robot.voltage),
        }
        request(self.base_url, "POST", f"/robots/{robot.robot_id}/battery", body)
        self.log(f"  배터리 {body['battery_percent']}% ({robot.robot_id})")

    def do_arm(self, robot: Robot, args: dict) -> None:
        request(self.base_url, "POST", f"/robots/{robot.robot_id}/arm")
        self.log(f"  활성화 {robot.robot_id}")

    def _rotate_marker(self, base: int) -> int:
        """반복 재생에서 마커를 돌린다.

        003 의 uq_active_session_marker 는 **활성 세션 사이에서 전역 유니크**다.
        루프가 같은 마커로 다시 스캔했을 때 앞 회차 세션이 아직 안 닫혀 있으면
        409 로 막힌다. 회차마다 어긋나게 두면 그 창이 겹치지 않는다.

        1 회차는 시나리오에 적힌 값 그대로다 — 한 번만 돌리는 기존 사용과
        동작이 같아야 회귀 테스트가 계속 유효하다.

        스키마가 0~49 로 제한하므로(schemas.py) 반드시 그 안에서 돈다.
        """
        if self.iteration == 0:
            return base
        # 2 씩 벌린다. 한 시나리오의 두 로봇(30·31)이 같은 오프셋을 받아도
        # 서로 겹치지 않아야 한다.
        return (base + 2 * (self.iteration % 10)) % 50

    def do_qr_scan(self, robot: Robot, args: dict) -> None:
        body = {"patient_id": args["patient_id"], "robot_id": robot.robot_id}
        if "marker_id" in args:
            body["marker_id"] = self._rotate_marker(args["marker_id"])
        result = request(self.base_url, "POST", "/qr/scan", body)
        self.sessions[robot.robot_id] = result["session_id"]
        self.log(f"  QR 스캔 {args['patient_id']} → session {result['session_id']}")

    def do_event(self, robot: Robot, args: dict) -> None:
        code = args["code"]
        event = {
            "event_id": str(uuid.uuid4()),
            "robot_id": robot.robot_id,
            # session_id 0 은 '세션 없음'. 백엔드가 NULL 로 저장한다.
            "session_id": self.sessions.get(robot.robot_id, 0),
            "occurred_at": now_iso(),
            "level": args.get("level", self.canon.level_of(code)),
            "event_code": code,
            "source_node": SOURCE_NODE,
            "payload": args.get("payload", {}),
        }
        self.last_ingest = request(self.base_url, "POST", "/events", [event])
        self.log(f"  이벤트 {code} ({robot.robot_id})")

    def do_order(self, robot: Robot, args: dict) -> None:
        """관제가 로봇에 명령을 건다. 로봇이 아니라 **사람** 쪽 동작이다.

        하네스는 이미 `arm` 으로 대시보드 역할을 하고 있다 — 그것도 의료진이
        누르는 버튼이다. 개입을 재현하려면 누르는 쪽이 있어야 하고, 그걸
        테스트 코드에 흩어 놓으면 시나리오 파일만 봐서는 무슨 일이 벌어지는지
        알 수 없다.

        actor 를 생략하면 헤더 없이 보낸다. 그건 실수가 아니라 검증 대상이다 —
        서버가 거부하지 않고 익명으로 남기는지(backend/app/actor.py) 확인하는
        경로다.
        """
        body = {
            "command": args["command"],
            "argument": str(args.get("argument", "run")),
        }
        headers = {"X-Actor": args["actor"]} if args.get("actor") else {}
        result = request(
            self.base_url, "POST", f"/robots/{robot.robot_id}/orders", body,
            extra_headers=headers)
        who = args.get("actor") or "익명"
        self.log(f"  명령 {body['command']}({body['argument']}) "
                 f"← {who} ({robot.robot_id}) order={result['order_id']}")

    def do_servos(self, robot: Robot, args: dict) -> None:
        """서보 온도·전류 표본 (§4.4 · 로드맵 11).

        실기에서는 U2D2 로 Dynamixel 을 읽어 보낸다. 과열을 실기로 재현하려면
        팔을 실제로 몇십 분 돌려야 하고, 그때도 원하는 조인트가 원하는 온도로
        올라간다는 보장이 없다.

        여기서 검증하려는 것은 온도계가 아니라 **서버가 임계를 어떻게
        판정하는가** 다 — 조인트별 임계, 히스테리시스, 반복 발행 억제.
        """
        request(self.base_url, "POST", f"/robots/{robot.robot_id}/servos",
                {"servos": args.get("servos") or []})
        hottest = max((s.get("temp_c") or 0) for s in args.get("servos") or [{}])
        self.log(f"  서보 {len(args.get('servos') or [])}개 "
                 f"최고 {hottest}℃ ({robot.robot_id})")

    def do_inventory(self, robot: Robot, args: dict) -> None:
        """형상 보고 — 지금 무슨 커밋·무슨 맵으로 도는가 (§7.2 형상 패널).

        실기에서는 게이트웨이가 /proc 과 git 을 훑고 /map 격자를 해싱해 만든다.
        여기서는 그 결과값만 준다 — 하네스가 검증하려는 것은 수집 방법이 아니라
        **4대의 형상이 갈렸을 때 서버가 그걸 잡아내는가** 이기 때문이다.

        커밋이 갈린 상태를 실기로 만들려면 로봇 한 대에만 다른 브랜치를 배포하고
        재기동해야 한다. 한 줄로 재현되는 쪽이 낫다.
        """
        commit_val = args.get("commit")
        branch_val = args.get("branch", "main")
        map_name_val = args.get("map_name")
        map_hash_val = args.get("map_hash")

        workspace = {
            "path": str(args.get("workspace", "/home/pinky/mingky_care_pro")),
            "commit": str(commit_val) if commit_val is not None else None,
            "branch": str(branch_val) if branch_val is not None else None,
            "dirty": bool(args.get("dirty", False)),
            # 0 이면 서버가 '지금 도는 코드가 아니다' 로 걸러낸다.
            "process_count": int(args.get("process_count", 7)),
        }
        payload = {
            "workspaces": [workspace],
            "node_graph": [],
            "processes": [],
            "map_name": str(map_name_val) if map_name_val is not None else None,
            "map_hash": str(map_hash_val) if map_hash_val is not None else None,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:8]
        request(self.base_url, "POST", f"/robots/{robot.robot_id}/inventory",
                {**payload, "inventory_hash": digest})
        self.log(f"  형상 commit={workspace['commit']} "
                 f"map={payload['map_hash']} ({robot.robot_id})")

    def do_topics(self, robot: Robot, args: dict) -> None:
        """토픽 나이·주기를 갈아끼운다. 실기로는 케이블을 뽑아야 나오는 상태다.

        기존 값에 덮어쓴다. 통째로 갈아치우면 `/scan` 하나를 죽이려고 나머지
        세 토픽을 매번 다시 적어야 하고, 그러면 시나리오가 무엇을 바꾸려는
        것인지 안 보인다.
        """
        merged = dict(robot.topics)
        merged.update(_topics(args.get("set") or {}))
        # 새 dict 를 통째로 건다. heartbeat 스레드가 같은 dict 를 읽는 중이다.
        robot.topics = merged
        self.log(f"  토픽 {sorted(args.get('set') or {})} ({robot.robot_id})")

    _ACTIONS = {
        "battery": do_battery,
        "topics": do_topics,
        "inventory": do_inventory,
        "servos": do_servos,
        "arm": do_arm,
        "qr_scan": do_qr_scan,
        "event": do_event,
        "order": do_order,
    }

    def _open_sessions(self) -> dict:
        """**서버가 아는** 활성 세션 중 이 시나리오의 로봇 것.

        self.sessions 를 믿으면 안 되는 경우가 있어서 따로 묻는다. 프로세스가
        재시작되면 그 기억은 비어 있는데 서버에는 앞 프로세스가 열어 둔 세션이
        그대로 남아 있다. 하네스가 자기 기억만 보면 그 세션을 영원히 못 닫는다.
        """
        try:
            rows = request(self.base_url, "GET", "/sessions/active") or []
        except Exception as exc:
            self.log(f"  활성 세션 조회 실패: {exc}")
            return {}

        ours = {robot.robot_id for robot in self.scenario.robots}
        found = {}
        for row in rows:
            robot_id = row.get("robot_id")
            session_id = row.get("session_id")
            if robot_id in ours and session_id:
                found[robot_id] = session_id
        return found

    def _recover(self) -> None:
        """열려 있는 세션을 닫는다. 반복 재생에서만 부른다.

        회차가 중간에 죽으면 session.ended 까지 못 가고 세션이 열린 채 남는다.
        그 상태로 다음 회차가 arm 을 부르면 `robot busy with session N` 409 가
        나고(routers/robots.py 의 arm_robot), 그 회차도 같은 자리에서 죽는다.
        아무도 손대지 않으면 **한 번의 일시적 실패가 데모를 영구히 세운다.**

        ## 왜 서버에 묻나

        처음에는 self.sessions 만 닫았는데 그것으로는 못 푸는 자리가 있었다.
        실기 배포에서 실제로 걸린 것이 그 자리다 — 서비스를 재시작하자 앞
        프로세스가 열어 둔 세션이 남았고, 새 프로세스의 기억은 비어 있었다.
        게다가 실패 지점이 arm 이라 qr_scan 을 못 지나 기억이 채워지지도
        않는다. 1250 회차를 같은 409 로 헛돌았다.

        그래서 자기 기억이 아니라 **서버의 활성 세션 목록**을 기준으로 닫는다.
        end_reason 은 `aborted` 다 — 정본에 있는 값이고, 완주가 아니었다는
        사실을 타임라인에 남기는 쪽이 completed 로 덮는 것보다 정직하다.
        """
        pending = dict(self._open_sessions())
        # 서버 조회가 실패했을 때를 대비해 자기 기억도 합친다.
        for robot_id, session_id in self.sessions.items():
            if session_id:
                pending.setdefault(robot_id, session_id)

        for robot_id, session_id in list(pending.items()):
            if not session_id:
                continue
            try:
                request(self.base_url, "POST", "/events", [{
                    "event_id": str(uuid.uuid4()),
                    "robot_id": robot_id,
                    "session_id": session_id,
                    "occurred_at": now_iso(),
                    "level": self.canon.level_of("session.ended"),
                    "event_code": "session.ended",
                    "source_node": SOURCE_NODE,
                    "payload": {"end_reason": "aborted"},
                }])
                self.log(f"  정리 — {robot_id} 세션 {session_id} 를 닫았다")
            except Exception as exc:
                self.log(f"  정리 실패 {robot_id}: {exc}")
        self.sessions.clear()

    def _play_once(self) -> None:
        """시나리오 스텝을 한 번 재생한다. heartbeat 는 건드리지 않는다."""
        for step in self.scenario.steps:
            if step.wait:
                time.sleep(step.wait)

            if step.action == "sleep":
                continue

            robot = self.scenario.robot(step.robot)
            if robot is None:
                raise RuntimeError(f"robots 에 없는 로봇: {step.robot}")

            handler = self._ACTIONS.get(step.action)
            if handler is None:
                raise RuntimeError(f"모르는 action: {step.action}")
            handler(self, robot, step.args)

    def run(self, loop: bool = False, loop_delay: float = 10.0,
            max_iterations: int | None = None) -> None:
        """시나리오를 재생한다. loop 면 끝나도 멈추지 않는다.

        ## 왜 반복이 필요한가

        하네스가 끝나면 heartbeat 도 멈추고, 15 초 뒤 백엔드가 그 로봇에
        comm_lost 를 찍는다(HEARTBEAT_OFFLINE_AFTER_SEC). 개발 중에는 그게
        맞는 동작이다 — 진짜 로봇은 안 멈추기 때문이다.

        하지만 실기가 회수된 뒤 상시 데모로 세워 둘 때는 그 전제가 뒤집힌다.
        아무도 안 보고 있어도 대시보드가 살아 있어야 하므로, 여기서 heartbeat
        를 끊지 않고 시나리오만 다시 돈다.

        ## 회차 사이에 죽지 않는다

        한 회차가 실패해도(409·네트워크 등) 다음 회차를 계속 간다. 상시
        데모에서 일시적인 실패 하나로 프로세스가 죽으면 그 뒤로 화면이
        영영 빈 채로 남는다. 대신 무엇이 실패했는지는 남긴다.
        """
        self.log(f"[시나리오] {self.scenario.name}")
        self.start_heartbeats()
        try:
            # 첫 heartbeat 가 도착해야 link_state 가 unknown 을 벗어난다.
            # 그 전에 arm 을 부르면 link_unknown 으로 거부된다.
            time.sleep(1.0)

            if loop:
                # 앞 프로세스가 남긴 세션을 먼저 치운다. 재시작이 곧 복구여야
                # 한다 — 사람이 DB 를 열어 손으로 닫아야 하면 상시 데모가 아니다.
                self._recover()

            consecutive_failures = 0
            while True:
                if loop:
                    self.log(f"[회차 {self.iteration + 1}] {self.scenario.name}")
                    # 앞 회차의 session_id 를 물고 가면 이번 회차 이벤트가
                    # 이미 닫힌 세션에 붙는다. qr_scan 이 곧 다시 채운다.
                    self.sessions.clear()
                try:
                    self._play_once()
                    consecutive_failures = 0
                except Exception as exc:
                    if not loop:
                        raise
                    consecutive_failures += 1
                    self.log(f"[회차 실패] {exc} — 정리하고 다음 회차로 간다")
                    self._recover()

                self.iteration += 1
                if not loop:
                    break
                if max_iterations and self.iteration >= max_iterations:
                    break

                # 세션이 닫히고 화면이 '복귀 완료' 를 보여줄 틈을 준다.
                # 곧바로 다시 스캔하면 완주 상태가 한 프레임도 안 보인다.
                #
                # 계속 실패하는 중이면 간격을 늘린다. 정리로도 안 풀리는 사정이
                # 있을 때(서버가 내려갔다거나) 5초마다 같은 요청을 던지면 로그만
                # 채우고 서버를 두드린다. 실기 배포에서 1250 회차가 그렇게 돌았다.
                delay = loop_delay * min(max(consecutive_failures, 1), BACKOFF_MAX_STEPS)
                if self._stop.wait(delay):
                    break
        finally:
            self.stop_heartbeats()

        self.log("[완료]")


# --- CLI ----------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario", help="시나리오 YAML 경로")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--canon", default=str(DEFAULT_CANON))
    parser.add_argument("--check", action="store_true",
                        help="정본 대조만 하고 끝낸다 (서버 불필요)")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--loop", action="store_true",
                        help="시나리오를 끝나도 계속 반복한다 (상시 데모용)")
    parser.add_argument("--loop-delay", type=float, default=10.0,
                        help="회차 사이 대기 초 (기본 10)")
    parser.add_argument("--max-iterations", type=int, default=None,
                        help="이 회차만큼만 돌고 멈춘다 (테스트용)")
    args = parser.parse_args(argv)

    scenario = load_scenario(args.scenario)
    canon = Canon.load(args.canon)

    problems = validate(scenario, canon)
    if problems:
        print(f"[정본 위반] {scenario.name}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.check:
        print(f"[정본 OK] {scenario.name} — 스텝 {len(scenario.steps)}개")
        return 0

    Harness(scenario, canon, args.base_url, verbose=not args.quiet).run(
        loop=args.loop, loop_delay=args.loop_delay,
        max_iterations=args.max_iterations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
