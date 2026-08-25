"""전체 위치 조회 — 관제가 핑키 2대를 한 지도에서 본다.

판정도 상태도 `app/fleet_pose.py` 에 있고 여기에는 HTTP·WS 배관만 있다.

## 왜 `routers/fleet.py` 가 아닌가

저쪽은 "4대가 무엇으로 돌고 있는가"(형상) 전용이고 몇 시간에 한 번 바뀌는
값을 다룬다. 여기는 0.5초짜리다. 같은 `/fleet` 접두사를 쓰되 파일을 나눠
두 수명주기가 한 모듈에서 섞이지 않게 한다.

## 읽기 전용이다

**이 경로로는 로봇에 아무것도 못 보낸다.** 조작은 `routers/teleop.py` 의
조작자 소켓만 할 수 있고, 그쪽은 붙는 순간 `control_audit` 에 점유를
남긴다. 관측이 그 기록을 남기면 화면을 열어둔 것만으로 세션이 SLO 개입으로
판정된다 (`app/fleet_pose.py` 모듈 주석).

그래서 여기에는 `actor` 의존성도, `control_audit.record` 호출도 없다.
없는 것이 이 모듈의 계약이므로 **테스트가 그 부재를 지킨다**
(`tests/test_fleet_poses.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import fleet_link, fleet_pose
from ..schemas import FleetCoordinationSetOut, FleetPosesOut

router = APIRouter(prefix="/fleet", tags=["fleet"])
log = logging.getLogger("mingky")


@router.get("/poses", response_model=FleetPosesOut)
async def get_fleet_poses() -> FleetPosesOut:
    """지금 서버가 아는 모든 로봇의 마지막 위치.

    폴링용이 아니다. 0.5초 값을 HTTP 로 따라가면 절반을 놓치므로 화면은
    아래 스트림을 쓴다. 이 경로는 첫 로딩·진단·테스트용이다.
    """
    return FleetPosesOut(**fleet_pose.snapshot_message())


@router.get("/coordination", response_model=FleetCoordinationSetOut)
async def get_fleet_coordination() -> FleetCoordinationSetOut:
    """지금 누가 왜 멈춰 있고, 누구의 방문 순서가 바뀌었는가.

    화면이 "그냥 멈춰 있다" 대신 "핑키 2호가 지날 때까지 기다립니다" 를 말할
    수 있게 하는 값이다. 로봇이 멈춘 이유가 안 보이면 의료진은 고장으로
    읽고 사람을 부른다 — 그 호출 하나하나가 §1.1 의 개입이다.

    폴링용이다. 위치와 달리 초당 몇 번씩 바뀌는 값이 아니라, 화면의 다른
    상태(`/robots`)와 같은 주기로 물으면 된다.
    """
    return FleetCoordinationSetOut(robots=await fleet_link.coordination())


@router.websocket("/poses/stream")
async def fleet_pose_stream(websocket: WebSocket):
    """위치 브로드캐스트. 붙으면 스냅샷 한 번, 이후 갱신분만.

    ## 왜 수신도 기다리는가

    보내기만 하는 소켓은 **조용히 죽는다.** 로봇이 하나도 안 붙어 있으면
    큐가 영영 안 차고, 그동안 관전자가 창을 닫아도 서버는 모른다. 큐와
    구독이 그대로 남아 다음 로봇이 붙는 순간까지 새는 것이다.

    관전자가 보내는 메시지는 **하나도 쓰지 않는다.** 이 대기의 목적은 끊김을
    알아채는 것뿐이고, 그래서 받은 내용은 읽지도 않고 버린다 — 이 경로로는
    로봇에 아무것도 못 간다는 것이 모듈 주석의 계약이다.
    """
    await websocket.accept()
    queue = fleet_pose.subscribe()
    log.info("fleet pose: 관전자 연결 (총 %d)", fleet_pose.watcher_count())

    async def drain() -> None:
        while True:
            await websocket.receive()

    async def pump() -> None:
        # 스냅샷을 **구독한 뒤에** 보낸다. 순서를 뒤집으면 그 사이에 들어온
        # 좌표가 스냅샷에도 큐에도 없어 그대로 사라진다.
        await websocket.send_json(fleet_pose.snapshot_message())
        while True:
            await websocket.send_json(await queue.get())

    tasks = {asyncio.create_task(drain()), asyncio.create_task(pump())}
    try:
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        # 취소한 태스크를 거둬야 한다. 안 그러면 이벤트 루프가 "Task was
        # destroyed but it is pending" 을 남기고, 그 경고가 진짜 누수와
        # 구분되지 않는다.
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                task.result()
    finally:
        fleet_pose.unsubscribe(queue)
        log.info("fleet pose: 관전자 연결 끊김 (총 %d)", fleet_pose.watcher_count())
