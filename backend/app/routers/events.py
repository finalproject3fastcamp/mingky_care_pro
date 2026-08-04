from fastapi import APIRouter, Depends

from ..db import get_pool
from ..event_codes import EventCodeRegistry
from ..ingest import ingest
from ..registry import get_registry
from ..schemas import EventIn, IngestResult

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
