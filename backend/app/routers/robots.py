"""로봇 목록, 최근 상태, 그리고 의료진이 로봇을 활성화(arming)하는 경로."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Response

from .. import (
    arming,
    battery_forecast,
    dispense,
    heartbeat,
    inventory_rules,
    qr_runtime,
    robot_runtime,
    servo_health,
    topic_watch,
)
from ..db import get_pool
from ..ingest import ingest
from ..registry import get_registry
from ..schemas import (
    BatteryForecastOut,
    BatterySampleIn,
    ManipulatorDetail,
    ManipulatorRobotOut,
    MobileRobotOut,
    NodeGraphInfo,
    ProcessInfo,
    QrObservationIn,
    QrObservationOut,
    RobotArmingOut,
    RobotHeartbeatIn,
    RobotHeartbeatOut,
    RobotInventoryIn,
    RobotInventoryOut,
    RobotOut,
    ServoHealthOut,
    ServoSampleIn,
    WorkspaceInfo,
)

log = logging.getLogger("mingky")

router = APIRouter(prefix="/robots", tags=["robots"])

# 의료진이 로봇을 고를 때 최소로 요구하는 배터리 잔량.
# BatteryGuard 의 저전압 운영선과 같은 값이다. 서버에서도 다시 검사해 오래된
# 화면이나 직접 API 호출로 저전압 로봇이 활성화되는 것을 막는다.
MIN_BATTERY_PERCENT = 40
MAX_BATTERY_AGE = timedelta(minutes=5)

# 이 전압을 넘으면 퍼센트가 100 에 고정된다 (adc_reader 의 FULL_V).
#
# 충전 중에는 충전기가 단자 전압을 끌어올려, 잔량이 18% 여도 7.6V 를 넘겨
# 100% 로 보인다. 실측: pinky-01 이 6.94V(18%) 에서 충전기를 꽂자 2분 만에
# 7.64V(100%) 가 됐다. 2분에 82% 가 찰 수는 없다 — 잔량이 아니라 충전
# 전압이다.
#
# 쉬고 있는 완충 로봇도 이 값을 넘으므로, 클램프 자체로는 거부하지 않는다.
# 전압이 오르는 중(충전 중)일 때만 잔량을 믿을 수 없다고 본다.
BATTERY_CLAMP_VOLTAGE = 7.6

# 충전 여부 판정에 쓸 전압 추이 창.
BATTERY_TREND_WINDOW_MIN = 30


def _reject(code: str, message: str, **params) -> HTTPException:
    """arm 거부를 기계용 코드와 사람용 문구로 나눠 돌려준다.

    영어 문자열 하나만 내려주면 프론트가 그걸 파싱해 화면 문구를 만들게 된다.
    그러면 백엔드가 문구를 못 고친다 — 오타 하나 고치는 데 프론트 배포가
    필요해지고, 실제로는 아무도 안 고치고 영어가 그대로 의료진에게 보인다.

    code 는 기계가 분기하고, params 로 숫자를 넘겨 프론트가 문장을 만든다.
    "배터리 N%" 의 N 을 문자열에서 정규식으로 뽑는 상황을 만들지 않는다.

    message 는 남겨 둔다. detail 을 dict 로 바꾸면 문자열을 기대하던 기존
    클라이언트가 깨지므로, 프론트를 다 옮긴 뒤에 정리한다.
    """
    return HTTPException(
        status_code=409,
        detail={"code": code, "params": params, "message": message},
    )

# 배터리는 마지막으로 저장된 표본이다. 실시간 값이 아니다.
# 화면에 띄우는 실시간 상태는 DB 에 저장하지 않기로 했다(3~5초마다 덮어쓰는
# 값을 영속화할 이유가 없다). 여기서는 2분 주기로 쌓인 로그의 최신 행을 쓴다.
_SQL = """
    SELECT r.robot_id, r.robot_type, r.display_name, r.domain_id, r.is_active,
           b.voltage        AS battery_voltage,
           b.battery_percent,
           b.recorded_at    AS battery_recorded_at,
           s.session_id     AS active_session_id,
           s.patient_id     AS active_patient_id,
           last_session.ended_at AS last_session_ended_at,
           last_session.end_reason AS last_session_end_reason
    FROM robots r
    LEFT JOIN LATERAL (
        SELECT voltage, battery_percent, recorded_at
        FROM robot_battery_log
        WHERE robot_id = r.robot_id
        ORDER BY recorded_at DESC
        LIMIT 1
    ) b ON TRUE
    -- 003 의 부분 유니크 인덱스가 활성 세션을 로봇당 하나로 보장하므로
    -- 이 조인이 행을 늘리지 않는다.
    LEFT JOIN guidance_sessions s
        ON s.robot_id = r.robot_id AND s.ended_at IS NULL
    LEFT JOIN LATERAL (
        SELECT ended_at, end_reason
        FROM guidance_sessions
        WHERE robot_id = r.robot_id AND ended_at IS NOT NULL
        ORDER BY ended_at DESC
        LIMIT 1
    ) last_session ON TRUE
    ORDER BY r.robot_id
