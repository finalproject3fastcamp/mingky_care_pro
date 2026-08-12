"""로봇에 내리는 명령(order) 보관.

    대시보드 → POST /robots/{id}/orders        → 여기에 적재
    로봇     → GET  /robots/{id}/orders/next   → 꺼내 본다 (지우지 않는다)
    로봇     → POST .../orders/{order_id}/ack  → 여기서 지운다

## 왜 GET 이 지우지 않는가

무선 구간에서 응답이 유실되면 로봇은 명령을 못 받았는데 서버는 보냈다고
믿는다. 그러면 명령이 증발한다. 꺼내 보기(peek)와 지우기(ack)를 나누면
로봇이 실제로 받아서 실행에 넣은 것만 사라진다.

같은 명령을 두 번 받는 것은 허용한다. order_id 가 붙어 있으므로 로봇이
이미 처리한 것인지 판단할 수 있다. 잃는 것보다 중복이 낫다.

## 왜 로봇이 물어보러 오는가

서버가 로봇에 밀어넣으려면 로봇이 접속 가능한 주소를 가져야 한다. 로봇이
NAT 안에 있거나 네트워크를 옮기면 그 전제가 깨진다. 로봇이 물어보는 쪽이면
로봇은 나가는 연결만 만들면 되고, 어느 네트워크에 있든 동작한다.

## 단일 프로세스 전제

heartbeat.py 와 같다. 워커나 레플리카가 2개 이상이면 명령이 프로세스마다
갈라져, 로봇이 물어본 프로세스에 명령이 없을 수 있다.
늘려야 하면 이 모듈의 저장소를 PostgreSQL 로 옮길 것.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from .schemas import OrderOut

log = logging.getLogger("mingky")

# 안전 명령으로 다루는 command. 주행 명령과 슬롯을 나눠 담는다.
#
# 한 슬롯에 같이 두면 비상정지 요청이 아직 안 받아간 사이에 goto 가 들어와
# **덮어써진다.** 조작자는 정지를 눌렀는데 로봇은 그 명령을 본 적이 없다.
# 로그에만 남고 화면에는 성공으로 보이므로 알아채기도 어렵다.
_SAFETY_COMMANDS = frozenset({"set_mode"})
_SYSTEM_COMMANDS = frozenset({"system_start", "system_stop", "system_restart"})

# robot_id → 대기 중인 주행 명령. 로봇당 하나만 둔다.
#
# 큐로 쌓지 않는 이유: 안내 로봇에게 "지금 어디로 가라" 는 최신 것 하나만
# 유효하다. 밀린 목적지를 순서대로 소화하는 건 오히려 위험하다.
# 새 명령이 오면 아직 안 받아간 이전 명령을 덮어쓴다.
_pending: dict[str, OrderOut] = {}

# robot_id → 대기 중인 안전 명령. 주행 명령이 덮어쓰지 못한다.
#
# 안전 명령끼리는 최신 것이 이긴다. estop 뒤에 오는 해제 요청은 조작자의
# 명시적 판단이므로 덮어쓰는 것이 맞다.
_safety: dict[str, OrderOut] = {}

# 통합 launch 제어는 주행 명령과 별도 슬롯이다. 시스템 시작 요청이 아직
# 전달되지 않았다고 해서 뒤이어 누른 Waypoint 명령이 이를 지우면 안 된다.
_system: dict[str, OrderOut] = {}

# robot_id → 그 로봇의 명령을 기다리고 있는 대기자들.
#
# 롱폴링용이다. 로봇이 3초마다 물어보면 명령이 걸린 직후에도 평균 1.5초를
# 기다린다. 실측으로 왕복이 200ms 인데 대기가 1500ms 라, 늦는 원인의 대부분이
# 폴링 주기였다. 명령이 걸리는 순간 깨우면 그 1500ms 가 사라진다.
#
# 로봇당 대기자가 여럿일 수 있다(재접속 직후 이전 요청이 아직 안 끝난 경우).
# 그래서 Event 하나를 공유하지 않고 대기자마다 따로 만든다 — 공유하면 늦게
# 온 쪽이 clear() 해서 먼저 기다리던 쪽이 신호를 놓친다.
_waiters: dict[str, set[asyncio.Event]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slot(command: str) -> dict[str, OrderOut]:
    if command in _SAFETY_COMMANDS:
        return _safety
    if command in _SYSTEM_COMMANDS:
        return _system
    return _pending


def put(robot_id: str, command: str, argument: str) -> OrderOut:
    """명령을 걸어둔다. 같은 종류의 기존 대기 명령만 덮어쓴다."""
    slot = _slot(command)
    previous = slot.get(robot_id)
    if previous is not None:
        log.warning("이전 명령을 덮어씀: robot=%s %s(%s) order_id=%s",
                    robot_id, previous.command, previous.argument,
                    previous.order_id)

    order = OrderOut(
        order_id=uuid.uuid4(),
        robot_id=robot_id,
        command=command,
        argument=argument,
        created_at=_now(),
    )
    slot[robot_id] = order
    _wake(robot_id)
    return order


def _wake(robot_id: str) -> None:
    """이 로봇을 기다리던 롱폴링 요청을 전부 깨운다."""
    for event in _waiters.pop(robot_id, ()):
        event.set()


async def wait_next(robot_id: str, timeout: float) -> OrderOut | None:
    """명령이 생길 때까지 기다렸다 돌려준다. timeout 안에 없으면 None.

    peek 과 달리 **응답을 붙들고 있는다.** 로봇은 이 요청 하나를 열어둔 채
    기다리다 명령이 걸리면 즉시 받는다. 왕복이 아니라 편도라 폴링보다
    빠르다.

    peek 과 등록 사이에 await 가 없다. 같은 이벤트 루프에서 도는 put 이
    그 틈에 끼어들 수 없으므로, 명령이 걸렸는데 아무도 못 깨우는 경우는
    생기지 않는다.
    """
    order = peek(robot_id)
    if order is not None:
        return order

    event = asyncio.Event()
    _waiters.setdefault(robot_id, set()).add(event)
    try:
        await asyncio.wait_for(event.wait(), timeout)
    except TimeoutError:
        return None
    finally:
        waiting = _waiters.get(robot_id)
        if waiting is not None:
            waiting.discard(event)
            if not waiting:
                _waiters.pop(robot_id, None)
    return peek(robot_id)


def peek(robot_id: str) -> OrderOut | None:
    """대기 중인 명령을 돌려준다. 지우지 않는다.

    **안전 명령을 먼저 준다.** 정지 요청과 주행 명령이 함께 대기 중이면
    정지가 먼저 가야 한다. 반대 순서면 로봇이 목적지로 출발한 뒤에야
    정지를 받는다.
    """
    return (_safety.get(robot_id) or _system.get(robot_id)
            or _pending.get(robot_id))


def ack(robot_id: str, order_id: uuid.UUID) -> bool:
    """로봇이 받았다고 알려온 명령을 지운다.

    지운 경우 True. order_id 가 안 맞으면 지우지 않고 False —
    그 사이 새 명령으로 덮어써진 경우이므로 새 것을 살려둬야 한다.
    """
    for slot in (_safety, _system, _pending):
        order = slot.get(robot_id)
        if order is not None and order.order_id == order_id:
            del slot[robot_id]
            return True
    return False


def clear_motion(robot_id: str) -> None:
    """시스템 중지·재시작 전에 아직 전달되지 않은 주행 명령을 폐기한다."""
    _pending.pop(robot_id, None)


def snapshot() -> dict[str, list[OrderOut]]:
    """대기 중인 전체 명령. 디버깅과 조회용.

    로봇당 안전·주행 슬롯이 따로 있으므로 목록으로 돌려준다.
    peek 과 같은 순서(안전 먼저)로 담는다.
    """
    result: dict[str, list[OrderOut]] = {}
    for robot_id in set(_safety) | set(_system) | set(_pending):
        orders = [
            s[robot_id] for s in (_safety, _system, _pending) if robot_id in s]
        result[robot_id] = orders
    return result


def reset() -> None:
    """테스트용."""
    _pending.clear()
    _safety.clear()
    _system.clear()
