"""로봇 목록, 최근 상태, 그리고 의료진이 로봇을 활성화(arming)하는 경로."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Response

from .. import arming, heartbeat, robot_runtime
from ..db import get_pool
from ..schemas import BatterySampleIn, RobotArmingOut, RobotHeartbeatIn, RobotOut

router = APIRouter(prefix="/robots", tags=["robots"])

# 의료진이 로봇을 고를 때 최소로 요구하는 배터리 잔량.
# BatteryGuard 의 저전압 운영선과 같은 값이다. 서버에서도 다시 검사해 오래된
# 화면이나 직접 API 호출로 저전압 로봇이 활성화되는 것을 막는다.
MIN_BATTERY_PERCENT = 40
MAX_BATTERY_AGE = timedelta(minutes=5)

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


def _row_to_out(row, armed_map: dict[str, datetime], seen: dict | None = None) -> RobotOut:
    """DB 행에 메모리에만 있는 두 가지(활성화·생존)를 얹는다.

    seen 을 넘기지 않으면 link_state 는 unknown 으로 나간다. arm/disarm 응답처럼
    그 순간의 생존 여부가 관심사가 아닌 경로에서 굳이 조회하지 않기 위해서다.
    """
    last_seen, offline = (seen or {}).get(row["robot_id"], (None, False))
    runtime = robot_runtime.snapshot().get(row["robot_id"])
    return RobotOut(
        **dict(row),
        armed_at=armed_map.get(row["robot_id"]),
        last_seen_at=last_seen,
        # 한 번도 신호를 안 보낸 로봇은 offline 이 아니라 unknown 이다.
        # OMX 는 관제 PC 에 USB 직결된 LeRobot 프로세스라 잃을 네트워크
        # 링크가 없다. 이걸 두절로 표시하면 타임라인이 오탐으로 덮인다.
        link_state=("unknown" if last_seen is None
                    else "offline" if offline else "online"),
        system_state=runtime.system_state if runtime else "unknown",
        localization_active=runtime.localization_active if runtime else False,
        runtime_reported_at=runtime.reported_at if runtime else None,
    )


@router.get("", response_model=list[RobotOut])
async def list_robots() -> list[RobotOut]:
    """모든 로봇 목록 + 최근 배터리 + 활성 세션 + 활성화 여부."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SQL)
    # 활성화·생존 둘 다 DB 가 아니라 메모리에 있다 (arming.py / heartbeat.py).
    armed_map = arming.snapshot()
    seen = heartbeat.snapshot()
    return [_row_to_out(row, armed_map, seen) for row in rows]


@router.post("/{robot_id}/arm", response_model=RobotOut)
async def arm_robot(robot_id: str) -> RobotOut:
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
                raise HTTPException(status_code=409, detail="robot is not active")
            if row["robot_type"] != "mobile":
                raise HTTPException(
                    status_code=409, detail="only mobile robots can be armed")
            if row["active_session_id"] is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"robot busy with session {row['active_session_id']}")
            seen = heartbeat.snapshot().get(robot_id)
            if seen is None:
                raise HTTPException(
                    status_code=409, detail="robot connection is unknown")
            if seen[1]:
                raise HTTPException(status_code=409, detail="robot is offline")
            percent = row["battery_percent"]
            if percent is None or percent < MIN_BATTERY_PERCENT:
                raise HTTPException(
                    status_code=409,
                    detail=(f"battery {percent if percent is not None else 'unknown'}%"
                            f" below {MIN_BATTERY_PERCENT}%"))
            recorded_at = row["battery_recorded_at"]
            if (recorded_at is None
                    or datetime.now(timezone.utc) - recorded_at > MAX_BATTERY_AGE):
                raise HTTPException(
                    status_code=409, detail="battery reading is stale")

            already = arming.is_armed(robot_id)
            arming.arm(robot_id)
            # idempotent: 이미 armed 였으면 이벤트도 이중 발행하지 않는다.
            if not already:
                await _insert_activation_event(
                    conn, robot_id, "activation.armed", {})

    armed_map = arming.snapshot()
    return _row_to_out(row, armed_map)


@router.delete("/{robot_id}/arm", response_model=RobotOut)
async def disarm_robot(robot_id: str) -> RobotOut:
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


@router.post("/{robot_id}/heartbeat", status_code=204)
async def post_heartbeat(
    robot_id: str, body: RobotHeartbeatIn | None = None,
) -> Response:
    """로봇 생존 신호.

    게이트웨이의 이벤트 큐를 타지 않는 별도 경로다. 실패하면 로봇 쪽에서
    그냥 버린다 — 재전송하면 두절 중 쌓였다가 몰려와 신호의 의미가 사라진다.

    본문이 없다. 지금 필요한 정보는 '언제 왔는가' 뿐이고, 그건 서버가 안다.
    로봇 시계를 믿지 않아도 되므로 시계 어긋남의 영향도 받지 않는다.
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
    if body is not None:
        robot_runtime.update(
            robot_id, body.system_state, body.localization_active)
    return Response(status_code=204)


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
