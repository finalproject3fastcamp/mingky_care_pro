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
import math

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from .. import control_audit, fleet_pose
from ..actor import Actor, actor_from_query

router = APIRouter(prefix="/robots", tags=["teleop"])
log = logging.getLogger("mingky")

# robot_id → 현재 붙어 있는 로봇 소켓. 로봇 한 대에 하나만 둔다.
_robots: dict[str, WebSocket] = {}
# robot_id → 조작자 소켓들. 여러 명이 볼 수 있게 열어 두되, 조작은 아래 참고.
_operators: dict[str, set[WebSocket]] = {}


def _record_pose(robot_id: str, message: str) -> None:
    """올라온 프레임이 위치면 `fleet_pose` 에 남긴다.

    여기서 가로채는 이유는, 이 소켓이 **관제가 로봇 위치를 듣는 유일한
    지점**이기 때문이다. 조작자가 붙어 있든 아니든 로봇은 계속 올리므로,
    아무도 안 보고 있어도 서버는 위치를 안다. 관제 화면이 여러 대를 한
    번에 그릴 수 있는 근거가 이것이다 (`app/fleet_pose.py`).

    파싱 비용은 로봇당 초당 7 프레임 남짓이다 (pose 2Hz + 진단 4종 1Hz +
    모드 1Hz). 진단 레이어는 브리지가 이미 120점으로 솎아 보낸다.

    **깨진 프레임은 조용히 넘긴다.** 여기서 예외가 나면 중계가 끊기고,
    중계가 끊기는 것은 곧 그 로봇에 조작이 안 닿는다는 뜻이다. 위치 한
    프레임을 놓치는 것보다 나쁘다.
    """
    try:
        payload = json.loads(message)
        if payload.get("type") != "pose":
            return
        x, y, yaw = (
            float(payload["x"]), float(payload["y"]), float(payload["yaw"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        return

    # NaN 은 JSON 에 없는 값이라 `json.loads` 는 받아주지만 브라우저의
    # `JSON.parse` 는 거부한다. 그대로 흘리면 그 프레임이 화면에서 통째로
    # 버려지고, 원인이 지도에서는 안 보인다.
    if not all(map(math.isfinite, (x, y, yaw))):
        return

    fleet_pose.update(robot_id, x, y, yaw)


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
            # 로봇은 pose · 진단 레이어 · 모드 상태를 올린다. 조작자에게는
            # 전부 그대로 흘려보내고, 위치만 따로 한 벌 더 챙긴다.
            message = await websocket.receive_text()
            _record_pose(robot_id, message)
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
async def operator_socket(
    websocket: WebSocket,
    robot_id: str,
    actor: Actor = Depends(actor_from_query),
):
    """대시보드가 붙는 쪽. 조작을 보내고 pose 를 받는다.

    여기만 헤더가 아니라 `?actor=` 쿼리인 것은 브라우저 `new WebSocket(url)`
    에 커스텀 헤더를 실을 방법이 없어서다. 정규화는 HTTP 경로와 같은 함수를
    지난다.
    """
    await websocket.accept()
    _operators.setdefault(robot_id, set()).add(websocket)
    log.info("teleop: 조작자 연결 %s (총 %d)", robot_id, len(_operators[robot_id]))

    # 점유를 **중계 시작 전에** 남긴다. 여기서 효과는 순간 명령이 아니라 살아
    # 있는 소켓이라, 기록보다 먼저 첫 조작을 흘려보내면 기록 없는 조작 구간이
    # 생긴다. 짧아도 그 구간이 정확히 §1.1 이 놓치는 개입이다.
    await control_audit.record(robot_id, control_audit.TELEOP_ATTACH, actor)

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
        # finally 에 두는 것이 요점이다. 정상 종료 경로에만 적으면 회선이
        # 끊긴 세션은 영원히 점유 중으로 남아, 감사 로그 자체가 거짓말이 된다.
        # 이 시점에 세션이 이미 끝났으면 session_id 는 NULL 로 들어간다 —
        # 판정은 attach 로 하므로 문제가 없다.
        await control_audit.record(
            robot_id, control_audit.TELEOP_DETACH, actor)
        log.info("teleop: 조작자 연결 끊김 %s", robot_id)