"""

# 개별 로봇 조회. arm/disarm 검증에 쓴다. 위 SQL 과 컬럼 셋을 맞춰 두면
# 필요할 때 응답 조립을 공유할 수 있다.
_SQL_ONE = _SQL.replace(
    "ORDER BY r.robot_id", "WHERE r.robot_id = $1"
)

_INSERT_EVENT = """
    INSERT INTO events (event_id, robot_id, session_id, occurred_at,
                        level, event_code, source_node, payload)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (event_id) DO NOTHING
"""


async def _insert_activation_event(
    conn, robot_id: str, code: str, payload: dict, session_id: int | None = None,
) -> None:
    """activation.* 이벤트를 events 테이블에 남긴다.

    로봇이 발행하는 이벤트와 달리 이건 백엔드가 직접 발행한다. 이유는
    arming 상태가 백엔드 소유라서 — 로봇은 폴링 결과만 볼 뿐이다.
    source_node 는 그 사실을 드러내려고 'backend.arming' 으로 표시한다.
    """
    await conn.execute(
        _INSERT_EVENT,
        uuid.uuid4(),
        robot_id,
        session_id,
        datetime.now(timezone.utc),
        "info",
        code,
        "backend.arming",
        json.dumps(payload, ensure_ascii=False),
    )


def _common(row, seen: dict | None) -> dict:
    """종류와 무관한 필드 (§4.3 의 공통 축) — 생존·형상·리소스.

    seen 을 넘기지 않으면 link_state 는 unknown 으로 나간다. arm/disarm 응답처럼
    그 순간의 생존 여부가 관심사가 아닌 경로에서 굳이 조회하지 않기 위해서다.
    """
    last_seen, offline = (seen or {}).get(row["robot_id"], (None, False))
    runtime = robot_runtime.snapshot().get(row["robot_id"])
    return dict(
        robot_id=row["robot_id"],
        display_name=row["display_name"],
        domain_id=row["domain_id"],
        is_active=row["is_active"],
        last_seen_at=last_seen,
        # 한 번도 신호를 안 보낸 로봇은 offline 이 아니라 unknown 이다.
        # OMX 는 관제 PC 에 USB 직결된 LeRobot 프로세스라 잃을 네트워크
        # 링크가 없다. 이걸 두절로 표시하면 타임라인이 오탐으로 덮인다.
        link_state=("unknown" if last_seen is None
                    else "offline" if offline else "online"),
        runtime_reported_at=runtime.reported_at if runtime else None,
        # 구버전 게이트웨이는 안 보낸다. None 이 정상이고, 화면은 값이 없는
        # 것과 0 인 것을 구분해서 그려야 한다.
        cpu_total_pct=runtime.cpu_total_pct if runtime else None,
        queue_pending=runtime.queue_pending if runtime else None,
        max_node_cpu_pct=runtime.max_node_cpu_pct if runtime else None,
        max_node_cpu_name=runtime.max_node_cpu_name if runtime else None,
        inventory_hash=runtime.inventory_hash if runtime else None,
    )


def _row_to_out(
    row,
    armed_map: dict[str, datetime],
    seen: dict | None = None,
    details: dict[str, ManipulatorDetail] | None = None,
) -> MobileRobotOut | ManipulatorRobotOut:
    """DB 행을 타입별 응답으로 만든다 (§7.3).

    분기가 여기 한 곳뿐인 것이 핵심이다. 팔에 없는 것(배터리·arming·Nav2)을
    null 로 채워 내보내면 프론트가 그 null 을 '보고 안 함' 과 구분하지 못하고,
    타입별 화면이 결국 런타임 if 로 흩어진다.
    """
    if row["robot_type"] == "manipulator":
        return ManipulatorRobotOut(
            **_common(row, seen),
            # 아직 아무것도 보고하지 않은 팔은 dispense.summarize 에 안 담긴다.
            # 그건 정상 초기 상태이지 로봇이 없는 것이 아니므로 빈 창을 채운다.
            detail=(details or {}).get(row["robot_id"]) or ManipulatorDetail(
                window=dispense.DEFAULT_WINDOW,
                window_cycles=0,
                sample_complete=False,
                cycles_completed=0,
                cycles_aborted=0,
                pick_succeeded=0,
                pick_failed=0,
                pick_retried=0,
            ),
        )

    runtime = robot_runtime.snapshot().get(row["robot_id"])
    return MobileRobotOut(
        **_common(row, seen),
        battery_voltage=row["battery_voltage"],
        battery_percent=row["battery_percent"],
        battery_recorded_at=row["battery_recorded_at"],
        active_session_id=row["active_session_id"],
        active_patient_id=row["active_patient_id"],
        last_session_ended_at=row["last_session_ended_at"],
        last_session_end_reason=row["last_session_end_reason"],
        armed_at=armed_map.get(row["robot_id"]),
        system_state=runtime.system_state if runtime else "unknown",
        localization_active=runtime.localization_active if runtime else False,
        fire_alarm_active=runtime.fire_alarm_active if runtime else None,
        returning_to_dock=runtime.returning_to_dock if runtime else False,
        navigation_speed_mps=runtime.navigation_speed_mps if runtime else None,
        guide_robot_state=runtime.guide_robot_state if runtime else None,
        # 판정은 서버가 한 번만 한다. 화면이 같은 임계를 다시 들고 있으면
        # config/topic_watch.yaml 을 고쳐도 화면 색이 안 바뀐다.
        topics=topic_watch.judge(runtime.topics) if runtime else [],
    )


@router.get("", response_model=list[RobotOut])
async def list_robots() -> list[MobileRobotOut | ManipulatorRobotOut]:
    """모든 로봇 목록 + 타입별 상세.

    팔의 조제 지표를 별도 엔드포인트로 빼지 않는다. 화면이 로봇 목록을 이미
    3초로 폴링하는데 팔만 따로 물으면 두 응답의 시각이 어긋나고, 그 어긋남은
    "사이클이 끝났는데 유휴로 안 바뀐다" 같은 형태로만 보인다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SQL)
        # 팔이 한 대도 없으면 쿼리를 아예 보내지 않는다. mobile 만 있는
        # 설치에서 이 경로가 비용이 되면 안 된다.
        details = {}
        if any(row["robot_type"] == "manipulator" for row in rows):
            details = dispense.summarize(
                await conn.fetch(dispense.DETAIL_SQL, dispense.EVENT_LIMIT))
    # 활성화·생존 둘 다 DB 가 아니라 메모리에 있다 (arming.py / heartbeat.py).
    armed_map = arming.snapshot()
    seen = heartbeat.snapshot()
    return [_row_to_out(row, armed_map, seen, details) for row in rows]


