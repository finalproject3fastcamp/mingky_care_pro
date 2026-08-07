"""의료진이 활성화한 로봇을 담는 인메모리 레지스트리.

의료진이 대시보드에서 "이 핑키를 지금 쓰겠다" 를 표시하면 여기 기록되고,
로봇 QR 노드는 매 폴링마다 여기를 조회해 자기가 armed 인지 본다. armed
가 아니면 QR 을 디코드하지 않는다.

## 왜 DB 가 아닌가

registry.py 가 이미 쓰는 인메모리 공유 패턴 그대로다. arming 은 몇 초~
몇 분짜리 휘발성 상태고 (docs/monitoring-spec.md 원칙),
routers/robots.py 상단에도 "3~5초마다 덮어쓰는 값을 영속화할 이유가 없다"
는 같은 취지가 박혀 있다. 감사·타임라인은 events 로그 (activation.*) 로
남기니 영속 기록도 놓치지 않는다.

## 워커 1개 전제

파이썬 dict 는 프로세스 메모리 안에만 있다. uvicorn 워커가 여러 개면 arm
요청이 A 워커, 로봇 폴링이 B 워커로 갈리면서 상태가 안 보이는 순간이 생긴다.
워커를 늘려야 할 때가 오면 Redis 나 DB 로 옮긴다. 지금 배포는 워커 1개다.
"""

from __future__ import annotations

from datetime import datetime, timezone

# robot_id → armed_at (UTC aware). 항목이 없으면 disarmed.
_armed: dict[str, datetime] = {}


def arm(robot_id: str) -> datetime:
    """arming 을 켜고 armed_at 을 돌려준다.

    idempotent: 이미 armed 인 로봇을 다시 arm 하면 기존 시각을 유지한다.
    재요청을 새 시각으로 덮어쓰면 이벤트 로그가 시끄러워지고, 재요청은 대개
    다른 탭에서 온 중복이라 무해하다.
    """
    if robot_id not in _armed:
        _armed[robot_id] = datetime.now(timezone.utc)
    return _armed[robot_id]


def disarm(robot_id: str) -> bool:
    """arming 을 끄고 실제로 지웠는지를 돌려준다.

    False = 원래 disarmed. 이벤트 이중 발행을 막기 위해 호출측이 쓴다.
    """
    return _armed.pop(robot_id, None) is not None


def is_armed(robot_id: str) -> bool:
    """이 로봇이 활성화 상태인가."""
    return robot_id in _armed


def armed_at(robot_id: str) -> datetime | None:
    """활성화된 시각. 활성화 상태가 아니면 None."""
    return _armed.get(robot_id)


def snapshot() -> dict[str, datetime]:
    """현재 armed 인 모든 로봇. GET /robots 가 응답을 조립할 때 쓴다."""
    return dict(_armed)
