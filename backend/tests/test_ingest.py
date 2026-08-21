"""이벤트 적재 규칙 검증 — 특히 robot_type 오배선 처리.

DB 없이 돈다. ingest() 가 커넥션에서 실제로 쓰는 것은 네 가지뿐이라
(transaction / fetch / fetchrow / execute) 그만큼만 흉내낸다.

레지스트리는 가짜를 쓰지 않고 config/event_codes.yaml 정본을 그대로 읽는다.
검사하려는 것의 절반이 "어느 코드가 어느 타입에 속하는가" 이고, 그 답은
yaml 에 있다. 가짜로 바꾸면 yaml 회귀를 못 잡는다.
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from app.event_codes import UNKNOWN_CODE, EventCodeRegistry
from app.ingest import _INSERT, ingest
from app.schemas import EventIn

MISMATCH_CODE = "system.robot_type_mismatch"


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeConnection:
    """robots 조회와 events INSERT 만 아는 최소 커넥션.

    duplicate_ids 에 든 event_id 는 ON CONFLICT DO NOTHING 에 걸린 것처럼
    RETURNING 을 비운다 — 재전송 상황을 만드는 장치다.
    """

    def __init__(self, robot_types, duplicate_ids=()):
        self.robot_types = robot_types
        self.duplicate_ids = set(duplicate_ids)
        # 시도된 INSERT 의 (event_code, robot_id). 중복이라 안 들어간 것도 포함.
        self.inserts = []
        # 상태 갱신이 건드린 테이블 이름.
        self.updated = []

    def transaction(self):
        return FakeTransaction()

    async def fetch(self, query, *args):
        assert "robots" in query, query
        wanted = set(args[0])
        return [{"robot_id": robot_id, "robot_type": robot_type}
                for robot_id, robot_type in self.robot_types.items()
                if robot_id in wanted]

    async def fetchrow(self, query, *args):
        event_id, robot_id = args[0], args[1]
        self.inserts.append((args[5], robot_id))
        return None if event_id in self.duplicate_ids else {"event_id": event_id}

    async def execute(self, query, *args):
        self.updated.append(query.split()[1])
        return "UPDATE 1"


def _registry():
    return EventCodeRegistry.load()


_BASE_TIME = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)


def _event(robot_id, event_code, event_id=None, offset_sec=0, session_id=7):
    return EventIn(
        event_id=event_id or uuid.uuid4(),
        robot_id=robot_id,
        session_id=session_id,
        occurred_at=_BASE_TIME + timedelta(seconds=offset_sec),
        level="info",
        event_code=event_code,
        source_node="test",
        payload={},
    )


def _codes(conn):
    return [code for code, _ in conn.inserts]


def _run(conn, events):
    return asyncio.run(ingest(conn, events, _registry()))


def test_insert_sql_detaches_events_from_missing_sessions() -> None:
    """DB 초기화 뒤 남은 로봇 큐 한 건이 새 이벤트 전체를 막지 않는다."""
    assert "SELECT 1 FROM guidance_sessions WHERE session_id = $3" in _INSERT
    assert "'reported_session_id', $3::bigint" in _INSERT


def test_mismatched_event_is_recorded_but_never_updates_state():
    """오배선은 적재하되 상태는 건드리지 않는다.

    nav.goal_succeeded 는 session_steps.arrived_at 을 찍는다. 조제 로봇이
    이 코드를 보내는 건 배선이 잘못된 것인데, 그대로 적용하면 팔 하나가
    환자의 안내 단계를 진행시킨다. 미등록 코드와 같은 원칙으로 기록은
    남기고 — 판정만 거부한다.
    """
    conn = FakeConnection({"omx-01": "manipulator"})

    result = _run(conn, [_event("omx-01", "nav.goal_succeeded")])

    assert result.state_updates == 0
    assert conn.updated == []
    assert result.type_mismatches == ["nav.goal_succeeded"]
    # 원본과 마커가 둘 다 남는다. 기록을 잃으면 오배선을 조사할 수 없다.
    assert _codes(conn) == ["nav.goal_succeeded", MISMATCH_CODE]


def test_matching_event_still_updates_state():
    """가드가 정상 경로를 막지 않는다."""
    conn = FakeConnection({"pinky-01": "mobile"})

    result = _run(conn, [_event("pinky-01", "nav.goal_succeeded")])

    assert result.state_updates == 1
    assert conn.updated == ["session_steps"]
    assert result.type_mismatches == []
    assert MISMATCH_CODE not in _codes(conn)


def test_marker_payload_names_the_robot_type_and_what_was_allowed():
    """대시보드가 마커만 보고 원인을 말할 수 있어야 한다."""
    conn = FakeConnection({"omx-01": "manipulator"})
    captured = {}

    original = conn.fetchrow

    async def spy(query, *args):
        if args[5] == MISMATCH_CODE:
            captured.update(payload=args[7], level=args[4], robot_id=args[1])
        return await original(query, *args)

    conn.fetchrow = spy
    _run(conn, [_event("omx-01", "nav.goal_succeeded")])

    payload = json.loads(captured["payload"])
    assert captured["level"] == "warning"
    assert captured["robot_id"] == "omx-01"
    assert payload["received_code"] == "nav.goal_succeeded"
    assert payload["actual_type"] == "manipulator"
    assert payload["allowed_types"] == ["mobile"]


def test_codes_common_to_both_types_pass_from_a_manipulator():
    """생존·세션 신호는 타입 무관이다 (monitoring-spec §4.3).

    이걸 mobile 전용으로 잠그면 팔 게이트웨이를 붙이는 순간 정상 신호가
    전부 경고로 뒤덮인다. 항상 켜져 있는 경고는 아무도 안 본다.
    """
    conn = FakeConnection({"omx-01": "manipulator"})

    result = _run(conn, [
        _event("omx-01", "robot.comm_lost", offset_sec=0),
        _event("omx-01", "robot.comm_restored", offset_sec=1),
        _event("omx-01", "session.started", offset_sec=2),
        _event("omx-01", "robot.battery_low", offset_sec=3),
    ])

    assert result.type_mismatches == []
    assert MISMATCH_CODE not in _codes(conn)


def test_activation_is_mobile_only():
    """arming 은 주행 로봇 전용이다.

    routers/robots.py 가 robot_type != 'mobile' 인 로봇의 arming 을 거부하므로,
    팔에서 온 activation.* 은 정의상 오배선이다. 정본과 백엔드 가드가 서로
    다른 말을 하면 안 된다.
    """
    conn = FakeConnection({"omx-01": "manipulator"})

    result = _run(conn, [_event("omx-01", "activation.armed")])

    assert result.type_mismatches == ["activation.armed"]


def test_manipulator_codes_pass_from_the_arm():
    """조제 사이클 전체가 경고 없이 지나가야 한다 (§6.2).

    §4.3 분류에서 팔 전용 열이 비어 있던 동안 조제 로봇 2대는 관제에 아무것도
    보고할 수 없었다. 이 테스트가 그 열이 다시 비는 것을 막는다.
    """
    conn = FakeConnection({"omx-01": "manipulator"})

    result = _run(conn, [
        _event("omx-01", "manipulator.policy_loaded", offset_sec=0),
        _event("omx-01", "manipulator.cycle_started", offset_sec=1),
        _event("omx-01", "manipulator.pick_failed", offset_sec=2),
        _event("omx-01", "manipulator.pick_succeeded", offset_sec=3),
        _event("omx-01", "manipulator.place_succeeded", offset_sec=4),
        _event("omx-01", "manipulator.cycle_completed", offset_sec=5),
    ])

    assert result.type_mismatches == []
    assert result.unknown_codes == []
    assert MISMATCH_CODE not in _codes(conn)


def test_manipulator_codes_are_a_mismatch_from_a_mobile_robot():
    """오배선은 양방향이다.

    type_mismatch.yaml 이 잠그는 것은 '팔이 nav 를 낸다' 쪽뿐이다. 반대
    방향 — 핑키가 조제 이벤트를 낸다 — 도 배선 오류이고, 여기를 열어두면
    §6.1 검증이 절반만 걸린 상태가 된다.
    """
    conn = FakeConnection({"pinky-01": "mobile"})

    result = _run(conn, [_event("pinky-01", "manipulator.cycle_started")])

    assert result.type_mismatches == ["manipulator.cycle_started"]
    assert _codes(conn) == ["manipulator.cycle_started", MISMATCH_CODE]
    # 팔 코드는 갱신하는 DB 컬럼이 없다(applies_to 없음). 오배선이든 아니든
    # 상태를 건드리면 안 된다.
    assert conn.updated == []


def test_probabilistic_pick_failure_stays_a_warning():
    """모방학습 pick 실패는 정상 동작 범위다 (§4.4).

    여기가 error 로 올라가면 §8.4 의 알림 라우팅이 확률적 실패마다 사람을
    깨우고, 그러면 팔의 알림 전체가 무시된다. error 는 사이클 포기와 서보
    결함에만 쓴다.
    """
    registry = _registry()

    assert registry.level_of("manipulator.pick_failed") == "warning"
    assert registry.level_of("manipulator.homing_required") == "warning"
    assert registry.level_of("manipulator.cycle_aborted") == "error"
    assert registry.level_of("manipulator.servo_fault") == "error"


def test_no_arm_prefixed_code_survives_in_the_canon():
    """접두사는 manipulator.* 다. arm.* 은 arming 과 겹친다 (§4.2).

    정본에 arm.* 이 하나라도 생기면 'arm robot arming' 이 되살아나고,
    §4.2 가 금지한 일괄 치환이 다시 유혹이 된다.
    """
    registry = _registry()

    assert [code for code in registry._codes if code.startswith("arm.")] == []


def test_a_resent_event_does_not_emit_the_marker_again():
    """마커는 append-only 라 중복 발행하면 영원히 남는다.

    게이트웨이는 두절 후 같은 배치를 다시 보낸다. 재전송마다 마커가 하나씩
    늘면 미등록 코드 화면의 건수가 실제 발생 횟수와 무관해진다.
    """
    unknown_id, mismatch_id = uuid.uuid4(), uuid.uuid4()
    conn = FakeConnection(
        {"pinky-01": "mobile", "omx-01": "manipulator"},
        duplicate_ids=[unknown_id, mismatch_id],
    )

    result = _run(conn, [
        _event("pinky-01", "bogus.code", event_id=unknown_id, offset_sec=0),
        _event("omx-01", "nav.goal_succeeded", event_id=mismatch_id, offset_sec=1),
    ])

    assert result.duplicates == 2
    assert result.inserted == 0
    assert UNKNOWN_CODE not in _codes(conn)
    assert MISMATCH_CODE not in _codes(conn)


def test_unknown_code_is_stored_with_a_marker():
    """규칙 4 회귀 방지 — 미등록 코드는 거부하지 않는다."""
    conn = FakeConnection({"pinky-01": "mobile"})

    result = _run(conn, [_event("pinky-01", "bogus.code")])

    assert result.unknown_codes == ["bogus.code"]
    assert _codes(conn) == ["bogus.code", UNKNOWN_CODE]
    assert conn.updated == []


def test_robot_missing_from_the_inventory_is_not_judged():
    """robots 에 없는 로봇은 타입을 모르므로 판정하지 않는다.

    모르는 것을 오배선으로 몰면, 등록이 늦은 새 로봇의 정상 이벤트가 전부
    경고로 찍힌다. 그건 오배선이 아니라 등록 누락이고 다른 문제다.
    """
    conn = FakeConnection({})

    result = _run(conn, [_event("pinky-99", "nav.goal_succeeded")])

    assert result.type_mismatches == []
    assert result.state_updates == 1


def test_batch_is_applied_in_occurred_at_order():
    """규칙 1 회귀 방지 — 도착 순서가 아니라 발생 순서."""
    conn = FakeConnection({"pinky-01": "mobile"})

    _run(conn, [
        _event("pinky-01", "nav.goal_aborted", offset_sec=30),
        _event("pinky-01", "nav.goal_sent", offset_sec=10),
        _event("pinky-01", "nav.stuck", offset_sec=20),
    ])

    assert _codes(conn) == ["nav.goal_sent", "nav.stuck", "nav.goal_aborted"]


# --- 알림 연결 (§8.4 · 로드맵 12) ---------------------------------------------

def test_only_newly_inserted_events_reach_the_alert_path(monkeypatch):
    """재전송된 배치가 같은 사건을 다시 울리면, 두절이 한 번 있었을 뿐인데
    채널에는 열 번 찍힌다. 알림은 '적재됐다' 가 아니라 '처음 들어왔다' 에 붙는다.
    """
    from app import notify

    resent = uuid.uuid4()
    conn = FakeConnection({"pinky-01": "mobile"}, duplicate_ids=[resent])
    seen = []
    monkeypatch.setattr(notify, "notify", seen.append)

    _run(conn, [
        _event("pinky-01", "robot.comm_lost", event_id=resent),
        _event("pinky-01", "fire.detected", offset_sec=1),
    ])

    assert [e.event_code for e in seen[0]] == ["fire.detected"]


def test_alerting_failure_never_breaks_ingestion(monkeypatch):
    """여기서 예외가 새면 게이트웨이가 같은 배치를 무한히 재전송한다."""
    from app import notify

    def boom(_events):
        raise RuntimeError("웹훅이 죽었다")

    monkeypatch.setattr(notify, "notify", boom)
    conn = FakeConnection({"pinky-01": "mobile"})

    # ingest 는 notify.notify 를 그대로 부른다. 방어는 그 안에 있으므로
    # 여기서는 '적재 결과가 온전한가' 만 본다.
    try:
        result = _run(conn, [_event("pinky-01", "fire.detected")])
    except RuntimeError:
        raise AssertionError("알림 실패가 적재를 깼다")

    assert result.inserted == 1