@router.post("/{robot_id}/arm", response_model=MobileRobotOut)
async def arm_robot(robot_id: str) -> MobileRobotOut:
    """의료진 대시보드가 로봇 하나를 골라 활성화한다.

    검증:
      - 등록된 로봇이며 is_active
      - robot_type = 'mobile' (조제 스테이션은 대상 아님)
      - 진행 중 세션 없음
      - 5분 이내 배터리 >= MIN_BATTERY_PERCENT (없거나 오래됐으면 거부)

    이미 armed 인 로봇을 다시 arm 하면 200 이 나가지만 arming.arm 이
    idempotent 라 armed_at 시각과 이벤트가 새로 안 생긴다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(_SQL_ONE, robot_id)
            if row is None:
                raise HTTPException(status_code=404, detail="robot not found")
            if not row["is_active"]:
                raise _reject("robot_inactive", "robot is not active")
            if row["robot_type"] != "mobile":
                raise _reject("not_mobile", "only mobile robots can be armed")
            if row["active_session_id"] is not None:
                raise _reject(
                    "robot_busy",
                    f"robot busy with session {row['active_session_id']}",
                    session_id=row["active_session_id"])
            runtime = robot_runtime.snapshot().get(robot_id)
            if runtime is not None and runtime.returning_to_dock:
                raise _reject(
                    "returning_to_dock", "robot is returning to charging station")
            if runtime is not None and runtime.guide_robot_state in (
                    "returning_to_dock", "paused"):
                raise _reject(
                    "robot_unavailable",
                    f"robot state is {runtime.guide_robot_state}",
                    state=runtime.guide_robot_state)
            seen = heartbeat.snapshot().get(robot_id)
            if seen is None:
                raise _reject("link_unknown", "robot connection is unknown")
            if seen[1]:
                raise _reject(
                    "robot_offline", "robot is offline",
                    last_seen_sec=round(
                        (datetime.now(timezone.utc) - seen[0]).total_seconds()))
            percent = row["battery_percent"]
            if percent is None:
                raise _reject("battery_unknown", "battery reading is missing")
            if percent < MIN_BATTERY_PERCENT:
                raise _reject(
                    "battery_low",
                    f"battery {percent}% below {MIN_BATTERY_PERCENT}%",
                    percent=percent, min_percent=MIN_BATTERY_PERCENT)
            voltage = row["battery_voltage"]
            if voltage is not None and voltage > BATTERY_CLAMP_VOLTAGE:
                # 퍼센트가 100 에 고정된 구간이다. 충전 중이면 그 100 은
                # 잔량이 아니라 충전 전압이라, 그대로 배정하면 거의 빈
                # 로봇이 안내를 나갔다가 도중에 멈춘다.
                if await _battery_is_charging(conn, robot_id):
                    raise _reject(
                        "battery_charging",
                        "battery percent is unreliable while charging",
                        voltage=round(float(voltage), 3),
                        clamp_voltage=BATTERY_CLAMP_VOLTAGE)

            recorded_at = row["battery_recorded_at"]
            if (recorded_at is None
                    or datetime.now(timezone.utc) - recorded_at > MAX_BATTERY_AGE):
                # 나이를 그대로 넘긴다. "오래됐다" 만으로는 5분인지 12분인지
                # 모르고, 그 차이가 엔지니어를 부를지 말지를 가른다.
                age_sec = (
                    None if recorded_at is None
                    else round((datetime.now(timezone.utc)
                                - recorded_at).total_seconds()))
                raise _reject(
                    "battery_stale", "battery reading is stale",
                    age_sec=age_sec,
                    max_age_sec=round(MAX_BATTERY_AGE.total_seconds()))

            already = arming.is_armed(robot_id)
            arming.arm(robot_id)
            # idempotent: 이미 armed 였으면 이벤트도 이중 발행하지 않는다.
            if not already:
                await _insert_activation_event(
                    conn, robot_id, "activation.armed", {})

    armed_map = arming.snapshot()
    # 위에서 mobile 이 아닌 로봇을 거부했으므로 여기 오는 것은 항상 mobile 이다.
    return _row_to_out(row, armed_map)


@router.delete("/{robot_id}/arm", response_model=RobotOut)
async def disarm_robot(robot_id: str) -> MobileRobotOut | ManipulatorRobotOut:
    """의료진이 활성화를 취소.

    로봇이 없으면 404. 원래 disarmed 였으면 200 + 이벤트 없음.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_SQL_ONE, robot_id)
        if row is None:
            raise HTTPException(status_code=404, detail="robot not found")
        changed = arming.disarm(robot_id)
        if changed:
            await _insert_activation_event(
                conn, robot_id, "activation.canceled", {"reason": "staff"})

    armed_map = arming.snapshot()
    return _row_to_out(row, armed_map)


