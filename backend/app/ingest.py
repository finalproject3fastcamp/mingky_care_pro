"""이벤트 적재와 그에 따른 상태 갱신.

config/event_codes.yaml 3절의 규칙을 코드로 옮긴 곳이다.

  1. 배치를 occurred_at 순으로 정렬해 적용한다.
     네트워크 두절 후 몰아 보내면 도착 순서가 발생 순서와 다를 수 있고,
     session_steps 의 CHECK (completed_at IS NULL OR arrived_at IS NOT NULL)
     가 순서 오류를 거부한다. 그 제약은 안전망이므로 완화하지 않는다.

  2. 적재와 상태 갱신을 같은 트랜잭션에 넣는다.
     따로 커밋되면 이벤트만 남고 상태가 안 바뀌는 구멍이 생긴다.

  3. 재전송에도 멱등해야 한다.
     ON CONFLICT (event_id) DO NOTHING 으로 중복 적재를 막고,
     새로 들어온 이벤트에 대해서만 상태를 갱신한다.
     UPDATE 에도 IS NULL 조건을 걸어 두 겹으로 막는다.

  4. 미등록 코드는 거부하지 않는다.
     그대로 적재하고 system.unknown_event_code 를 추가로 남긴다.
     기록을 잃지 않으면서 문서 누락이 대시보드에 드러난다.
"""

import json
import logging
import uuid

import asyncpg
from asyncpg.exceptions import IntegrityConstraintViolationError

from .event_codes import UNKNOWN_CODE, EventCodeRegistry
from .schemas import EventIn, IngestResult

log = logging.getLogger("mingky")

_INSERT = """
    INSERT INTO events (event_id, robot_id, session_id, occurred_at,
                        level, event_code, source_node, payload)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (event_id) DO NOTHING
    RETURNING event_id
"""

# visit_name 으로 단계를 찾지 않는다. 한 세션에서 같은 장소를 두 번 방문할 수
# 있어(진료실 초진·판독) 이름만으로는 어느 단계인지 결정되지 않는다.
# 아직 도착하지 않은 가장 이른 단계가 곧 현재 단계다.
_ARRIVED = """
    UPDATE session_steps SET arrived_at = $1
    WHERE session_step_id = (
        SELECT session_step_id FROM session_steps
        WHERE session_id = $2 AND arrived_at IS NULL
        ORDER BY step_order LIMIT 1
    )
"""

_COMPLETED = """
    UPDATE session_steps SET completed_at = $1, completed_source = $2
    WHERE session_id = $3 AND step_order = $4 AND completed_at IS NULL
"""

_ENDED = """
    UPDATE guidance_sessions SET ended_at = $1, end_reason = $2
    WHERE session_id = $3 AND ended_at IS NULL
"""


async def _insert(conn: asyncpg.Connection, event: EventIn) -> bool:
    """새로 들어갔으면 True, 중복이면 False."""
    row = await conn.fetchrow(
        _INSERT,
        event.event_id,
        event.robot_id,
        # session_id 0 은 '세션 없음' 이라 FK 를 걸 수 없다. NULL 로 저장한다.
        event.session_id or None,
        event.occurred_at,
        event.level,
        event.event_code,
        event.source_node,
        json.dumps(event.payload, ensure_ascii=False),
    )
    return row is not None


async def _apply_state(conn: asyncpg.Connection, event: EventIn) -> int:
    """이벤트에 따른 상태 갱신. 갱신된 행 수를 돌려준다."""
    payload = event.payload

    if event.event_code == "nav.goal_succeeded":
        result = await conn.execute(_ARRIVED, event.occurred_at, event.session_id)
    elif event.event_code == "session.step_completed":
        result = await conn.execute(
            _COMPLETED, event.occurred_at, payload.get("source"),
            event.session_id, payload.get("step_order"))
    elif event.event_code == "session.ended":
        result = await conn.execute(
            _ENDED, event.occurred_at, payload.get("end_reason"), event.session_id)
    else:
        return 0

    return int(result.split()[-1]) if result.startswith("UPDATE") else 0


async def _apply_state_safely(conn: asyncpg.Connection, event: EventIn,
                              result: IngestResult) -> int:
    """상태 갱신을 세이브포인트 안에서 실행한다.

    시계가 어긋난 로봇이 보내면 003 의 CHECK 에 걸릴 수 있다.
    예) ended_at 이 started_at 보다 앞선 session.ended.

    그때 배치 전체를 실패시키면 게이트웨이가 같은 배치를 무한히 재전송한다.
    미등록 코드를 거부하지 않는 것과 같은 이유로, 이벤트는 남기고
    상태 갱신만 건너뛴 뒤 응답으로 알린다. 제약 자체는 완화하지 않는다.
    """
    try:
        # asyncpg 의 중첩 트랜잭션은 세이브포인트로 동작한다.
        async with conn.transaction():
            return await _apply_state(conn, event)
    except IntegrityConstraintViolationError as exc:
        log.warning("상태 갱신 거부: %s (%s) — %s",
                    event.event_code, event.event_id, exc)
        result.rejected_updates.append(event.event_code)
        return 0


def _unknown_marker(event: EventIn) -> EventIn:
    """미등록 코드를 받았다는 사실 자체를 이벤트로 남긴다."""
    return EventIn(
        event_id=uuid.uuid4(),
        robot_id=event.robot_id,
        session_id=event.session_id,
        occurred_at=event.occurred_at,
        level="error",
        event_code=UNKNOWN_CODE,
        source_node="backend.ingest",
        payload={"received_code": event.event_code},
    )


async def ingest(conn: asyncpg.Connection, events: list[EventIn],
                 registry: EventCodeRegistry) -> IngestResult:
    result = IngestResult(received=len(events), inserted=0, duplicates=0,
                          state_updates=0)

    # 규칙 1 — 도착 순서가 아니라 발생 순서로 적용한다.
    ordered = sorted(events, key=lambda e: e.occurred_at)

    # 규칙 2 — 적재와 갱신을 한 트랜잭션에.
    async with conn.transaction():
        for event in ordered:
            known = registry.is_known(event.event_code)

            # 규칙 4 — 모르는 코드도 버리지 않는다.
            if not known and event.event_code not in result.unknown_codes:
                result.unknown_codes.append(event.event_code)

            if await _insert(conn, event):
                result.inserted += 1
                # 규칙 3 — 새로 들어온 이벤트에 대해서만 상태를 건드린다.
                if known:
                    result.state_updates += await _apply_state_safely(
                        conn, event, result)
            else:
                result.duplicates += 1

            if not known:
                await _insert(conn, _unknown_marker(event))

    return result
