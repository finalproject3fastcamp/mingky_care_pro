import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from ..db import get_pool
from ..event_codes import EventCodeRegistry
from ..ingest import ingest
from ..registry import get_registry
from ..schemas import EventIn, EventOut, EventPage, IngestResult

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=IngestResult)
async def post_events(
    events: list[EventIn],
    registry: EventCodeRegistry = Depends(get_registry),
) -> IngestResult:
    """이벤트 배치를 적재한다.

    게이트웨이가 네트워크 두절 동안 쌓아둔 것을 몰아 보내므로 배치로 받는다.
    같은 배치를 여러 번 보내도 결과가 같다.

    미등록 event_code 가 섞여 있어도 200 을 돌려준다. 거부하면 게이트웨이가
    같은 배치를 무한히 재전송하게 되므로, 적재한 뒤 응답의 unknown_codes 로
    알린다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        return await ingest(conn, events, registry)


# level 은 텍스트라 그대로는 대소 비교가 안 된다.
# min_level 필터를 위해 순서를 부여한다.
_LEVEL_RANK = """
    CASE level WHEN 'info' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END
"""

_WHERE = f"""
    WHERE ($1::text        IS NULL OR robot_id    = $1)
      AND ($2::bigint      IS NULL OR session_id  = $2)
      AND ($3::int         IS NULL OR {_LEVEL_RANK} >= $3)
      AND ($4::text        IS NULL OR event_code LIKE $4)
      AND ($5::text        IS NULL OR source_node = $5)
      AND ($6::timestamptz IS NULL OR occurred_at >= $6)
      AND ($7::timestamptz IS NULL OR occurred_at <  $7)
"""

_COUNT_SQL = f"SELECT count(*) FROM events {_WHERE}"

_LIST_SQL = f"""
    SELECT event_id, robot_id, session_id, occurred_at, received_at,
           level, event_code, source_node, payload
    FROM events
    {_WHERE}
    -- 도착 순서가 아니라 발생 순서로 보여준다.
    -- 두절 후 몰아 들어온 이벤트가 화면 맨 위로 올라오면 안 된다.
    ORDER BY occurred_at DESC, event_id
    LIMIT $8 OFFSET $9
"""

_LEVEL_RANKS = {"info": 0, "warning": 1, "error": 2}


@router.get("", response_model=EventPage)
async def list_events(
    robot_id: str | None = None,
    session_id: int | None = None,
    min_level: str | None = Query(None, pattern="^(info|warning|error)$"),
    code_prefix: str | None = Query(None, description="예: nav. 또는 session."),
    source_node: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> EventPage:
    """엔지니어 대시보드의 이벤트 타임라인.

    시간대·레벨·노드별 필터를 지원한다 (docs/monitoring-spec.md 3.2).
    min_level 은 그 이상만 남긴다. warning 을 주면 warning 과 error 가 나온다.
    """
    like = f"{code_prefix}%" if code_prefix else None
    rank = _LEVEL_RANKS.get(min_level) if min_level else None
    args = (robot_id, session_id, rank, like, source_node, since, until)

    pool = get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(_COUNT_SQL, *args)
        rows = await conn.fetch(_LIST_SQL, *args, limit, offset)

    return EventPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            EventOut(
                event_id=row["event_id"],
                robot_id=row["robot_id"],
                session_id=row["session_id"],
                occurred_at=row["occurred_at"],
                received_at=row["received_at"],
                level=row["level"],
                event_code=row["event_code"],
                source_node=row["source_node"],
                # payload 는 JSONB 지만 asyncpg 는 문자열로 돌려준다.
                payload=json.loads(row["payload"]) if row["payload"] else {},
            )
            for row in rows
        ],
    )