@router.get("/{robot_id}/arming", response_model=RobotArmingOut)
async def get_arming(robot_id: str) -> RobotArmingOut:
    """로봇 QR 노드가 주기 폴링. 최소 페이로드로 유지한다."""
    at = arming.armed_at(robot_id)
    return RobotArmingOut(robot_id=robot_id, armed=at is not None, armed_at=at)


@router.post("/{robot_id}/heartbeat", response_model=RobotHeartbeatOut)
async def post_heartbeat(
    robot_id: str, body: RobotHeartbeatIn | None = None,
) -> RobotHeartbeatOut:
    """로봇 생존 신호.

    게이트웨이의 이벤트 큐를 타지 않는 별도 경로다. 실패하면 로봇 쪽에서
    그냥 버린다 — 재전송하면 두절 중 쌓였다가 몰려와 신호의 의미가 사라진다.

    '언제 왔는가' 는 서버가 안다. 로봇 시계를 믿지 않으므로 시계 어긋남의
    영향도 받지 않는다. 본문은 로봇만 아는 것(시스템 상태, 자원, 인벤토리
    지문)만 담는다.

    응답에 need_inventory 를 실어 인벤토리 본문을 요구한다. 서버가 재시작해
    메모리를 잃었거나 로봇의 전송이 유실되면 해시만으로는 복구되지 않는다.
    """
    if not heartbeat.is_tracked(robot_id):
        # 첫 신호일 때만 확인한다. 오타난 robot_id 로 유령 로봇이 감시 목록에
        # 쌓이면, 존재하지 않는 로봇에 대해 comm_lost 가 계속 발행된다.
        pool = get_pool()
        async with pool.acquire() as conn:
            known = await conn.fetchval(
                "SELECT 1 FROM robots WHERE robot_id = $1 AND is_active", robot_id)
        if not known:
            raise HTTPException(status_code=404, detail="unknown or inactive robot")

    heartbeat.touch(robot_id)
    if body is None:
        return RobotHeartbeatOut()

    robot_runtime.update(
        robot_id,
        body.system_state,
        body.localization_active,
        body.fire_alarm_active,
        body.returning_to_dock,
        body.navigation_speed_mps,
        guide_robot_state=body.guide_robot_state,
        inventory_hash=body.inventory_hash,
        cpu_total_pct=body.cpu_total_pct,
        queue_pending=body.queue_pending,
        max_node_cpu_pct=body.max_node_cpu_pct,
        max_node_cpu_name=body.max_node_cpu_name,
        topics=body.topics,
    )
    if body.returning_to_dock:
        # 복귀 직전에 활성화된 경우에도 QR 스캔을 즉시 끈다.
        arming.disarm(robot_id)

    return RobotHeartbeatOut(
        need_inventory=await _needs_inventory(robot_id, body.inventory_hash))


