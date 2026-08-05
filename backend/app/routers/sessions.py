"""안내 세션 조회.

의료진 대시보드(/medical)가 읽는 경로다.
"""

from fastapi import APIRouter, HTTPException

from ..db import get_pool
from ..schemas import PatientSummary, SessionOut, SessionStep

router = APIRouter(prefix="/sessions", tags=["sessions"])

# age 는 컬럼이 아니라 계산값이다. 002 에서 지웠다.
# 저장해두면 시간이 지나면서 birth_date 와 갈라진다.
_SESSION_COLUMNS = """
    gs.session_id, gs.robot_id, gs.marker_id,
    gs.started_at, gs.ended_at, gs.end_reason,
    p.patient_id, p.name, p.gender, p.birth_date,
    date_part('year', age(p.birth_date))::int AS age,
    c.condition_name
"""

_ACTIVE_SQL = f"""
    SELECT {_SESSION_COLUMNS},
           cs.step_order AS current_step_order,
           cs.visit_name AS current_visit
    FROM guidance_sessions gs
    JOIN patients p USING (patient_id)
    JOIN conditions c ON c.condition_id = gs.condition_id
    -- 진행 중인 세션만 담는 뷰다. 끝난 세션에는 '현재' 가 없다.
    LEFT JOIN session_current_step cs USING (session_id)
    WHERE gs.ended_at IS NULL
    ORDER BY gs.started_at
"""

_ONE_SQL = f"""
    SELECT {_SESSION_COLUMNS},
           cs.step_order AS current_step_order,
           cs.visit_name AS current_visit
    FROM guidance_sessions gs
    JOIN patients p USING (patient_id)
    JOIN conditions c ON c.condition_id = gs.condition_id
    LEFT JOIN session_current_step cs USING (session_id)
    WHERE gs.session_id = $1
"""

_STEPS_SQL = """
    SELECT session_id, step_order, visit_name,
           arrived_at, completed_at, completed_source
    FROM session_steps
    WHERE session_id = ANY($1::bigint[])
    ORDER BY session_id, step_order
"""


def _to_session(row, steps) -> SessionOut:
    return SessionOut(
        session_id=row["session_id"],
        robot_id=row["robot_id"],
        marker_id=row["marker_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        end_reason=row["end_reason"],
        patient=PatientSummary(
            patient_id=row["patient_id"],
            name=row["name"],
            gender=row["gender"],
            birth_date=row["birth_date"],
            age=row["age"],
            condition_name=row["condition_name"],
        ),
        steps=steps,
        current_step_order=row["current_step_order"],
        current_visit=row["current_visit"],
    )


def _group_steps(step_rows) -> dict[int, list[SessionStep]]:
    grouped: dict[int, list[SessionStep]] = {}
    for row in step_rows:
        grouped.setdefault(row["session_id"], []).append(
            SessionStep(
                step_order=row["step_order"],
                visit_name=row["visit_name"],
                arrived_at=row["arrived_at"],
                completed_at=row["completed_at"],
                completed_source=row["completed_source"],
            )
        )
    return grouped


@router.get("/active", response_model=list[SessionOut])
async def list_active() -> list[SessionOut]:
    """진행 중인 안내 목록. 의료진 화면이 주기적으로 읽는다."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_ACTIVE_SQL)
        if not rows:
            return []
        # 세션마다 따로 조회하면 N+1 이 된다. 한 번에 가져와 나눈다.
        steps = _group_steps(
            await conn.fetch(_STEPS_SQL, [r["session_id"] for r in rows]))
    return [_to_session(r, steps.get(r["session_id"], [])) for r in rows]


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: int) -> SessionOut:
    """단일 세션. 끝난 세션도 조회된다."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_ONE_SQL, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        steps = _group_steps(await conn.fetch(_STEPS_SQL, [session_id]))
    return _to_session(row, steps.get(session_id, []))
