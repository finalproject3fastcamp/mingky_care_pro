"""안내 세션 조회.

의료진 대시보드(/medical)가 읽는 경로다.
"""

import json

from fastapi import APIRouter, HTTPException

from ..db import get_pool
from ..schemas import (
    EventOut,
    PatientSummary,
    SessionEndingContextOut,
    SessionOut,
    SessionStep,
)

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

# 종료 직전 창. 새 테이블도 새 컬럼도 필요 없다 — events 에 session_id 와
# occurred_at 이 이미 있으므로 쿼리만 추가하면 된다.
#
# ASC 로 뽑는다. 목록 조회(GET /events)는 최신이 위여야 하지만 인과는
# 시간 순으로 읽어야 "A 다음에 B" 가 보인다.
ENDING_WINDOW_SEC = 60

_ENDING_EVENTS_SQL = """
    SELECT event_id, robot_id, session_id, occurred_at, received_at,
           level, event_code, source_node, payload
    FROM events
    WHERE session_id = $1
      AND occurred_at BETWEEN $2 - ($3 || ' seconds')::interval AND $2
    ORDER BY occurred_at
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


@router.get("/{session_id}/ending-context",
            response_model=SessionEndingContextOut)
async def get_ending_context(session_id: int) -> SessionEndingContextOut:
    """세션이 왜 그렇게 끝났는지 — 종료 직전 창의 이벤트.

    end_reason 만 보면 "배터리 부족으로 끝났다" 까지다. 그게 갑자기 벌어진
    일인지 40초 전부터 예고돼 있었는지는 알 수 없는데, 후자면 임계값이
    늦은 것이라 대응이 다르다.

    아직 안 끝난 세션에는 창이 없다. 200 으로 빈 결과를 돌려준다 —
    404 는 "세션이 없다" 와 구분이 안 된다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT session_id, ended_at, end_reason "
            "FROM guidance_sessions WHERE session_id = $1",
            session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")

        ended_at = row["ended_at"]
        if ended_at is None:
            return SessionEndingContextOut(
                session_id=session_id, end_reason=row["end_reason"])

        event_rows = await conn.fetch(
            _ENDING_EVENTS_SQL, session_id, ended_at, str(ENDING_WINDOW_SEC))

    events = [
        EventOut(
            event_id=r["event_id"],
            robot_id=r["robot_id"],
            session_id=r["session_id"],
            occurred_at=r["occurred_at"],
            received_at=r["received_at"],
            level=r["level"],
            event_code=r["event_code"],
            source_node=r["source_node"],
            # payload 는 JSONB 지만 asyncpg 는 문자열로 돌려준다.
            payload=json.loads(r["payload"]) if r["payload"] else {},
        )
        for r in event_rows
    ]

    # 인과의 시작점은 창 안에서 가장 이른 경고/오류다. info 는 제외한다 —
    # 정상 진행 로그가 원인으로 지목되면 오히려 판단을 흐린다.
    lead = next((e for e in events if e.level in ("warning", "error")), None)

    return SessionEndingContextOut(
        session_id=session_id,
        ended_at=ended_at,
        end_reason=row["end_reason"],
        lead_event_code=lead.event_code if lead else None,
        lead_event_at=lead.occurred_at if lead else None,
        lead_sec=(
            round((ended_at - lead.occurred_at).total_seconds())
            if lead else None),
        events=events,
    )
