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

import logging
import uuid
from datetime import datetime, timezone

from .schemas import OrderOut

log = logging.getLogger("mingky")

# robot_id → 대기 중인 명령. 로봇당 하나만 둔다.
#
# 큐로 쌓지 않는 이유: 안내 로봇에게 "지금 어디로 가라" 는 최신 것 하나만
# 유효하다. 밀린 목적지를 순서대로 소화하는 건 오히려 위험하다.
# 새 명령이 오면 아직 안 받아간 이전 명령을 덮어쓴다.
_pending: dict[str, OrderOut] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def put(robot_id: str, command: str, argument: str) -> OrderOut:
    """명령을 걸어둔다. 기존 대기 명령이 있으면 덮어쓴다."""
    previous = _pending.get(robot_id)
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
    _pending[robot_id] = order
    return order


def peek(robot_id: str) -> OrderOut | None:
    """대기 중인 명령을 돌려준다. 지우지 않는다."""
    return _pending.get(robot_id)


def ack(robot_id: str, order_id: uuid.UUID) -> bool:
    """로봇이 받았다고 알려온 명령을 지운다.

    지운 경우 True. order_id 가 안 맞으면 지우지 않고 False —
    그 사이 새 명령으로 덮어써진 경우이므로 새 것을 살려둬야 한다.
    """
    order = _pending.get(robot_id)
    if order is None or order.order_id != order_id:
        return False
    del _pending[robot_id]
    return True


def snapshot() -> dict[str, OrderOut]:
    """대기 중인 전체 명령. 디버깅과 조회용."""
    return dict(_pending)


def reset() -> None:
    """테스트용."""
    _pending.clear()
