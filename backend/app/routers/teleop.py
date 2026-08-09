"""대시보드와 로봇을 잇는 실시간 조작 중계.

    [대시보드] --ws--> /robots/{id}/teleop/operator ─┐
                                                     ├─ 중계 ─→ 로봇
    [로봇]     --ws--> /robots/{id}/teleop/robot   ─┘
                                                     └─ pose ─→ 대시보드

## 왜 orders 로 안 하나

`orders` 는 로봇이 3초마다 물어보러 오는 구조다. 웨이포인트 명령에는 맞지만
사람이 방향키를 누르는 조작에는 못 쓴다 — 누른 뒤 최대 3초 뒤에 움직이고,
손을 뗀 것과 명령이 없는 것을 구분할 수 없다.

## 왜 양쪽 다 서버로 접속하나

로봇은 NAT 뒤에 있어 서버가 로봇에 접속할 수 없다. `orders` 주석에 적힌 것과
같은 이유로, 여기서도 **양쪽이 서버로 나오는 연결**만 만든다.

## 왜 메모리에 두나

`arming.py`, `heartbeat.py` 와 같은 인메모리 공유 패턴이다. 연결은 살아있는
동안만 의미가 있어 영속화할 이유가 없다.

**워커 1개 전제다.** 워커가 여러 개면 대시보드가 A 워커, 로봇이 B 워커에
붙어 서로 못 만난다. 늘려야 할 때가 오면 Redis pub/sub 으로 옮긴다.
지금 배포는 워커 1개다 (backend/README.md).

## 안전

이 경로가 끊기면 로봇에는 아무것도 안 간다. 그러면 로봇의 twist_mux timeout
과 pinky_bringup 워치독이 1초 안에 모터를 세운다. **중계가 죽는 것이 곧
정지**이므로 여기서 별도의 정지 신호를 보내지 않는다 — 보내려 해도 끊긴
연결로는 못 보낸다.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/robots", tags=["teleop"])
log = logging.getLogger("mingky")

# robot_id → 현재 붙어 있는 로봇 소켓. 로봇 한 대에 하나만 둔다.
_robots: dict[str, WebSocket] = {}
# robot_id → 조작자 소켓들. 여러 명이 볼 수 있게 열어 두되, 조작은 아래 참고.
_operators: dict[str, set[WebSocket]] = {}


@router.websocket("/{robot_id}/teleop/robot")
async def robot_socket(websocket: WebSocket, robot_id: str):
    """로봇이 붙는 쪽. 명령을 받고 pose 를 올린다."""
    await websocket.accept()

    # 같은 로봇이 다시 붙으면 이전 연결은 죽은 것으로 본다. 재부팅이나 회선
    # 복구 뒤 옛 소켓이 남아 있으면 명령이 그리로 새기 때문이다.
    old = _robots.get(robot_id)
    if old is not None:
        try:
            await old.close()
        except RuntimeError:
            pass

    _robots[robot_id] = websocket
    log.info("teleop: 로봇 연결 %s", robot_id)

    try:
        while True:
            # 로봇이 올리는 것은 지금은 pose 뿐이다. 그대로 조작자들에게 뿌린다.
            message = await websocket.receive_text()
            for operator in list(_operators.get(robot_id, ())):
                try:
                    await operator.send_text(message)
                except (WebSocketDisconnect, RuntimeError):
                    _operators.get(robot_id, set()).discard(operator)
    except WebSocketDisconnect:
        pass
    finally:
        if _robots.get(robot_id) is websocket:
            del _robots[robot_id]
        log.info("teleop: 로봇 연결 끊김 %s", robot_id)


@router.websocket("/{robot_id}/teleop/operator")
async def operator_socket(websocket: WebSocket, robot_id: str):
    """대시보드가 붙는 쪽. 조작을 보내고 pose 를 받는다."""
    await websocket.accept()
    _operators.setdefault(robot_id, set()).add(websocket)
    log.info("teleop: 조작자 연결 %s (총 %d)", robot_id, len(_operators[robot_id]))

    # 로봇이 안 붙어 있으면 눌러도 아무 일이 안 일어난다. 화면이 그 사실을
    # 알아야 "고장" 과 "연결 없음" 을 구분해 보여줄 수 있다.
    await websocket.send_text(json.dumps({
        "type": "status",
        "robot_connected": robot_id in _robots,
    }))

    try:
        while True:
            message = await websocket.receive_text()
            robot = _robots.get(robot_id)
            if robot is None:
                await websocket.send_text(json.dumps({
                    "type": "status", "robot_connected": False}))
                continue
            try:
                await robot.send_text(message)
            except (WebSocketDisconnect, RuntimeError):
                _robots.pop(robot_id, None)
    except WebSocketDisconnect:
        pass
    finally:
        _operators.get(robot_id, set()).discard(websocket)
        log.info("teleop: 조작자 연결 끊김 %s", robot_id)
