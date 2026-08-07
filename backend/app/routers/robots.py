"""로봇 목록, 최근 상태, 그리고 의료진이 로봇을 활성화(arming)하는 경로."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from .. import arming
from ..db import get_pool
from ..schemas import RobotArmingOut, RobotOut

router = APIRouter(prefix="/robots", tags=["robots"])

# 의료진이 로봇을 고를 때 최소로 요구하는 배터리 잔량.
# 로봇 내부 warn 임계값(guide_manager: 6.9V) 과 별개의 UI 문턱이다.
# 안내가 시작되기 전이라 여유를 넉넉히 잡는다.
MIN_BATTERY_PERCENT = 40

# 배터리는 마지막으로 저장된 표본이다. 실시간 값이 아니다.
# 화면에 띄우는 실시간 상태는 DB 에 저장하지 않기로 했다(3~5초마다 덮어쓰는
# 값을 영속화할 이유가 없다). 여기서는 2분 주기로 쌓인 로그의 최신 행을 쓴다.
_SQL = """
    SELECT r.robot_id, r.robot_type, r.display_name, r.domain_id, r.is_active,
           b.voltage        AS battery_voltage,
           b.battery_percent,
           b.recorded_at    AS battery_recorded_at,
           s.session_id     AS active_session_id,
           s.patient_id     AS active_patient_id
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


def _row_to_out(row, armed_map: dict[str, datetime]) -> RobotOut:
    return RobotOut(
        **dict(row),
        armed_at=armed_map.get(row["robot_id"]),
    )


@router.get("", response_model=list[RobotOut])
async def list_robots() -> list[RobotOut]:
    """모든 로봇 목록 + 최근 배터리 + 활성 세션 + 활성화 여부."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SQL)
    armed_map = arming.snapshot()
    return [_row_to_out(row, armed_map) for row in rows]


@router.post("/{robot_id}/arm", response_model=RobotOut)
async def arm_robot(robot_id: str) -> RobotOut:
    """의료진 대시보드가 로봇 하나를 골라 활성화한다.

    검증:
      - 등록된 로봇이며 is_active
      - robot_type = 'mobile' (조제 스테이션은 대상 아님)
      - 진행 중 세션 없음
      - 최신 배터리 >= MIN_BATTERY_PERCENT (표본이 아직 없으면 거부)

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
            percent = row["battery_percent"]
            if percent is None or percent < MIN_BATTERY_PERCENT:
                raise HTTPException(
                    status_code=409,
                    detail=(f"battery {percent if percent is not None else 'unknown'}%"
                            f" below {MIN_BATTERY_PERCENT}%"))

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