async def _needs_inventory(robot_id: str, reported_hash: str | None) -> bool:
    """로봇이 보고한 지문이 저장된 것과 다른가.

    해시가 없으면 요구하지 않는다. 인벤토리를 끈 게이트웨이(구버전이거나
    inventory_interval_sec=0)에 매번 재전송을 요구해도 오지 않고, 그 요구가
    5초마다 DB 조회를 만든다.
    """
    if not reported_hash:
        return False
    pool = get_pool()
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT inventory_hash FROM robot_inventory WHERE robot_id = $1",
            robot_id)
    return stored != reported_hash


@router.post("/{robot_id}/inventory", status_code=204)
async def post_inventory(robot_id: str, body: RobotInventoryIn) -> Response:
    """실행 중인 코드 버전과 노드 목록. 내용이 바뀔 때만 온다.

    최신 한 행만 유지한다. 추이가 필요 없다 — 지금 무엇이 도는지가 질문이지
    어제 무엇이 돌았는지가 아니다.

    reported_at 은 로봇 시계가 아니라 서버 수신 시각을 쓴다. 이 값으로
    신선도를 판정하므로 전송 지연을 숨기지 않아야 한다.
    """
    payload = body.model_dump(mode="json", exclude={"inventory_hash", "reported_at"})

    pool = get_pool()
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            """
            INSERT INTO robot_inventory (robot_id, inventory_hash, payload)
            SELECT robot_id, $2, $3::jsonb FROM robots
            WHERE robot_id = $1 AND is_active
            ON CONFLICT (robot_id) DO UPDATE
                SET inventory_hash = EXCLUDED.inventory_hash,
                    payload        = EXCLUDED.payload,
                    reported_at    = now()
            RETURNING 1
            """,
            robot_id, body.inventory_hash, json.dumps(payload, ensure_ascii=False),
        )
    if not stored:
        raise HTTPException(status_code=404, detail="unknown or inactive robot")
    return Response(status_code=204)


