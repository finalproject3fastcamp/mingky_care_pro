"""형상 조회 — 4대가 무엇으로 돌고 있는가 (§7.2 · 로드맵 10).

판정은 `app/fleet_config.py` 에 있고 여기에는 HTTP 와 조회만 있다.
"""

from fastapi import APIRouter

from .. import dispense, fleet_config
from ..db import get_pool
from ..schemas import FleetConfigOut

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("/config", response_model=FleetConfigOut)
async def get_fleet_config() -> FleetConfigOut:
    """로봇별 코드 커밋 · 맵 지문 · 정책 체크포인트와 그 불일치.

    로봇 목록(GET /robots)에 얹지 않는다. 저쪽은 3초 폴링이고 이건 몇 시간에
    한 번 바뀌는 값이다 — 같은 응답에 넣으면 팔 2대분 events 집계가 3초마다
    돈다. 화면도 이 카드만 느리게 폴링한다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(fleet_config.CONFIG_SQL)
        # 팔의 정책은 events 에서 접는다(§6.2). 팔이 없는 설치에서는 이
        # 쿼리를 아예 보내지 않는다.
        details = {}
        if any(row["robot_type"] == "manipulator" for row in rows):
            details = dispense.summarize(
                await conn.fetch(dispense.DETAIL_SQL, dispense.EVENT_LIMIT))

    return fleet_config.summarize(rows, details)
