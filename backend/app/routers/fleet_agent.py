"""로봇의 군집 링크 — 목표를 받고 판정을 돌려주는 소켓.

배관만 있다. 상태는 `app/fleet_link.py`, 판정은 `app/fleet_reserve.py` 에 있다.

## 이 경로로 로봇을 몰 수는 없다

돌려보내는 것은 `proceed` 뿐이고 속도도 좌표도 없다. 서 있으라는 말조차
"이 구간을 지금은 못 쓴다" 의 결과이지 모터 명령이 아니다. 주행은 끝까지
Nav2 가 한다.

그래서 `control_audit` 에 남기지 않는다 — 사람의 개입이 아니라 기계의 조정
이고, 남기면 감사 로그가 오염되고 SLO 판정까지 흔든다(§1.1).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import fleet_link

router = APIRouter(prefix="/robots", tags=["fleet"])
log = logging.getLogger("mingky")


@router.websocket("/{robot_id}/fleet")
async def fleet_socket(websocket: WebSocket, robot_id: str):
    """로봇이 붙는 쪽. 목표를 올리고 판정을 받는다."""
    await websocket.accept()

    # 같은 로봇이 다시 붙으면 이전 연결은 죽은 것으로 본다. 옛 소켓이 남아
    # 있으면 판정이 그리로 새고, 로봇은 데드맨으로 계속 풀린다
    # (routers/teleop.py 의 로봇 소켓과 같은 규칙).
    old = fleet_link.attach(robot_id, websocket)
    if old is not None:
        try:
            await old.close()
        except Exception:
            pass
    log.info("fleet: 로봇 링크 %s (총 %d)", robot_id, len(fleet_link.linked()))

    # 붙자마자 한 번 보낸다. 다음 tick 을 기다리면 그 사이 로봇은 판정을
    # 못 받은 상태라, 방금 붙었는데 데드맨이 도는 것처럼 보인다.
    await fleet_link.push()

    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue
            if message.get("type") != "intent":
                continue
            fleet_link.report(
                robot_id,
                goal_waypoint=message.get("goal_waypoint"),
                guiding=bool(message.get("guiding")),
            )
            # 목표가 바뀌면 곧바로 다시 판정한다. tick 을 기다리면 로봇이
            # 최대 1초 동안 옛 판정으로 달린다.
            await fleet_link.push()
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    except asyncio.CancelledError:
        raise
    finally:
        fleet_link.detach(robot_id, websocket)
        log.info("fleet: 로봇 링크 끊김 %s (총 %d)",
                 robot_id, len(fleet_link.linked()))
