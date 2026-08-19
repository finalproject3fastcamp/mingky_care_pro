"""로봇 명령 하향 경로.

관제가 로봇에 DDS 로 목표를 보내던 것을 HTTP 로 바꾼다. 로봇이 물어보러
오는 방식이라 로봇이 어느 네트워크에 있든(NAT 안이든 클라우드 너머든)
동작한다. 설계 근거는 app/orders.py 주석 참고.
"""

import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import control_audit, orders, robot_runtime
from ..actor import Actor, actor_from_header
from ..db import get_pool
from ..schemas import OrderAck, OrderIn, OrderOut

router = APIRouter(prefix="/robots", tags=["orders"])

MIN_NAVIGATION_SPEED = Decimal("0.05")
MAX_NAVIGATION_SPEED = Decimal("0.25")
NAVIGATION_SPEED_STEP = Decimal("0.01")


def _validate_navigation_speed(argument: str) -> None:
    try:
        speed = Decimal(argument)
        valid_step = speed.is_finite() and speed % NAVIGATION_SPEED_STEP == 0
    except (InvalidOperation, ValueError):
        valid_step = False
        speed = Decimal("0")
    if not valid_step or not MIN_NAVIGATION_SPEED <= speed <= MAX_NAVIGATION_SPEED:
        raise HTTPException(
            status_code=422,
            detail="navigation speed must be 0.05..0.25 m/s in 0.01 steps",
        )


def _validate_low_obstacle_mode(argument: str) -> None:
    if argument not in ("disabled", "sidestep"):
        raise HTTPException(
            status_code=422,
            detail="low obstacle mode must be disabled or sidestep",
        )


async def _require_robot(robot_id: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        known = await conn.fetchval(
            "SELECT 1 FROM robots WHERE robot_id = $1 AND is_active", robot_id)
    if not known:
        raise HTTPException(status_code=404, detail="unknown or inactive robot")


async def _require_active_session(
        robot_id: str, argument: str, command: str = "start_guidance") -> None:
    """세션 명령이 현재 로봇의 활성 세션을 정확히 가리키는지 확인한다."""
    try:
        session_id = int(argument)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"{command} requires a session_id") from exc
    if session_id <= 0:
        raise HTTPException(
            status_code=422,
            detail=f"{command} requires a positive session_id")

    pool = get_pool()
    async with pool.acquire() as conn:
        matches = await conn.fetchval(
            """
            SELECT 1
            FROM guidance_sessions
            WHERE session_id = $1 AND robot_id = $2 AND ended_at IS NULL
            """,
            session_id,
            robot_id,
        )
    if not matches:
        raise HTTPException(
            status_code=409,
            detail="session is not active for this robot",
        )


