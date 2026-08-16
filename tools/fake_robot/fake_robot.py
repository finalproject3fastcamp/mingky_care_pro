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

    def do_qr_scan(self, robot: Robot, args: dict) -> None:
        body = {"patient_id": args["patient_id"], "robot_id": robot.robot_id}
        if "marker_id" in args:
            body["marker_id"] = args["marker_id"]
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
        "arm": do_arm,
        "qr_scan": do_qr_scan,
        "event": do_event,
        "order": do_order,
    }

    def run(self) -> None:
        self.log(f"[시나리오] {self.scenario.name}")
        self.start_heartbeats()
        try:
            # 첫 heartbeat 가 도착해야 link_state 가 unknown 을 벗어난다.
            # 그 전에 arm 을 부르면 link_unknown 으로 거부된다.
            time.sleep(1.0)

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

    Harness(scenario, canon, args.base_url, verbose=not args.quiet).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
