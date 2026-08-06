"""로봇 목록과 최근 상태."""

from fastapi import APIRouter, HTTPException, Response

from .. import heartbeat
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

    # 생존 여부는 DB 가 아니라 메모리에 있다. heartbeat 모듈 주석 참고.
    seen = heartbeat.snapshot()
    result = []
    for row in rows:
        last_seen, offline = seen.get(row["robot_id"], (None, False))
        result.append(RobotOut(
            **dict(row),
            last_seen_at=last_seen,
            # 한 번도 신호를 안 보낸 로봇은 offline 이 아니라 unknown 이다.
            # OMX 는 ROS 가 아니라 LeRobot 으로 제어해서 heartbeat 를 보낼
            # 수단이 없다. 이걸 두절로 표시하면 타임라인이 오탐으로 덮인다.
            link_state=("unknown" if last_seen is None
                        else "offline" if offline else "online"),
        ))
    return result


@router.post("/{robot_id}/heartbeat", status_code=204)
async def post_heartbeat(robot_id: str) -> Response:
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
    return Response(status_code=204)
