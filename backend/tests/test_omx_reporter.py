"""OMX 리포터와 pharmacy 워커 연동 — 관제 보고의 계약.

OMX 는 ROS 미사용(domain NULL)이라 게이트웨이가 없어, 조제/포장을 돌리는
pharmacy 워커가 직접 관제로 heartbeat·라이프사이클 이벤트를 보낸다. 여기서
잠그는 것은 카메라도 로봇도 DB 도 없이 확인할 수 있는 계약이다.

  - heartbeat 는 본문 없이(생존 신호만) 올바른 경로로 간다
  - 발행하는 모든 이벤트 코드가 config/event_codes.yaml 에 존재하고 payload
    키가 정본 스키마와 정확히 일치한다
  - **pick 성공/실패는 절대 발행하지 않는다** — ground truth 가 없다
  - `MINGKY_OMX_ROBOT_ID` 가 없으면 아무 것도 나가지 않는다(기존 동작 불변)

HTTP 는 로컬 http.server 로 받아 요청을 그대로 기록한다. 진짜 백엔드도 클라우드도
붙이지 않는다.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

import omx.report.reporter as reporter
from app import pharmacy
from app.schemas import RobotHeartbeatIn, ServoSampleIn

# 정본. 리포터가 발행하는 코드·payload 키를 여기에 대조한다.
_CODES = yaml.safe_load(
    (Path(reporter.__file__).resolve().parents[2] / "config" / "event_codes.yaml")
    .read_text(encoding="utf-8"))


class _Recorder(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.server.posts.append({"path": self.path, "body": raw.decode("utf-8")})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            b'{"received":1,"inserted":1,"duplicates":0,"state_updates":0}')

    def log_message(self, *a):  # 테스트 출력 오염 방지
        pass


@pytest.fixture
def server(monkeypatch):
    """요청을 기록하는 로컬 백엔드. 리포터가 이쪽을 보게 env 를 건다."""
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    httpd.posts = []
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    monkeypatch.setenv("MINGKY_BACKEND_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setenv("MINGKY_OMX_ROBOT_ID", "omx-01")
    monkeypatch.setenv("MINGKY_OMX_HTTP_TIMEOUT", "5")
    yield httpd
    httpd.shutdown()


def _events(server) -> list[dict]:
    """/events 로 온 이벤트를 발생 순서대로 편다."""
    out = []
    for p in server.posts:
        if p["path"] == "/events":
            out.extend(json.loads(p["body"]))
    return out


# ── heartbeat ──────────────────────────────────────────────────────────────
def test_heartbeat_posts_empty_body_to_robot_path(server):
    status = reporter.heartbeat()

    assert status == 200
    assert len(server.posts) == 1
    p = server.posts[0]
    assert p["path"] == "/robots/omx-01/heartbeat"
    # 생존 신호만 — 본문을 실으면 서버 runtime 을 기본값으로 덮어쓴다.
    assert p["body"] == ""


def test_heartbeat_without_robot_id_sends_nothing(server, monkeypatch):
    monkeypatch.delenv("MINGKY_OMX_ROBOT_ID", raising=False)

    assert reporter.heartbeat() is None
    assert server.posts == []


# ── 이벤트 payload 가 정본 스키마와 일치하는가 ─────────────────────────────
_EVENT_CALLS = [
    ("cycle_started", ("job1", "red+green"), "manipulator.cycle_started",
     {"dispense_id": "job1", "medication_id": "red+green"}),
    ("cycle_completed", ("job1", 4200), "manipulator.cycle_completed",
     {"dispense_id": "job1", "duration_ms": 4200}),
    ("cycle_aborted", ("job1", "사용자가 중단했습니다"), "manipulator.cycle_aborted",
     {"dispense_id": "job1", "reason": "사용자가 중단했습니다"}),
    ("policy_loaded", ("1unasy/pill_v3_xy@150000", "1unasy/pill_v3_xy"),
     "manipulator.policy_loaded",
     {"checkpoint_id": "1unasy/pill_v3_xy@150000",
      "dataset_revision": "1unasy/pill_v3_xy"}),
]


@pytest.mark.parametrize("fn_name,args,code,payload", _EVENT_CALLS)
def test_event_matches_canonical_schema(server, fn_name, args, code, payload):
    getattr(reporter, fn_name)(*args)

    events = _events(server)
    assert len(events) == 1
    ev = events[0]

    assert ev["event_code"] == code
    assert ev["robot_id"] == "omx-01"
    assert ev["source_node"] == reporter.SOURCE_NODE
    assert ev["session_id"] == 0

    # 코드가 정본에 존재하고 level 이 일치한다.
    assert code in _CODES, f"{code} 가 event_codes.yaml 에 없다"
    assert ev["level"] == _CODES[code]["level"]

    # payload 키가 정본 스키마와 정확히 일치한다 (누락도 초과도 없다).
    assert set(ev["payload"]) == set(_CODES[code]["payload"])
    assert ev["payload"] == payload


def test_duration_ms_is_int(server):
    # p50/p95 재료라 문자열로 새면 안 된다.
    reporter.cycle_completed("job1", 3999.7)
    ev = _events(server)[0]
    assert ev["payload"]["duration_ms"] == 3999
    assert isinstance(ev["payload"]["duration_ms"], int)


def test_events_without_robot_id_send_nothing(server, monkeypatch):
    monkeypatch.delenv("MINGKY_OMX_ROBOT_ID", raising=False)

    assert reporter.cycle_started("j", "red") is None
    assert reporter.cycle_completed("j", 1) is None
    assert reporter.cycle_aborted("j", "x") is None
    assert reporter.policy_loaded("c", "d") is None
    assert server.posts == []


# ── pick_* 는 절대 발행하지 않는다 ─────────────────────────────────────────
def test_reporter_never_names_pick_codes():
    published = {reporter.CYCLE_STARTED, reporter.CYCLE_COMPLETED,
                 reporter.CYCLE_ABORTED, reporter.POLICY_LOADED}
    assert not any("pick" in c for c in published)

    # pick 코드는 정본에 존재한다 — 그래서 유혹이 있다. 우리에겐 ground truth 가
    # 없으므로 존재해도 발행하지 않는다.
    assert "manipulator.pick_succeeded" in _CODES
    assert "manipulator.pick_failed" in _CODES


# ── pharmacy 워커 연동 ─────────────────────────────────────────────────────
@pytest.fixture
def _fast_sim(monkeypatch):
    """시뮬 워커의 색별 대기(0.25초×16)를 즉시 통과시킨다."""
    async def _instant(*a, **k):
        return
    monkeypatch.setattr(pharmacy.asyncio, "sleep", _instant)


def _run_dispense(monkeypatch, 조합, policy_id="xy"):
    async def _meds():
        return {c: {"이름": "약", "색이름": c} for c in ("red", "green", "yellow")}
    monkeypatch.setattr(pharmacy, "_get_meds", _meds)
    monkeypatch.setattr(pharmacy, "REAL_MODE", False)

    async def _run():
        pharmacy._JOB_LOCK = asyncio.Lock()
        pharmacy._SUBSCRIBERS = set()
        pharmacy._JOB = {"id": "j1", "상태": "조제중", "단계": [], "중단요청": False}
        await pharmacy._dispense_worker("j1", {"조합": 조합}, policy_id)
    asyncio.run(_run())


def test_dispense_worker_reports_truthful_lifecycle(server, monkeypatch, _fast_sim):
    _run_dispense(monkeypatch, ["red", "green"])

    events = _events(server)
    codes = [e["event_code"] for e in events]

    # 정책 로드 → 사이클 시작 → 사이클 완료. 순서까지 진실하다.
    assert codes == ["manipulator.policy_loaded",
                     "manipulator.cycle_started",
                     "manipulator.cycle_completed"]

    # pick_* 도 성공 신호도 없다.
    assert not any("pick" in c for c in codes)
    assert "manipulator.cycle_aborted" not in codes

    started = next(e for e in events if e["event_code"] == "manipulator.cycle_started")
    assert started["payload"] == {"dispense_id": "j1", "medication_id": "red+green"}

    loaded = next(e for e in events if e["event_code"] == "manipulator.policy_loaded")
    assert loaded["payload"]["checkpoint_id"] == "1unasy/pill_v3_xy@150000"
    assert loaded["payload"]["dataset_revision"] == "1unasy/pill_v3_xy"

    done = next(e for e in events if e["event_code"] == "manipulator.cycle_completed")
    assert isinstance(done["payload"]["duration_ms"], int)


def test_dispense_worker_reports_abort_on_stop(server, monkeypatch, _fast_sim):
    async def _meds():
        return {"red": {"이름": "약", "색이름": "red"}}
    monkeypatch.setattr(pharmacy, "_get_meds", _meds)
    monkeypatch.setattr(pharmacy, "REAL_MODE", False)

    async def _run():
        pharmacy._JOB_LOCK = asyncio.Lock()
        pharmacy._SUBSCRIBERS = set()
        # 중단요청을 미리 세워 첫 단계에서 포기 경로로 간다.
        pharmacy._JOB = {"id": "j1", "상태": "조제중", "단계": [], "중단요청": True}
        await pharmacy._dispense_worker("j1", {"조합": ["red"]}, "xy")
    asyncio.run(_run())

    codes = [e["event_code"] for e in _events(server)]
    assert "manipulator.cycle_aborted" in codes
    assert "manipulator.cycle_completed" not in codes
    aborted = next(e for e in _events(server)
                   if e["event_code"] == "manipulator.cycle_aborted")
    assert aborted["level"] == "error"
    assert aborted["payload"]["reason"] == "사용자가 중단했습니다"


def test_worker_is_silent_without_env(server, monkeypatch, _fast_sim):
    # env 미설정이면 기존 동작 그대로 — 아무 것도 관제로 나가지 않는다.
    monkeypatch.delenv("MINGKY_OMX_ROBOT_ID", raising=False)
    _run_dispense(monkeypatch, ["red", "green"])
    assert server.posts == []


# ── 자원(CPU) ──────────────────────────────────────────────────────────────
def test_cpu_total_pct_from_deltas():
    # idle 이 50, total 이 200 만큼 늘면 유휴 25% → 사용률 75%.
    prev = (1000, 4000)
    cur = (1050, 4200)
    assert reporter.cpu_total_pct(prev, cur) == 75.0


def test_cpu_total_pct_needs_two_samples():
    assert reporter.cpu_total_pct(None, (1, 2)) is None
    # 델타가 0 이면(같은 표본) 나눗셈이 성립하지 않는다.
    assert reporter.cpu_total_pct((10, 20), (10, 20)) is None


def test_read_proc_stat_is_pair_or_none():
    stat = reporter.read_proc_stat()
    # 리눅스면 (idle, total), 아니면 None — 둘 다 정상이다.
    assert stat is None or (isinstance(stat, tuple) and len(stat) == 2)


def test_resource_heartbeat_body_matches_schema(server):
    # 자원을 실은 heartbeat 는 로봇 경로로 가고, 본문이 RobotHeartbeatIn 과
    # 일치한다. queue_pending 등 나머지는 생략한다.
    status = reporter.heartbeat(body={"cpu_total_pct": 42.5})

    assert status == 200
    p = server.posts[0]
    assert p["path"] == "/robots/omx-01/heartbeat"
    body = json.loads(p["body"])
    assert body == {"cpu_total_pct": 42.5}
    # 스키마가 이 본문을 받아들이고, 자원 필드가 그대로 실린다.
    parsed = RobotHeartbeatIn.model_validate(body)
    assert parsed.cpu_total_pct == 42.5


def test_empty_body_heartbeat_still_sends_no_body(server):
    # body 가 없으면 순수 생존 핑 — 본문을 붙이지 않는다(기존 계약 불변).
    reporter.heartbeat(body=None)
    reporter.heartbeat(body={})
    assert [p["body"] for p in server.posts] == ["", ""]


# ── 서보(온도·전류) ─────────────────────────────────────────────────────────
_FAKE_SERVOS = [
    {"joint": "shoulder_lift", "temp_c": 41.0, "hardware_error": 0,
     "voltage_v": 11.9},
    {"joint": "gripper", "temp_c": 58.0, "hardware_error": 0, "voltage_v": 12.0},
]


def test_send_servos_body_matches_schema(server):
    status = reporter.send_servos(_FAKE_SERVOS)

    assert status == 200
    p = server.posts[0]
    assert p["path"] == "/robots/omx-01/servos"
    body = json.loads(p["body"])
    # 스키마가 payload 를 받아들인다 (joint·temp_c·hardware_error·voltage_v).
    parsed = ServoSampleIn.model_validate(body)
    assert [s.joint for s in parsed.servos] == ["shoulder_lift", "gripper"]
    assert parsed.servos[0].temp_c == 41.0


def test_send_servos_without_readings_sends_nothing(server):
    assert reporter.send_servos([]) is None
    assert server.posts == []


class _RunnerState(BaseHTTPRequestHandler):
    """조제/포장 상태를 흉내 내는 로컬 러너. busy 클래스 속성으로 상태를 정한다."""

    busy = False

    def do_GET(self):  # noqa: N802
        state = {"상태": "진행" if self.busy else "대기"}
        raw = json.dumps(state, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def runner(monkeypatch):
    """리포터가 idle 을 확인하는 로컬 러너 목."""
    _RunnerState.busy = False
    httpd = HTTPServer(("127.0.0.1", 0), _RunnerState)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    monkeypatch.setenv("MINGKY_RUNNER_PORT", str(httpd.server_address[1]))
    yield httpd
    httpd.shutdown()


def test_report_servos_skips_while_dispensing(server, runner, monkeypatch):
    # 조제 중에는 시리얼 충돌을 피하려 서보를 읽지도, 보내지도 않는다.
    _RunnerState.busy = True
    called = []
    monkeypatch.setattr(
        reporter, "read_servos_via_subprocess",
        lambda: called.append(1) or _FAKE_SERVOS)

    assert reporter.report_servos() is None
    assert called == []            # 읽기 자체를 시도하지 않는다
    assert server.posts == []      # /servos 로 아무 것도 안 나간다


def test_report_servos_sends_when_idle(server, runner, monkeypatch):
    _RunnerState.busy = False
    monkeypatch.setattr(
        reporter, "read_servos_via_subprocess", lambda: _FAKE_SERVOS)

    assert reporter.report_servos() == 200
    assert len(server.posts) == 1
    assert server.posts[0]["path"] == "/robots/omx-01/servos"


def test_runner_idle_false_when_runner_unreachable(server, monkeypatch):
    # 러너에 닿지 못하면 상태를 모르므로 안전하게 유휴가 아니라고 본다.
    monkeypatch.setenv("MINGKY_RUNNER_PORT", "1")  # 아무도 안 듣는 포트
    monkeypatch.setenv("MINGKY_OMX_HTTP_TIMEOUT", "1")
    assert reporter._runner_idle() is False
