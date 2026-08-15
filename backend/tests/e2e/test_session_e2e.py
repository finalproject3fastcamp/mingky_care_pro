"""가짜 로봇 4대가 아니라 배관 전체를 통과시키는 테스트.

나머지 테스트는 전부 가짜 커넥션이다. 빠르고 DB 가 필요 없지만, 그래서
**한 번도 통과시켜본 적 없는 경로**가 있다 — heartbeat 로 link_state 를 세우고,
배터리 표본으로 arming 전제조건을 채우고, arm 을 받고, QR 로 세션을 만들고,
이벤트가 session_steps 를 실제로 갱신하는 그 순서다. 그 순서가 깨지면 단위
테스트는 전부 초록인데 로봇이 아무것도 못 한다.

여기서는 진짜 uvicorn 과 진짜 PostgreSQL 을 쓴다. 로봇만 가짜다.

  pytest -m e2e        # DB 환경 변수가 있어야 한다

기본 `pytest` 는 이 파일을 건너뛴다(pytest.ini 의 addopts). DB 없이 도는 것이
단위 잡의 계약이라 섞으면 안 된다.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import asyncpg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_DIR = REPO_ROOT / "tools" / "fake_robot"
SCENARIO_DIR = HARNESS_DIR / "scenarios"

sys.path.insert(0, str(HARNESS_DIR))

import fake_robot  # noqa: E402

from app.config import database_url  # noqa: E402

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    """고정 포트를 쓰지 않는다. 러너에서 무엇이 8000 을 잡고 있을지 모른다."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def backend():
    """진짜 uvicorn 을 띄운다.

    워크플로가 아니라 테스트가 서버를 띄우는 이유는, 로컬에서도 CI 와 똑같이
    한 줄로 돌리기 위해서다. YAML 에 기동 절차가 흩어지면 로컬 재현이 안 되고,
    재현이 안 되는 CI 실패는 고치는 데 몇 배가 든다.
    """
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO_ROOT / "backend",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ},
    )

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            # 단일 인스턴스 가드에 걸린 경우가 가장 흔하다. 로그를 그대로
            # 보여주지 않으면 "왜 안 뜨는지" 를 알 수 없다.
            pytest.fail("백엔드가 기동 중 종료했습니다:\n" + process.stdout.read())
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1):
                break
        except (urllib.error.URLError, ConnectionError, socket.timeout):
            time.sleep(0.3)
    else:
        process.terminate()
        pytest.fail("백엔드가 30초 안에 뜨지 않았습니다")

    yield base_url

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def query(sql: str, *args):
    """어서션용 직접 조회. API 를 통하지 않는다.

    API 로 확인하면 API 버그가 API 로 가려진다. 이벤트가 정말 DB 상태를
    바꿨는지는 DB 에 물어야 한다.
    """
    async def go():
        conn = await asyncpg.connect(database_url())
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(go())


def scalar(sql: str, *args):
    rows = query(sql, *args)
    return rows[0][0] if rows else None


def play(scenario_name: str, base_url: str) -> None:
    scenario = fake_robot.load_scenario(SCENARIO_DIR / scenario_name)
    canon = fake_robot.Canon.load()

    problems = fake_robot.validate(scenario, canon)
    assert problems == [], "\n".join(problems)

    fake_robot.Harness(scenario, canon, base_url, verbose=True).run()


def test_seed_data_is_present(backend):
    """앞선 테스트가 실패했을 때 원인이 시드 누락인지 먼저 가른다."""
    assert scalar("SELECT count(*) FROM patients WHERE patient_id = 'p001'") == 1
    assert scalar(
        "SELECT robot_type FROM robots WHERE robot_id = 'pinky-01'") == "mobile"
    assert scalar(
        "SELECT robot_type FROM robots WHERE robot_id = 'omx-01'") == "manipulator"


def test_a_guidance_session_runs_to_completion(backend):
    """heartbeat → 배터리 → arm → QR → 3단계 → 종료.

    단위 테스트가 한 번도 통과시켜본 적 없는 순서다.
    """
    play("session_complete.yaml", backend)

    session = query("""
        SELECT session_id, patient_id, robot_id, end_reason, ended_at
        FROM guidance_sessions ORDER BY session_id DESC LIMIT 1
    """)[0]

    assert session["patient_id"] == "p001"
    assert session["robot_id"] == "pinky-01"
    assert session["end_reason"] == "completed"
    assert session["ended_at"] is not None

    steps = query("""
        SELECT step_order, visit_name, arrived_at, completed_at, completed_source
        FROM session_steps WHERE session_id = $1 ORDER BY step_order
    """, session["session_id"])

    # 마스터를 조인하지 않고 스냅샷을 복사하므로 3행이 그대로 있어야 한다.
    assert [s["visit_name"] for s in steps] == ["X-ray", "임상병리실", "물리치료실"]
    for step in steps:
        assert step["arrived_at"] is not None, step["visit_name"]
        assert step["completed_at"] is not None, step["visit_name"]
        assert step["completed_source"] == "qr"


def test_events_landed_with_the_session_attached(backend):
    """이벤트가 세션에 붙어야 타임라인이 성립한다.

    session_id 가 NULL 로 들어가면 적재는 성공하고 화면만 빈다 — 조용히
    틀리는 종류라 단위 테스트로는 잘 안 잡힌다.
    """
    session_id = scalar(
        "SELECT session_id FROM guidance_sessions ORDER BY session_id DESC LIMIT 1")

    codes = [row["event_code"] for row in query("""
        SELECT DISTINCT event_code FROM events
        WHERE session_id = $1 AND source_node = 'fake_robot'
    """, session_id)]

    for expected in ("session.started", "nav.goal_succeeded",
                     "session.step_completed", "session.ended"):
        assert expected in codes

    # 미등록 코드 마커가 생겼다면 정본과 코드가 갈라진 것이다.
    assert scalar("""
        SELECT count(*) FROM events
        WHERE event_code = 'system.unknown_event_code'
    """) == 0


def test_mismatched_robot_type_is_recorded_without_touching_state(backend):
    """오배선은 기록하되 판정하지 않는다 (§6.1).

    조제 스테이션이 nav.goal_succeeded 를 보내면 그대로 적용될 경우 팔 하나가
    환자의 안내 단계를 진행시킨다. 실기로는 게이트웨이 오배선이나 robot_id
    오타를 내야 나오는 상황이라 하네스로만 만들 수 있다.
    """
    arrived_before = scalar(
        "SELECT count(*) FROM session_steps WHERE arrived_at IS NOT NULL")

    play("type_mismatch.yaml", backend)

    marker = query("""
        SELECT robot_id, level, payload::text AS payload FROM events
        WHERE event_code = 'system.robot_type_mismatch'
        ORDER BY occurred_at DESC LIMIT 1
    """)[0]

    assert marker["robot_id"] == "omx-01"
    assert marker["level"] == "warning"
    assert "nav.goal_succeeded" in marker["payload"]
    assert "manipulator" in marker["payload"]

    arrived_after = scalar(
        "SELECT count(*) FROM session_steps WHERE arrived_at IS NOT NULL")
    assert arrived_after == arrived_before, "오배선이 상태를 갱신했다"

    # 공통 코드는 팔에서도 통과해야 한다. 여기가 걸리면 §4.3 분류가 틀린 것이다.
    assert scalar("""
        SELECT count(*) FROM events
        WHERE robot_id = 'omx-01' AND event_code = 'robot.comm_restored'
    """) >= 1
