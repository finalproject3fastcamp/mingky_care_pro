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
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
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
from app.control_audit import INTERVENTION_ACTIONS  # noqa: E402

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    """고정 포트를 쓰지 않는다. 러너에서 무엇이 8000 을 잡고 있을지 모른다."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _WebhookReceiver(http.server.BaseHTTPRequestHandler):
    """알림 웹훅을 받는 자리. Slack 대신 여기로 쏜다 (§8.4)."""

    received: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            _WebhookReceiver.received.append(json.loads(body))
        except json.JSONDecodeError:
            _WebhookReceiver.received.append({"raw": body})
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        """기본 구현이 stderr 로 요청마다 한 줄씩 찍는다. 테스트 로그를 덮는다."""


@pytest.fixture(scope="module")
def webhook():
    """실제 채널 대신 로컬 수신기를 세운다.

    알림은 '보냈다' 까지가 기능이다. 선별 로직은 단위 테스트가 잠그지만,
    ingest → notify → HTTP 배관이 실제로 이어져 있는지는 여기서만 확인된다.
    """
    _WebhookReceiver.received.clear()
    server = http.server.HTTPServer(("127.0.0.1", 0), _WebhookReceiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/hook"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def backend(webhook):
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
        # 알림은 기본이 '꺼짐' 이다(ALERT_WEBHOOK_URL 없음). 여기서만 켜서
        # 로컬 수신기로 돌린다 — 개발·CI 가 실수로 실제 채널에 쏘지 않는다.
        env={**os.environ, "ALERT_WEBHOOK_URL": webhook,
             "ALERT_WEBHOOK_KIND": "json"},
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


# §1.1 의 개입 판정. PR 3-2 의 SLO API 가 이 모양을 그대로 쓴다.
#
# 여기서 한 번 실제 DB 로 통과시켜 두면, 나중에 API 가 다른 답을 낼 때
# 원인이 SQL 인지 집계 로직인지 가를 수 있다.
_INTERVENED_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM control_audit
        WHERE session_id = $1 AND action = ANY($2::text[])
    )
"""


def test_completed_session_with_an_intervention_is_not_a_clean_run(backend):
    """완주가 성공을 뜻하지 않는다 (§1.1).

    end_reason 만 보면 이 세션은 성공이다. 3단계를 다 돌고 completed 로
    끝났다. 그런데 1단계 도착 직후에 사람이 로컬라이제이션 재요청을 넣었다 —
    로봇이 스스로 못 한 것을 사람이 대신했다는 뜻이므로 SLO 는 실패로 센다.

    감사 로그가 없으면 이 구분 자체가 불가능하다. 그래서 로드맵 4번이 5번의
    선행 조건이다.
    """
    play("session_with_intervention.yaml", backend)

    session = query("""
        SELECT session_id, end_reason FROM guidance_sessions
        ORDER BY session_id DESC LIMIT 1
    """)[0]

    # 완주는 했다. 여기까지만 보면 성공이다.
    assert session["end_reason"] == "completed"

    intervention = query("""
        SELECT action, argument, actor, actor_source, order_id
        FROM control_audit
        WHERE session_id = $1 ORDER BY audit_id
    """, session["session_id"])

    assert [row["action"] for row in intervention] == ["localize"]
    # 헤더가 latin-1 로 디코딩되는 구간을 지나 이름이 살아 있어야 한다.
    assert intervention[0]["actor"] == "정민경"
    assert intervention[0]["actor_source"] == "header"
    assert intervention[0]["argument"] == "run"
    # 감사 행이 명령을 가리킨다. put() 보다 먼저 기록하면서도 잃지 않은 값이다.
    assert intervention[0]["order_id"] is not None

    assert scalar(_INTERVENED_SQL,
                  session["session_id"], sorted(INTERVENTION_ACTIONS)) is True


def test_a_clean_session_is_still_judged_clean(backend):
    """개입 판정이 모든 세션을 실패로 만들지 않는지 본다.

    한쪽만 확인하면 "항상 True 를 돌려주는 쿼리" 도 통과한다. 앞선 완주
    세션이 여전히 깨끗하게 나와야 판정에 의미가 있다.
    """
    clean = scalar("""
        SELECT session_id FROM guidance_sessions
        WHERE end_reason = 'completed' ORDER BY session_id LIMIT 1
    """)

    assert scalar(_INTERVENED_SQL, clean, sorted(INTERVENTION_ACTIONS)) is False


