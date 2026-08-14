"""QR 스캔 → 안내 세션 시작.

이 엔드포인트만 요청-응답 방식이다. 로봇이 session_id 를 즉시 받아야
이후 발행하는 모든 이벤트에 달고 다닐 수 있는데, 이벤트는 단방향이라
응답을 줄 수 없기 때문이다 (docs/monitoring-spec.md 2장).

상태를 바꾸는 경로는 여전히 하나다. 세션 생성도 여기서만 일어난다.
"""

import json
import uuid
from datetime import datetime, timezone

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter, HTTPException

from .. import arming, robot_runtime
from ..db import get_pool
from ..schemas import Patient, QrScanRequest, ScheduleStep, TodaySchedule

router = APIRouter(prefix="/qr", tags=["qr"])

_PATIENT_SQL = """
    SELECT p.patient_id, p.name, p.gender, p.birth_date,
           c.condition_name, c.condition_id
    FROM patients p
    JOIN conditions c USING (condition_id)
    WHERE p.patient_id = $1
"""

# 재스캔에 안전하도록 기존 활성 세션을 먼저 찾는다.
# 로봇이 재부팅해도 같은 세션으로 복귀한다.
_ACTIVE_BY_PATIENT_SQL = """
    SELECT session_id, robot_id FROM guidance_sessions
    WHERE patient_id = $1 AND ended_at IS NULL
"""

# 로봇 기준으로도 확인해야 한다. 003 의 uq_active_session_robot 이
# 한 로봇의 동시 세션을 막는데, 미리 걸러내지 않으면 INSERT 가 터져
# 500 이 나가고 원인이 드러나지 않는다.
_ACTIVE_BY_ROBOT_SQL = """
    SELECT session_id, patient_id FROM guidance_sessions
    WHERE robot_id = $1 AND ended_at IS NULL
"""

_CREATE_SESSION_SQL = """
    INSERT INTO guidance_sessions (patient_id, robot_id, condition_id, marker_id)
    VALUES ($1, $2, $3, $4)
    RETURNING session_id
"""

# examination_steps(마스터)를 session_steps(스냅샷)로 복사한다.
# 조회 때마다 마스터를 조인하면, 나중에 검사 순서가 바뀔 때
# 과거 안내 기록의 일정까지 소급해서 달라진다.
_SNAPSHOT_STEPS_SQL = """
    INSERT INTO session_steps (session_id, step_order, visit_name)
    SELECT $1, step_order, examination_name
    FROM examination_steps
    WHERE condition_id = $2
"""

_STEPS_SQL = """
    SELECT step_order, visit_name, arrived_at, completed_at
    FROM session_steps
    WHERE session_id = $1
    ORDER BY step_order
"""

# 현재 단계는 컬럼으로 저장하지 않고 뷰에서 계산한다 (003).
_CURRENT_STEP_SQL = """
    SELECT step_order FROM session_current_step WHERE session_id = $1
"""

# 새 세션이 만들어질 때 backend 가 activation.consumed 를 남긴다.
# arming 상태 자체는 인메모리라 events 로그가 유일한 히스토리다.
_INSERT_EVENT = """
    INSERT INTO events (event_id, robot_id, session_id, occurred_at,
                        level, event_code, source_node, payload)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (event_id) DO NOTHING
"""


@router.post("/scan", response_model=TodaySchedule)
async def scan(payload: QrScanRequest) -> TodaySchedule:
    """QR 스캔 → 세션 조회/생성 + 오늘 일정."""
    pool = get_pool()
    async with pool.acquire() as conn:
        patient_row = await conn.fetchrow(_PATIENT_SQL, payload.patient_id)
        if patient_row is None:
            raise HTTPException(status_code=404, detail="patient not found")

        # arming 은 인메모리라 트랜잭션 밖에서 검사한다. 재스캔 (같은 환자·같은
        # 로봇) 은 disarm 이후 두 번째 스캔이라 armed 가 아닐 수 있어, 이미
        # 활성 세션이 있는 경우는 arming 검사를 건너뛴다.
        arming_ok = arming.is_armed(payload.robot_id)

        async with conn.transaction():
            by_patient = await conn.fetchrow(
                _ACTIVE_BY_PATIENT_SQL, payload.patient_id)
            by_robot = await conn.fetchrow(
                _ACTIVE_BY_ROBOT_SQL, payload.robot_id)

            if by_patient is not None:
                if by_patient["robot_id"] != payload.robot_id:
                    raise HTTPException(
                        status_code=409,
                        detail=f"patient already guided by {by_patient['robot_id']}")
                # 같은 로봇의 재스캔. 기존 세션을 그대로 돌려준다.
                session_id = by_patient["session_id"]
                created = False
            elif by_robot is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"robot busy with {by_robot['patient_id']}")
            else:
                runtime = robot_runtime.snapshot().get(payload.robot_id)
                if runtime is not None and runtime.guide_robot_state in (
                        "returning_to_dock", "paused"):
                    raise HTTPException(
                        status_code=409,
                        detail="robot unavailable while returning to dock")
                # 새 세션은 armed 상태에서만 만든다. 의료진이 미리 로봇을
                # 활성화해야 안내가 시작되는 시나리오를 강제한다.
                if not arming_ok:
                    raise HTTPException(
                        status_code=409, detail="robot not armed")
                try:
                    session_id = await conn.fetchval(
                        _CREATE_SESSION_SQL, payload.patient_id, payload.robot_id,
                        patient_row["condition_id"], payload.marker_id)
                except UniqueViolationError as exc:
                    # 위 두 검사를 빠져나온 경합. 마커 중복이 대표적이다.
                    # 동시 요청이면 여기서 걸린다.
                    raise HTTPException(
                        status_code=409, detail="session conflict") from exc
                await conn.execute(
                    _SNAPSHOT_STEPS_SQL, session_id, patient_row["condition_id"])
                # arming 이 세션으로 소비됨. 이벤트는 여기서만 남기고, dict
                # 정리는 트랜잭션 커밋 후에 한다 (아래).
                await conn.execute(
                    _INSERT_EVENT,
                    uuid.uuid4(), payload.robot_id, session_id,
                    datetime.now(timezone.utc),
                    "info", "activation.consumed", "backend.qr",
                    json.dumps({}, ensure_ascii=False),
                )
                created = True

            step_rows = await conn.fetch(_STEPS_SQL, session_id)
            current = await conn.fetchval(_CURRENT_STEP_SQL, session_id)

        # 커밋 성공 후에 인메모리 arming 을 지운다. 트랜잭션이 롤백되면
        # arming 은 그대로 남아 재시도할 수 있다.
        if created:
            arming.disarm(payload.robot_id)

    return TodaySchedule(
        session_id=session_id,
        patient=Patient(
            patient_id=patient_row["patient_id"],
            name=patient_row["name"],
            gender=patient_row["gender"],
            birth_date=patient_row["birth_date"],
            condition_name=patient_row["condition_name"],
        ),
        steps=[ScheduleStep(**dict(row)) for row in step_rows],
        # 모든 단계가 끝났으면 뷰가 아무것도 돌려주지 않는다.
        current_step_order=current or 0,
    )