@router.get("/{robot_id}/inventory", response_model=RobotInventoryOut)
async def get_inventory(robot_id: str) -> RobotInventoryOut:
    """엔지니어 화면이 읽는 인벤토리. 판정 결과를 함께 내려준다.

    중복 심각도와 워크스페이스 혼재 판정을 프론트가 다시 구현하면 두 곳이
    어긋나고, 어긋난 순간 어느 쪽이 맞는지 아무도 모른다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT inventory_hash, payload, reported_at "
            "FROM robot_inventory WHERE robot_id = $1",
            robot_id)
    if row is None:
        raise HTTPException(status_code=404, detail="inventory not reported yet")

    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) \
        else row["payload"]
    node_graph = [NodeGraphInfo(**item) for item in payload.get("node_graph", [])]
    workspaces = [WorkspaceInfo(**item) for item in payload.get("workspaces", [])]

    return RobotInventoryOut(
        robot_id=robot_id,
        inventory_hash=row["inventory_hash"],
        reported_at=row["reported_at"],
        node_graph=node_graph,
        processes=[ProcessInfo(**item) for item in payload.get("processes", [])],
        workspaces=workspaces,
        ros_domain_id=payload.get("ros_domain_id"),
        map_name=payload.get("map_name"),
        map_hash=payload.get("map_hash"),
        duplicates=inventory_rules.duplicates(node_graph),
        mixed_workspaces=inventory_rules.has_mixed_workspaces(workspaces),
    )


@router.post("/{robot_id}/battery", status_code=204)
async def post_battery(robot_id: str, sample: BatterySampleIn) -> Response:
    """배터리 최신 표본을 PostgreSQL 추이 로그에 저장한다.

    기록 시각은 로봇 시계가 아니라 서버 수신 시각을 쓴다. 이 값은 대시보드의
    stale 판정과 arming 검증에 사용되므로 전송 지연을 숨기지 않아야 한다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        inserted = await conn.fetchval(
            """
            INSERT INTO robot_battery_log (
                robot_id, recorded_at, voltage, battery_percent)
            SELECT robot_id, now(), $2, $3
            FROM robots
            WHERE robot_id = $1 AND is_active
            RETURNING 1
            """,
            robot_id,
            sample.voltage,
            sample.battery_percent,
        )
    if not inserted:
        raise HTTPException(status_code=404, detail="unknown or inactive robot")
    return Response(status_code=204)


async def _battery_is_charging(conn, robot_id: str) -> bool:
    """최근 전압 추이가 오르는 중인가.

    충전 중이면 단자 전압이 잔량과 무관하게 올라간다. 반대로 쉬고 있는
    완충 로봇은 평평하거나 아주 천천히 내려간다 — 그건 배정해도 된다.
    클램프 구간이라는 사실만으로 거부하면 완충 로봇이 영영 못 나간다.

    추정이 불안정하면(부하 변동) charging 으로 보지 않는다. 여기서
    막아 세우는 쪽이 안전해 보이지만, 오탐이 잦으면 의료진이 이유를
    모른 채 계속 거부당하고 결국 아무도 안 믿게 된다.
    """
    rows = await conn.fetch(
        """
        SELECT recorded_at, voltage
        FROM robot_battery_log
        WHERE robot_id = $1
          AND recorded_at >= now() - ($2 || ' minutes')::interval
        ORDER BY recorded_at
        """,
        robot_id, str(BATTERY_TREND_WINDOW_MIN),
    )
    result = battery_forecast.forecast(
        [(r["recorded_at"], r["voltage"]) for r in rows])
    return result is not None and result.direction == "charging"


@router.get("/{robot_id}/battery-forecast", response_model=BatteryForecastOut)
async def get_battery_forecast(
    robot_id: str,
    window_min: int = Query(60, ge=10, le=720),
) -> BatteryForecastOut:
    """충전/방전 예상 시간. 전압 추이의 기울기로 낸다.

    퍼센트로 계산하지 않는다. 7.6V 위는 전부 100% 라 기울기가 0 이고,
    충전 중인지 만충인지 구분되지 않는다.

    표본이 모자라거나 부하 변동으로 기울기가 불안정하면 시간을 내지 않고
    방향만 돌려준다. 틀린 시간은 없는 시간보다 나쁘다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT recorded_at, voltage
            FROM robot_battery_log
            WHERE robot_id = $1
              AND recorded_at >= now() - ($2 || ' minutes')::interval
            ORDER BY recorded_at
            """,
            robot_id, str(window_min),
        )

    result = battery_forecast.forecast(
        [(row["recorded_at"], row["voltage"]) for row in rows])

    if result is None:
        # 표본이 모자라다. 방향조차 알 수 없으므로 unknown 이다.
        return BatteryForecastOut(
            robot_id=robot_id, sample_count=len(rows),
            reason="insufficient_samples")

    return BatteryForecastOut(
        robot_id=robot_id,
        direction=result.direction,
        seconds=result.seconds,
        slope_v_per_hour=result.slope_v_per_hour,
        r_squared=result.r_squared,
        sample_count=result.sample_count,
        reason=result.reason,
    )


