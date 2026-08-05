"""로봇 목록과 최근 상태."""

from fastapi import APIRouter

from ..db import get_pool
from ..schemas import RobotOut

router = APIRouter(prefix="/robots", tags=["robots"])

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


@router.get("", response_model=list[RobotOut])
async def list_robots() -> list[RobotOut]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SQL)
    return [RobotOut(**dict(row)) for row in rows]
