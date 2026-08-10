"""로봇 명령 하향 경로.

관제가 로봇에 DDS 로 목표를 보내던 것을 HTTP 로 바꾼다. 로봇이 물어보러
오는 방식이라 로봇이 어느 네트워크에 있든(NAT 안이든 클라우드 너머든)
동작한다. 설계 근거는 app/orders.py 주석 참고.
"""

from fastapi import APIRouter, HTTPException, Response

from .. import orders
from ..db import get_pool
from ..schemas import OrderAck, OrderIn, OrderOut

router = APIRouter(prefix="/robots", tags=["orders"])


async def _require_robot(robot_id: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        known = await conn.fetchval(
            "SELECT 1 FROM robots WHERE robot_id = $1 AND is_active", robot_id)
    if not known:
        raise HTTPException(status_code=404, detail="unknown or inactive robot")


@router.post("/{robot_id}/orders", response_model=OrderOut, status_code=201)
async def create_order(robot_id: str, body: OrderIn) -> OrderOut:
    """명령을 건다. 대시보드가 호출한다.

    로봇당 대기 명령은 하나다. 아직 안 받아간 것이 있으면 덮어쓴다 —
    안내 로봇에게 유효한 목적지는 최신 하나뿐이고, 밀린 목적지를 순서대로
    소화하는 쪽이 오히려 위험하다.
    """
    await _require_robot(robot_id)
    return orders.put(robot_id, body.command, body.argument)


@router.get("/{robot_id}/orders/next", response_model=OrderOut | None)
async def next_order(robot_id: str) -> OrderOut | None:
    """로봇이 주기적으로 물어본다. 없으면 null.

    **꺼내 보기만 하고 지우지 않는다.** 응답이 무선에서 유실되면 로봇은
    못 받았는데 서버는 보냈다고 믿게 되고, 명령이 증발한다. 지우는 것은
    로봇이 ack 를 보냈을 때만이다.

    로봇 존재 확인을 하지 않는다. 이 경로는 3초마다 호출되므로 매번 DB 를
    때리면 폴링 비용이 그대로 DB 부하가 된다. 등록되지 않은 robot_id 는
    애초에 명령이 걸릴 수 없으므로 항상 null 이 나간다.
    """
    return orders.peek(robot_id)


@router.post("/{robot_id}/orders/{order_id}/ack", status_code=204)
async def ack_order(robot_id: str, order_id: str, body: OrderAck) -> Response:
    """로봇이 받아서 실행에 넣었음을 알린다. 여기서 지운다.

    경로의 order_id 와 본문의 order_id 가 같아야 한다. 로봇이 엉뚱한 것을
    지우는 사고를 막는다.

    이미 지워졌거나 그 사이 새 명령으로 덮어써졌으면 404 다. 로봇은 이
    응답을 실패로 취급하지 않아도 된다 — 다음 폴링에서 최신 명령을 받는다.
    """
    if str(body.order_id) != order_id:
        raise HTTPException(status_code=400, detail="order_id mismatch")
    if not orders.ack(robot_id, body.order_id):
        raise HTTPException(status_code=404, detail="order not pending")
    return Response(status_code=204)


@router.get("/orders/pending", response_model=list[OrderOut])
async def list_pending() -> list[OrderOut]:
    """대기 중인 전체 명령. 디버깅용.

    로봇당 안전·주행 슬롯이 따로 있어 두 건이 함께 나올 수 있다.
    peek 과 같은 순서(안전 먼저)로 담긴다.
    """
    return [order for orders_ in orders.snapshot().values() for order in orders_]
