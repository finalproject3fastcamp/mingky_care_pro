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
import json
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
from app.control_audit import INTERVENTION_ACTIONS  # noqa: E402

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