@router.post("/{robot_id}/servos", status_code=204)
async def post_servos(robot_id: str, sample: ServoSampleIn) -> Response:
    """서보 온도·전류 표본 (§4.4 · 로드맵 11).

    배터리와 같은 성격이다 — 이벤트가 아니라 저빈도 추이 로그다. 1분마다
    찍히는 온도를 events 에 넣으면 타임라인 필터가 쓸모없어진다.

    **팔이 아닌 로봇은 거부한다.** 미등록 이벤트 코드를 적재하고 경고하는
    §6.1 과 다른 판단이다. 저쪽은 기록을 잃으면 사건 자체가 사라지지만, 이건
    주기 표본이라 하나 버려도 다음이 온다. 반대로 오배선을 그대로 쌓으면
    서보가 없는 로봇의 행이 추이 판정에 섞인다.

    임계 통과는 여기서 판정한다. 별도 감시 루프를 두지 않는 이유는 표본이
    도착하는 순간이 곧 판정 시점이라서다 — heartbeat 처럼 '안 오는 것' 을
    재는 판정이 아니다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        robot_type = await conn.fetchval(
            "SELECT robot_type FROM robots WHERE robot_id = $1 AND is_active",
            robot_id)
        if robot_type is None:
            raise HTTPException(status_code=404, detail="unknown or inactive robot")
        if robot_type != "manipulator":
            raise HTTPException(
                status_code=409, detail="only manipulators report servos")

        async with conn.transaction():
            await conn.executemany(servo_health.INSERT_SQL, [
                (robot_id, reading.joint, reading.temp_c, reading.current_ma,
                 reading.voltage_v, reading.hardware_error)
                for reading in sample.servos
            ])
            events = servo_health.crossings(robot_id, sample.servos)
            if events:
                await ingest(conn, events, get_registry())

    for event in events:
        log.warning("%s robot=%s %s",
                    event.event_code, event.robot_id, event.payload)
    return Response(status_code=204)


@router.get("/{robot_id}/servos", response_model=ServoHealthOut)
async def get_servos(
    robot_id: str,
    window_min: int = Query(servo_health.DEFAULT_WINDOW_MIN, ge=10, le=1440),
) -> ServoHealthOut:
    """조인트별 최신값과 온도 추세.

    로봇 목록에 얹지 않는다. 저쪽은 3초 폴링인데 추세는 몇 시간 창의 집계라
    같이 두면 그 쿼리가 3초마다 돈다 — `/battery-forecast` 를 따로 뺀 것과
    같은 이유다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        latest = await conn.fetch(servo_health.LATEST_SQL, robot_id)
        trend = await conn.fetch(
            servo_health.TREND_SQL, robot_id, str(window_min))

    return servo_health.summarize(robot_id, latest, trend, window_min)


@router.post("/{robot_id}/qr-observation", status_code=204)
async def post_qr_observation(
    robot_id: str, observation: QrObservationIn,
) -> Response:
    """후방 QR 거리 최신값을 갱신한다. 이벤트·DB에는 저장하지 않는다."""
    if not heartbeat.is_tracked(robot_id):
        raise HTTPException(status_code=409, detail="robot is not connected")
    qr_runtime.update(robot_id, observation.visible, observation.distance)
    return Response(status_code=204)


@router.get("/{robot_id}/qr-observation", response_model=QrObservationOut)
async def get_qr_observation(robot_id: str) -> QrObservationOut:
    """관제 화면에 현재 QR 인식 여부와 거리만 제공한다."""
    observation = qr_runtime.get(robot_id)
    if observation is None:
        return QrObservationOut(robot_id=robot_id)
    return QrObservationOut(
        robot_id=robot_id,
        visible=observation.visible,
        distance=observation.distance,
        observed_at=observation.observed_at,
    )