def test_order_without_the_actor_header_is_recorded_anonymously(backend):
    """헤더가 없어도 거부하지 않는다. 익명으로 남기고 드러낸다.

    422 로 막으면 감사 문제가 가용성 문제가 된다 — 프론트 버그 하나로
    조작자가 명령을 못 내리게 된다. 대신 익명 행이 집계 가능해야 한다.

    그리고 이 goto 는 정상 주행이라 기록은 되지만 개입 판정에는 안 잡혀야
    한다. "기록은 넓게, 판정은 좁게" 가 실제로 그렇게 도는지 여기서 본다.
    """
    anonymous = query("""
        SELECT action, actor, actor_source, session_id FROM control_audit
        WHERE action = 'goto' ORDER BY audit_id DESC LIMIT 1
    """)[0]

    assert anonymous["actor"] is None
    assert anonymous["actor_source"] == "absent"
    # 세션이 끝난 뒤 내린 명령이라 붙을 세션이 없다.
    assert anonymous["session_id"] is None
    assert "goto" not in INTERVENTION_ACTIONS

    # 익명 비율을 세는 질의. fleet 탭이 이 숫자를 띄운다.
    counts = query("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE actor IS NULL) AS anonymous
        FROM control_audit
    """)[0]
    assert counts["anonymous"] >= 1
    assert counts["total"] > counts["anonymous"]


def get_json(base_url: str, path: str):
    with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as response:
        return json.loads(response.read())


def test_slo_api_counts_the_intervened_session_as_a_failure(backend):
    """§1.1 판정이 API 를 통해서도 같은 답을 낸다.

    앞선 테스트들이 만든 세션은 둘 다 completed 다. 하나는 깨끗하고 하나는
    localize 개입이 끼어 있으므로 완주율은 50% 여야 한다.

    단위 테스트는 judge() 에 손으로 만든 행을 먹인다. 여기서는 WINDOW_SQL 이
    진짜 DB 에서 그 행을 제대로 만들어내는지를 본다 — 실패하면 원인이 산식이
    아니라 쿼리라는 뜻이다.
    """
    result = get_json(backend, "/slo/completion")

    assert result["sessions_judged"] == 2
    assert result["success"] == 1
    assert result["failure"] == 1
    assert result["completion_rate"] == 0.5
    # 표본이 창을 못 채웠다. 화면이 이 완주율을 그대로 믿으면 안 된다.
    assert result["sample_complete"] is False
    assert result["budget_total"] == 5
    assert result["budget_exhausted"] is False

    failed = result["failed_sessions"]
    assert len(failed) == 1
    # 완주는 했다. 실패 사유는 종료가 아니라 개입 하나뿐이다.
    assert failed[0]["end_reason"] == "completed"
    assert failed[0]["failures"] == ["operator_order"]


def test_slo_window_can_be_narrowed_for_investigation(backend):
    """창을 줄이면 예산도 같이 줄어든다.

    window=1 이면 가장 최근에 끝난 세션 하나만 본다. 그 세션이 개입이 낀
    쪽이므로 완주율 0% 이고, 예산 0 을 이미 넘겨 소진이다.
    """
    result = get_json(backend, "/slo/completion?window=1")

    assert result["sessions_judged"] == 1
    assert result["completion_rate"] == 0.0
    assert result["budget_total"] == 0
    assert result["budget_exhausted"] is True


def test_control_audit_api_exposes_the_anonymous_share(backend):
    """익명 비율은 목록이 아니라 전체에 대한 값이어야 한다.

    "최근 20건 중 몇 건" 으로 세면 개입이 뜸한 날에는 비율이 요동친다.
    누적으로 봐야 클라이언트가 헤더를 빠뜨리기 시작한 것을 알아챌 수 있다.
    """
    page = get_json(backend, "/control-audit?limit=1")

    assert page["total"] == 2
    assert page["anonymous"] == 1
    # limit 은 목록만 자른다. 집계까지 잘리면 위 두 숫자가 1 이 된다.
    assert len(page["items"]) == 1

    full = get_json(backend, "/control-audit")
    actions = {item["action"]: item for item in full["items"]}

    assert actions["localize"]["actor"] == "정민경"
    assert actions["localize"]["intervention"] is True
    # goto 는 기록되지만 판정 대상이 아니다. 프론트가 이 판단을 다시 하지
    # 않도록 백엔드가 붙여 보낸다.
    assert actions["goto"]["actor"] is None
    assert actions["goto"]["actor_source"] == "absent"
    assert actions["goto"]["intervention"] is False


def test_dispense_cycles_land_without_a_session(backend):
    """조제 이벤트가 세션 없이 적재된다 (§6.2).

    팔은 QR 도 arming 도 거치지 않는다. session_id 없이 이벤트가 들어오는
    유일한 정상 경로이므로, 여기가 깨지면 NOT NULL 제약이나 판정 순서가
    mobile 을 전제하고 있다는 뜻이다.
    """
    play("manipulator_cycle.yaml", backend)
    play("manipulator_cycle_aborted.yaml", backend)

    rows = query("""
        SELECT event_code, level, robot_id, session_id, payload::text AS payload
        FROM events
        WHERE event_code LIKE 'manipulator.%'
        ORDER BY occurred_at
    """)
    codes = [row["event_code"] for row in rows]

    assert "manipulator.cycle_completed" in codes
    assert "manipulator.cycle_aborted" in codes
    # 팔은 세션에 딸리지 않는다. 0 이 아니라 NULL 로 저장돼야 인덱스와
    # FK 가 의도대로 동작한다.
    assert all(row["session_id"] is None for row in rows)
    assert {row["robot_id"] for row in rows} == {"omx-01", "omx-02"}

    levels = {row["event_code"]: row["level"] for row in rows}
    # 확률적 실패가 error 로 올라가면 §8.4 알림이 무의미해진다 (§4.4).
    assert levels["manipulator.pick_failed"] == "warning"
    assert levels["manipulator.cycle_aborted"] == "error"
    assert levels["manipulator.servo_fault"] == "error"

    # 정본과 갈라졌으면 마커가 남는다. 오배선도 미등록도 없어야 한다.
    assert scalar("""
        SELECT count(*) FROM events
        WHERE event_code IN ('system.unknown_event_code',
                             'system.robot_type_mismatch')
          AND robot_id IN ('omx-01', 'omx-02')
          AND payload->>'received_code' LIKE 'manipulator.%'
    """) == 0


def test_dispense_events_do_not_move_the_slo(backend):
    """조제는 안내 세션이 아니다.

    §1.1 판정은 guidance_sessions 를 기준으로 돌고 개입 판정에 쓰는 코드는
    robot.estop_engaged · robot.paused 뿐이다. 팔이 사이클을 포기해도(error)
    환자 안내 완주율은 움직이면 안 된다 — 여기가 흔들리면 팔 하나가 SLO 를
    끌어내리고 판정이 사람 손을 떠난다.
    """
    result = get_json(backend, "/slo/completion")

    assert result["sessions_judged"] == 2
    assert result["completion_rate"] == 0.5


def test_robot_list_splits_by_type(backend):
    """GET /robots 가 타입별로 다른 모양을 돌려준다 (§7.3).

    단위 테스트는 dispense.summarize 에 손으로 만든 행을 먹인다. 여기서는
    DETAIL_SQL 이 진짜 DB 에서 그 행을 만들어내는지를 본다 — 실패하면 원인이
    접기 규칙이 아니라 쿼리다.

    앞선 두 테스트가 omx-01 완주 1건과 omx-02 포기 1건을 이미 만들어 뒀다.
    여기서 재시도 사이클을 하나 더 얹어 pick 성공률이 1.0 에서 내려오게 한다.
    """
    play("manipulator_pick_retry.yaml", backend)

    robots = {robot["robot_id"]: robot for robot in get_json(backend, "/robots")}

    # 팔에 없는 것은 null 로 채우지 않고 아예 안 보낸다. 이게 프론트의
    # discriminated union 이 "팔에 배터리 카드를 렌더" 를 컴파일 타임에
    # 막을 수 있는 근거다.
    assert "battery_percent" not in robots["omx-01"]
    assert "armed_at" not in robots["omx-01"]
    assert "detail" not in robots["pinky-01"]
    assert robots["pinky-01"]["battery_percent"] is not None

    detail = robots["omx-01"]["detail"]
    assert detail["cycles_completed"] == 2
    assert detail["cycles_aborted"] == 0
    # 완주 2건 안에서 pick 3성공 1실패. 재시도가 끼었어도 사이클은 완주다.
    assert detail["pick_succeeded"] == 3
    assert detail["pick_failed"] == 1
    assert detail["pick_success_rate"] == 0.75
    assert detail["pick_retried"] == 1
    assert detail["sample_complete"] is False
    # 어느 체크포인트로 돈 사이클인지가 실패를 조사할 때의 첫 질문이다 (§4.4).
    assert detail["policy_checkpoint_id"] == "act_omx_020000"

    aborted = robots["omx-02"]["detail"]
    assert aborted["cycles_aborted"] == 1
    assert aborted["pick_success_rate"] == 0.0
    assert aborted["homing_required"] is True
    assert aborted["last_servo_fault"]["joint"] == "shoulder_lift"
    # 팔 하나의 결함이 다른 팔의 지표에 새면 안 된다.
    assert robots["omx-01"]["detail"]["last_servo_fault"] is None


def test_dead_lidar_is_caught_while_the_unit_still_looks_healthy(backend):
    """유닛은 active 인데 /scan 이 안 나오는 상태 (§7.2 · 로드맵 9).

    이 장애 모드가 여기까지 오지 못하면 관제는 계속 정상으로 보인다 —
    systemd 도 heartbeat 도 초록이기 때문이다. 실기로 재현하려면 사람이 로봇
    앞에서 라이다 USB 를 뽑아야 하므로 하네스가 유일한 재현 수단이다.

    판정 전체가 서버 안에서 일어나는지도 같이 본다. 시나리오는 이벤트를 하나도
    쏘지 않고 heartbeat 만 보낸다.
    """
    play("topic_stale.yaml", backend)

    events = query("""
        SELECT event_code, level, payload::text AS payload FROM events
        WHERE event_code LIKE 'robot.topic_%' AND robot_id = 'pinky-01'
        ORDER BY occurred_at
    """)
    codes = [row["event_code"] for row in events]

    # 끊김과 복구가 한 번씩. 매 주기 반복 발행하면 타임라인이 한 사건으로 덮인다.
    assert codes == ["robot.topic_stale", "robot.topic_restored"], codes
    assert events[0]["level"] == "error"
    assert '"/scan"' in events[0]["payload"]

    # /cmd_vel 은 900초째 비어 있지만 서 있는 로봇의 정상 상태다. 이게 알림에
    # 섞이면 대기 중인 로봇 2대가 항상 경고가 되고 진짜 두절이 그 속에 묻힌다.
    assert "cmd_vel" not in events[0]["payload"]

    # 발행 측이 서버라는 사실이 타임라인에 남아야 한다. 로봇이 낸 것으로 보이면
    # 조사할 때 게이트웨이 로그부터 뒤지게 된다.
    assert scalar("""
        SELECT source_node FROM events
        WHERE event_code = 'robot.topic_stale' LIMIT 1
    """) == "backend.topic_watch"


def test_topic_judgement_is_served_with_the_robot_list(backend):
    """화면이 임계를 다시 들고 있지 않다는 계약 (§7.2).

    시나리오가 끝난 뒤 마지막 heartbeat 는 정상값이므로 /scan 은 fresh 다.
    상태가 응답에 실려 오지 않으면 프론트가 자기 숫자로 색을 칠하게 되고,
    그때부터 config/topic_watch.yaml 을 고쳐도 화면이 안 바뀐다.
    """
    robots = {robot["robot_id"]: robot for robot in get_json(backend, "/robots")}
    topics = {t["topic"]: t for t in robots["pinky-01"]["topics"]}

    assert topics["/scan"]["state"] == "fresh"
    assert topics["/scan"]["expected_hz"] == 10
    # 상시 발행이 아닌 토픽은 쉬는 것이지 고장이 아니다.
    assert topics["/cmd_vel"]["state"] == "idle"
    assert topics["/cmd_vel"]["always_on"] is False
    # 팔에는 토픽 축 자체가 없다. null 로 채워 보내면 화면이 '감시 중인데
    # 아무것도 안 온다' 로 읽는다.
    assert "topics" not in robots["omx-01"]


def test_split_fleet_configuration_is_caught(backend):
    """4대가 서로 다른 형상으로 도는 상태 (§7.2 · 로드맵 10).

    §9.2 표의 "로봇 4대 SHA 동일 — 확인 안 함" 이 이 경로로 확인으로 바뀐다.
    실기로 만들려면 로봇 한 대에만 다른 브랜치를 배포하고 재기동해야 한다.
    """
    play("fleet_config_split.yaml", backend)

    config = get_json(backend, "/fleet/config")
    robots = {robot["robot_id"]: robot for robot in config["robots"]}

    # 커밋 안 된 변경은 커밋 해시만으로 재현이 불가능하다. 숨기면 안 된다.
    assert robots["pinky-02"]["dirty"] is True
    assert robots["pinky-02"]["branch"] == "hotfix/nav-timeout"

    found = {m["axis"]: m for m in config["mismatches"]}

    # "갈렸다" 만으로는 무엇을 되돌려야 할지 모른다. 몇 대 몇인지가 필요하다.
    assert found["commit"]["values"] == {
        "a1b2c3d4e5f6": ["pinky-01"], "9f3e11c2ab77": ["pinky-02"]}
    # 이름은 둘 다 yun_map_highres_clean 이다. 이름으로 비교했으면 못 잡는다.
    assert set(found["map"]["values"]) == {"7c9f1a2b3c4d", "ffffffffffff"}
    assert robots["pinky-01"]["map_name"] == robots["pinky-02"]["map_name"]

    # 팔은 코드 형상을 보고하지 않는다(게이트웨이가 아직 없다). 그건 불일치가
    # 아니라 '모른다' 다 — 여기서 세면 패널이 영구히 빨갛다.
    assert robots["omx-01"]["commit"] is None
    assert "policy" not in found
    # 앞선 시나리오들이 두 팔에 같은 체크포인트를 실어 뒀다.
    assert robots["omx-01"]["policy_checkpoint_id"] == "act_omx_020000"


def test_servo_telemetry_lands_and_crosses_thresholds(backend):
    """서보 온도·전류 수집과 임계 판정 (§4.4 · 로드맵 11).

    실기로 과열을 재현하려면 팔을 몇십 분 돌려야 하고, 그때도 원하는 조인트가
    원하는 온도로 올라간다는 보장이 없다. 여기서 보려는 것은 온도계가 아니라
    서버의 판정이다 — 조인트별 임계, 히스테리시스, 반복 발행 억제.
    """
    play("servo_overheat.yaml", backend)

    codes = [row["event_code"] for row in query("""
        SELECT event_code FROM events
        WHERE event_code LIKE 'manipulator.servo_%' AND robot_id = 'omx-01'
        ORDER BY occurred_at
    """)]

    # 과열 한 번, 해제 한 번. 계속 뜨거운 동안 반복 발행하면 알림이 잡음이 된다.
    assert codes == ["manipulator.servo_overheat", "manipulator.servo_cooled"], codes

    # 그리퍼는 66℃ 까지 갔지만 자기 임계(70) 아래다. 공통선을 쓰면 정상
    # 동작이 매 사이클 경고로 뜬다.
    overheated = query("""
        SELECT payload::text AS payload, level FROM events
        WHERE event_code = 'manipulator.servo_overheat' LIMIT 1
    """)[0]
    assert "shoulder_lift" in overheated["payload"]
    # 팔은 아직 돌고 있다. error 는 이미 멈춘 상태에만 쓴다 (§8.4).
    assert overheated["level"] == "warning"

    # 표본은 events 가 아니라 추이 로그에 쌓인다. 1분마다 찍히는 온도를
    # events 에 넣으면 타임라인 필터가 쓸모없어진다.
    assert scalar("""
        SELECT count(*) FROM robot_servo_log WHERE robot_id = 'omx-01'
    """) >= 10


def test_servo_health_api_separates_hot_from_climbing(backend):
    """지금 뜨거운 것과 오르는 중인 것은 다른 사실이다 (§4.4)."""
    health = get_json(backend, "/robots/omx-01/servos")
    servos = {servo["joint"]: servo for servo in health["servos"]}

    # 에러 비트는 온도와 무관하게 fault 다. 서보가 이미 토크를 끊었을 수 있다.
    assert servos["wrist"]["state"] == "fault"
    assert servos["wrist"]["hardware_error"] == 32
    # 나쁜 것이 맨 앞에 온다. 화면이 다시 정렬하지 않는다.
    assert health["servos"][0]["state"] == "fault"

    # 시나리오가 몇 초 안에 끝나므로 추세를 낼 시간 폭이 없다. 그때 기울기를
    # 만들어내면 안 된다 — 틀린 추세는 없는 추세보다 나쁘다.
    assert servos["shoulder_lift"]["slope_c_per_hour"] is None
    assert servos["shoulder_lift"]["rising"] is False


def test_mobile_robots_cannot_report_servos(backend):
    """오배선을 그대로 쌓으면 서보가 없는 로봇의 행이 추이 판정에 섞인다.

    이벤트(§6.1)와 다른 판단이다 — 저쪽은 기록을 잃으면 사건이 사라지지만
    이건 주기 표본이라 하나 버려도 다음이 온다.
    """
    request = urllib.request.Request(
        f"{backend}/robots/pinky-01/servos",
        data=json.dumps({"servos": [{"joint": "wrist", "temp_c": 40}]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")

    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)

    assert caught.value.code == 409
    assert scalar(
        "SELECT count(*) FROM robot_servo_log WHERE robot_id = 'pinky-01'") == 0


def test_serious_events_leave_the_screen(backend):
    """심각한 사건이 대시보드 밖으로 나간다 (§8.4 · 로드맵 12).

    원칙 4 — 야간에 화면을 보는 사람이 없으면 관측한 것이 아니다. 선별 규칙은
    단위 테스트가 잠그고, 여기서 보는 것은 ingest → notify → HTTP 배관이
    실제로 이어져 있는가다.

    앞선 시나리오들이 이미 사이클 포기·서보 과열·토픽 두절을 만들어 뒀다.
    """
    # 전송은 태스크로 던져진다. 적재 응답을 기다린 것만으로는 아직 안 왔을 수 있다.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not _WebhookReceiver.received:
        time.sleep(0.3)

    sent = list(_WebhookReceiver.received)
    codes = {alert["event_code"] for alert in sent}

    assert sent, "알림이 한 건도 나가지 않았습니다"
    # 정본(config/alert_routes.yaml)에 있는 코드만 나가야 한다.
    assert codes <= {
        "fire.detected", "robot.estop_engaged", "robot.comm_lost",
        "robot.battery_low", "manipulator.servo_fault",
        "manipulator.servo_overheat", "manipulator.cycle_aborted",
        "robot.topic_stale", "robot.comm_restored",
    }, codes

    # 확률적 실패는 절대 나가지 않는다 (§4.4). 이게 새면 하루에 수십 번 울리고
    # 그 순간부터 아무도 이 채널을 안 본다.
    assert "manipulator.pick_failed" not in codes

    # 코드 이름만 보내면 받는 사람이 저장소를 열어야 뜻을 안다.
    first = sent[0]
    assert first["robot_id"]
    assert first["tier"] in ("page", "notify")
    assert first["text"]


def test_the_same_fact_is_not_announced_twice(backend):
    """두절이 흔들리는 로봇 하나가 채널을 도배하면 안 된다.

    servo_overheat.yaml 은 어깨가 임계를 넘은 뒤에도 표본을 계속 올린다.
    서버가 반복 발행을 막고 있으므로 알림은 한 건이어야 한다.
    """
    per_key = {}
    for alert in _WebhookReceiver.received:
        key = (alert["robot_id"], alert["event_code"])
        per_key[key] = per_key.get(key, 0) + 1

    repeated = {key: count for key, count in per_key.items() if count > 1}
    assert repeated == {}, repeated


def test_two_pinkies_run_concurrent_sessions_kept_independent(backend):
    """핑키 2대가 동시에 안내를 돌려도 관제가 두 세션을 독립적으로 유지한다.

    백엔드 구조상 멀티 로봇은 가능했지만(로봇당 활성 세션 1개), 두 대를 실제로
    인터리브해서 돌려본 시나리오가 없었다. 하네스가 세션을 로봇별로 추적하지
    않으면 pinky-02 의 스캔이 pinky-01 의 session_id 를 덮어써서, 이후 pinky-01
    이벤트가 엉뚱한 세션에 붙는다 — 여기서 그 교차 오염이 없음을 잠근다.

    이 파일의 앞선 테스트가 만든 완주 세션 수(SLO 절대값)를 흔들지 않도록
    맨 끝에 둔다. 여기서는 델타로만 판정한다.
    """
    before = get_json(backend, "/slo/completion")

    play("two_pinky_concurrent.yaml", backend)

    # 세션 2개가 각각 다른 로봇으로, 서로 다른 session_id 로 생겼다.
    sessions = query("""
        SELECT session_id, patient_id, robot_id, end_reason, ended_at
        FROM guidance_sessions
        WHERE robot_id IN ('pinky-01', 'pinky-02')
          AND patient_id IN ('p001', 'p002')
        ORDER BY session_id DESC LIMIT 2
    """)
    by_robot = {row["robot_id"]: row for row in sessions}

    assert set(by_robot) == {"pinky-01", "pinky-02"}, sessions
    one, two = by_robot["pinky-01"], by_robot["pinky-02"]
    assert one["session_id"] != two["session_id"]
    assert one["patient_id"] == "p001"
    assert two["patient_id"] == "p002"

    # 둘 다 completed 로 정상 종료.
    for row in (one, two):
        assert row["end_reason"] == "completed", row["robot_id"]
        assert row["ended_at"] is not None, row["robot_id"]

    # 각자 단계 수만큼(p001=3, p002=2) session_steps 가 모두 완료됐다.
    expected_steps = {
        one["session_id"]: ["X-ray", "임상병리실", "물리치료실"],
        two["session_id"]: ["X-ray", "CT"],
    }
    for session_id, names in expected_steps.items():
        steps = query("""
            SELECT step_order, visit_name, arrived_at, completed_at, completed_source
            FROM session_steps WHERE session_id = $1 ORDER BY step_order
        """, session_id)
        assert [s["visit_name"] for s in steps] == names, session_id
        for step in steps:
            assert step["arrived_at"] is not None, (session_id, step["visit_name"])
            assert step["completed_at"] is not None, (session_id, step["visit_name"])
            assert step["completed_source"] == "qr", (session_id, step["visit_name"])

    # 교차 오염이 없다. 각 세션에 붙은 이벤트의 robot_id 는 한 대뿐이어야 한다.
    for robot_id, row in by_robot.items():
        landed = query("""
            SELECT DISTINCT robot_id FROM events
            WHERE session_id = $1 AND source_node = 'fake_robot'
        """, row["session_id"])
        assert [r["robot_id"] for r in landed] == [robot_id], (robot_id, landed)

    # 두 세션 모두 자기 마커로 뜬 session.started 를 갖는다(스캔이 안 섞였다).
    marker_of = {
        row["session_id"]: scalar("""
            SELECT payload->>'marker_id' FROM events
            WHERE session_id = $1 AND event_code = 'session.started'
            LIMIT 1
        """, row["session_id"])
        for row in (one, two)
    }
    assert marker_of[one["session_id"]] == "30"
    assert marker_of[two["session_id"]] == "31"

    # SLO 판정이 두 세션을 모두 성공으로 포함한다. 앞선 세션 수에 무관하도록
    # 델타로 본다 — 판정 세션 +2, 성공 +2, 실패는 그대로.
    after = get_json(backend, "/slo/completion")
    assert after["sessions_judged"] == before["sessions_judged"] + 2
    assert after["success"] == before["success"] + 2
    assert after["failure"] == before["failure"]

    # 두 세션 모두 실패 목록에 없다(둘 다 success).
    failed_ids = {f["session_id"] for f in after["failed_sessions"]}
    assert one["session_id"] not in failed_ids
    assert two["session_id"] not in failed_ids