@router.post("/{robot_id}/orders", response_model=OrderOut, status_code=201)
async def create_order(
    robot_id: str,
    body: OrderIn,
    actor: Actor = Depends(actor_from_header),
) -> OrderOut:
    """명령을 건다. 대시보드가 호출한다.

    로봇당 대기 명령은 하나다. 아직 안 받아간 것이 있으면 덮어쓴다 —
    안내 로봇에게 유효한 목적지는 최신 하나뿐이고, 밀린 목적지를 순서대로
    소화하는 쪽이 오히려 위험하다.

    `X-Actor` 헤더로 실행자를 남긴다. 없어도 거부하지 않는다 — 감사 누락이
    제어를 막으면 안 된다(actor.py). 익명으로 기록되고 fleet 탭이 그 비율을
    드러낸다.
    """
    await _require_robot(robot_id)
    if body.command in ("start_guidance", "cancel_guidance"):
        await _require_active_session(robot_id, body.argument, body.command)
    if body.command == "localize" and body.argument != "run":
        raise HTTPException(
            status_code=422, detail="localize requires argument 'run'")
    if body.command == "fire_alarm_reset" and body.argument != "run":
        raise HTTPException(
            status_code=422, detail="fire_alarm_reset requires argument 'run'")
    if body.command == "fire_alarm_reset":
        runtime = robot_runtime.snapshot().get(robot_id)
        if runtime is not None and runtime.fire_alarm_active is False:
            raise HTTPException(
                status_code=409, detail="fire alarm is not active")
    if body.command in ("cancel_navigation", "cancel_fire_evacuation"):
        if body.argument != "run":
            raise HTTPException(
                status_code=422,
                detail=f"{body.command} requires argument 'run'",
            )
    if body.command in ("set_navigation_speed", "set_low_obstacle_mode"):
        if body.command == "set_navigation_speed":
            _validate_navigation_speed(body.argument)
        else:
            _validate_low_obstacle_mode(body.argument)
        runtime = robot_runtime.snapshot().get(robot_id)
        if runtime is not None:
            if runtime.system_state != "active":
                raise HTTPException(
                    status_code=409,
                    detail=f"robot system is {runtime.system_state}",
                )
            if runtime.localization_active or runtime.fire_alarm_active:
                raise HTTPException(
                    status_code=409,
                    detail="robot safety operation is active",
                )
        pool = get_pool()
        async with pool.acquire() as conn:
            active_session = await conn.fetchval(
                """
                SELECT session_id FROM guidance_sessions
                WHERE robot_id = $1 AND ended_at IS NULL
                """,
                robot_id,
            )
        if active_session is not None:
            raise HTTPException(
                status_code=409,
                detail=f"robot busy with session {active_session}",
            )
    if body.command.startswith("system_"):
        if body.argument != "run":
            raise HTTPException(
                status_code=422, detail="system command requires argument 'run'")
        if body.command in ("system_stop", "system_restart"):
            runtime = robot_runtime.snapshot().get(robot_id)
            if runtime is not None and runtime.localization_active:
                raise HTTPException(
                    status_code=409,
                    detail="automatic localization is active",
                )
            pool = get_pool()
            async with pool.acquire() as conn:
                active_session = await conn.fetchval(
                    """
                    SELECT session_id FROM guidance_sessions
                    WHERE robot_id = $1 AND ended_at IS NULL
                    """,
                    robot_id,
                )
            if active_session is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"robot busy with session {active_session}",
                )
            # 재시작 직후 Nav2가 준비되기 전에 예전 목표가 전달되거나, 중지
            # 뒤 다시 켰을 때 낡은 목표가 갑자기 실행되는 일을 막는다.
            orders.clear_motion(robot_id)
    if body.command in (
            "goto", "goto_pose", "start_guidance", "cancel_guidance",
            "cancel_navigation", "localize", "fire_alarm_reset",
            "cancel_fire_evacuation"):
        runtime = robot_runtime.snapshot().get(robot_id)
        if runtime is not None and runtime.system_state != "active":
            raise HTTPException(
                status_code=409,
                detail=f"robot system is {runtime.system_state}",
            )
    if body.command in ("cancel_guidance", "cancel_navigation"):
        # 취소보다 먼저 적재됐지만 아직 로봇이 받지 않은 출발·목적지 명령이
        # 취소 직후 실행되는 것을 막는다.
        orders.clear_motion(robot_id)

    # 검증을 통과한 뒤, 명령이 걸리기 전에 남긴다. 순서 근거는
    # control_audit 모듈 주석 — 요약하면 SLO 는 실제보다 좋아 보이는 쪽으로
    # 틀리면 안 된다. 거부된 명령(위의 422·409)은 개입이 아니므로 여기 못 온다.
    #
    # 식별자를 여기서 만드는 이유는 감사 행이 명령을 가리켜야 하기 때문이다.
    # put() 이 만들면 기록 시점에는 아직 그 값이 없다.
    order_id = uuid.uuid4()
    await control_audit.record(
        robot_id, body.command, actor,
        argument=body.argument, order_id=order_id)

    return orders.put(robot_id, body.command, body.argument, order_id=order_id)


@router.get("/{robot_id}/orders/next", response_model=OrderOut | None)
async def next_order(
    robot_id: str,
    wait: float = Query(
        0.0, ge=0.0, le=50.0,
        description="명령이 없을 때 최대 몇 초까지 응답을 붙들고 기다릴지"),
) -> OrderOut | None:
    """로봇이 물어본다. 없으면 null.

    wait 를 주면 **롱폴링**이다. 명령이 없어도 바로 null 을 주지 않고 그
    시간까지 응답을 붙들고 있다가, 명령이 걸리는 순간 즉시 돌려준다.
    로봇이 3초마다 다시 묻는 대신 한 번 열어두고 기다리므로 명령이 걸리고
    로봇이 받기까지의 평균 1.5초가 사라진다.

    기본값이 0 인 것은 이전 로봇과 호환되기 위해서다. wait 를 안 보내는
    게이트웨이는 예전처럼 즉시 응답을 받는다.

    상한이 50초인 것은 중간 프록시 때문이다. nginx 기본 proxy_read_timeout
    이 60초라 그보다 짧아야 프록시가 먼저 끊지 않는다. Cloudflare 를 거치는
    경로는 100초가 상한이므로 이 값이면 양쪽 다 안전하다.

    **꺼내 보기만 하고 지우지 않는다.** 응답이 무선에서 유실되면 로봇은
    못 받았는데 서버는 보냈다고 믿게 되고, 명령이 증발한다. 지우는 것은
    로봇이 ack 를 보냈을 때만이다.

    로봇 존재 확인을 하지 않는다. 이 경로는 3초마다 호출되므로 매번 DB 를
    때리면 폴링 비용이 그대로 DB 부하가 된다. 등록되지 않은 robot_id 는
    애초에 명령이 걸릴 수 없으므로 항상 null 이 나간다.
    """
    if wait <= 0:
        return orders.peek(robot_id)
    return await orders.wait_next(robot_id, wait)


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
