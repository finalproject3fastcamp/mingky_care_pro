import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from ..db import get_pool
from ..event_codes import UNKNOWN_CODE, EventCodeRegistry
from ..ingest import ingest
from ..registry import get_registry
from ..schemas import EventIn, EventOut, EventPage, IngestResult, UnknownCodeOut

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

# 미등록 코드 집계.
#
# 별도 수집 테이블을 두지 않는다. ingest 규칙 4 가 모르는 코드도 버리지 않고
# system.unknown_event_code 마커를 함께 적재하므로, 데이터는 이미 events 에
# 다 있다. 테이블을 하나 더 만들면 같은 사실이 두 곳에 기록되고 둘이
# 어긋나는 순간 어느 쪽이 맞는지 아무도 답할 수 없게 된다.
#
# 미등록 코드는 정의상 드문 사건이라 이 집계가 무거워질 일은 없다.
_UNKNOWN_CODES_SQL = f"""
    SELECT
        payload->>'received_code' AS event_code,
        robot_id,
        count(*)         AS count,
        min(occurred_at) AS first_seen,
        max(occurred_at) AS last_seen
    FROM events
    WHERE event_code = '{UNKNOWN_CODE}'
      AND ($1::timestamptz IS NULL OR occurred_at >= $1)
    GROUP BY 1, 2
    ORDER BY last_seen DESC
    LIMIT $2
"""


@router.get("/unknown-codes", response_model=list[UnknownCodeOut])
async def list_unknown_codes(
    since: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[UnknownCodeOut]:
    """서버가 해석하지 못한 event_code 와 건수.

    비상정지 이력이 통째로 화면에서 빠져 있어도 지금까지는 아무 신호가
    없었다. 로봇 로그에만 남고 관제는 조용했다. 이 목록이 그 신호다.

    적재 자체는 되고 있으므로 데이터가 사라진 것은 아니다. 다만 등록되지
    않은 코드는 상태 갱신을 타지 않아 대시보드 판정에서 빠진다 —
    config/event_codes.yaml 을 갱신하면 그때부터 반영된다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_UNKNOWN_CODES_SQL, since, limit)

    return [
        UnknownCodeOut(
            # payload 에 received_code 가 없는 마커는 이론상 없지만, 있어도
            # 화면이 죽지 않도록 방어한다. 그 자체가 조사 단서다.
            event_code=row["event_code"] or "(코드 없음)",
            robot_id=row["robot_id"],
            count=row["count"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )
        for row in rows
    ]


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
