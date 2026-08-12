"""로봇 생존 감시.

두절은 '아무것도 안 오는 상태'라 도착하는 데이터로는 감지할 수 없다.
마지막으로 신호를 들은 시각을 기억해두고 주기적으로 경과를 재야 한다.

    로봇 → POST /robots/{id}/heartbeat → 메모리에 시각 기록
    주기 태스크 → 경과 초과분 판정 → robot.comm_lost / comm_restored 적재

## 왜 게이트웨이 큐를 안 타는가

gateway_node 의 큐는 두절 동안 이벤트를 SQLite 에 쌓았다가 복구되면 몰아
보낸다. heartbeat 를 같은 큐에 넣으면 두절 중 heartbeat 도 쌓이고, 복구
순간 "10분 전 나 살아있었음" 이 한꺼번에 도착한다. 신호의 의미가 사라진다.
heartbeat 는 실패하면 그냥 버리는 별도 경로여야 한다.

## 왜 메모리인가

로봇 4대 × 5초 = 초당 0.8회다. Redis 를 넣어서 얻는 건 성능이 아니라
운영 부담이다. PostgreSQL 도 감당하지만, 003 이 "화면 표시용 실시간 상태는
DB 에 저장하지 않는다" 로 정해둔 것과 성격이 같아 메모리에 둔다.
DB 에는 판정 결과(events)만 남는다.

## 단일 프로세스 전제

워커나 레플리카가 2개 이상이면 깨진다. 로봇이 프로세스 A 에 heartbeat 를
보내면 B 의 딕셔너리는 비어 있고, B 의 판정 태스크가 멀쩡한 로봇에
comm_lost 를 찍는다. 에러도 안 나고 오탐만 쌓인다.
늘려야 하면 이 모듈의 저장소를 PostgreSQL 로 옮길 것. 판정 로직은
touch()/snapshot() 뒤에 가려져 있어 그대로 둘 수 있다.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from . import db, robot_runtime
from .config import (
    HEARTBEAT_CHECK_INTERVAL_SEC,
    HEARTBEAT_OFFLINE_AFTER_SEC,
    SESSION_FAILURE_CANCEL_AFTER_SEC,
)
from .ingest import ingest
from .registry import get_registry
from .schemas import EventIn

log = logging.getLogger("mingky")

SOURCE_NODE = "backend.heartbeat"

# robot_id → 마지막 수신 시각
_last_seen: dict[str, datetime] = {}

# 현재 두절로 판정된 로봇. comm_lost 를 매 주기 반복 발행하지 않기 위한 것이다.
_offline: set[str] = set()

# 복구를 감지한 시점에 만들어두는 대기열.
#
# 복구는 heartbeat 가 다시 들어오는 순간 알 수 있는데, 그 처리를 요청 핸들러
# 안에서 하면 DB 왕복이 heartbeat 경로에 끼어든다. heartbeat 는 빠르고
# 실패하지 않아야 하므로 여기 쌓아두고 판정 태스크가 비운다.
_pending_restored: list[tuple[str, datetime, float]] = []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_tracked(robot_id: str) -> bool:
    """한 번이라도 heartbeat 를 보낸 적 있는 로봇인가."""
    return robot_id in _last_seen


def touch(robot_id: str) -> None:
    """heartbeat 수신. 요청 핸들러가 호출한다. DB 를 건드리지 않는다."""
    now = _now()
    previous = _last_seen.get(robot_id)
    _last_seen[robot_id] = now

    if robot_id in _offline:
        _offline.discard(robot_id)
        # 공백은 판정 시각이 아니라 마지막 heartbeat 부터 잰다.
        # 판정은 임계값만큼 늦게 일어나므로 두 값이 다르다.
        gap = (now - previous).total_seconds() if previous else 0.0
        _pending_restored.append((robot_id, now, gap))


def snapshot() -> dict[str, tuple[datetime, bool]]:
    """robot_id → (마지막 수신 시각, 두절 여부). GET /robots 가 쓴다."""
    return {rid: (seen, rid in _offline) for rid, seen in _last_seen.items()}


def _event(robot_id: str, occurred_at: datetime, code: str,
           level: str, payload: dict, session_id: int = 0) -> EventIn:
    return EventIn(
        event_id=uuid.uuid4(),
        robot_id=robot_id,
        session_id=session_id,
        # 마지막 heartbeat 로 소급하지 않는다. occurred_at 과 received_at 의
        # 차이는 게이트웨이 큐잉 지연을 뜻하는데, 서버가 방금 만든 이벤트에
        # 그 배지가 붙으면 거짓말이 된다. 두절 시작은 payload 로 역산한다.
        occurred_at=occurred_at,
        level=level,
        event_code=code,
        source_node=SOURCE_NODE,
        payload=payload,
    )


def collect(now: datetime | None = None) -> list[EventIn]:
    """이번 주기에 발행할 이벤트를 모은다. 상태 전이도 여기서 일어난다."""
    now = now or _now()
    events: list[EventIn] = []

    # 복구 먼저. 같은 주기에 끊김과 복구가 겹치면 발생 순서대로 나가야 한다.
    while _pending_restored:
        robot_id, at, gap = _pending_restored.pop(0)
        events.append(_event(robot_id, at, "robot.comm_restored", "info",
                             {"offline_sec": round(gap, 3)}))

    for robot_id, seen in _last_seen.items():
        if robot_id in _offline:
            continue
        gap = (now - seen).total_seconds()
        if gap > HEARTBEAT_OFFLINE_AFTER_SEC:
            _offline.add(robot_id)
            events.append(_event(robot_id, now, "robot.comm_lost", "error",
                                 {"last_seen_sec": round(gap, 3)}))

    return events


def cancellation_reasons(now: datetime | None = None) -> dict[str, str]:
    """30초 이상 지속된 장애를 로봇별 세션 종료 사유로 돌려준다."""
    now = now or _now()
    reasons: dict[str, str] = {}

    # 전원·네트워크·gateway 장애는 heartbeat 공백으로 함께 잡힌다.
    for robot_id, seen in _last_seen.items():
        if (now - seen).total_seconds() >= SESSION_FAILURE_CANCEL_AFTER_SEC:
            reasons[robot_id] = "robot_offline"

    # heartbeat 가 계속 오는 경우에만 통합 시스템 장애를 별도로 판정한다.
    # offline 과 겹치면 더 근본적인 robot_offline 을 우선한다.
    for robot_id, runtime in robot_runtime.snapshot().items():
        if robot_id in reasons or robot_id not in _last_seen:
            continue
        if runtime.system_state not in ("inactive", "failed"):
            continue
        if ((now - runtime.state_since).total_seconds()
                >= SESSION_FAILURE_CANCEL_AFTER_SEC):
            reasons[robot_id] = "system_failure"

    return reasons


async def _session_cancel_events(conn, now: datetime) -> list[EventIn]:
    reasons = cancellation_reasons(now)
    if not reasons:
        return []
    rows = await conn.fetch(
        """
        SELECT session_id, robot_id
        FROM guidance_sessions
        WHERE ended_at IS NULL AND robot_id = ANY($1::varchar[])
        """,
        list(reasons),
    )
    return [
        _event(
            row["robot_id"], now, "session.ended", "error",
            {"end_reason": reasons[row["robot_id"]]}, row["session_id"])
        for row in rows
    ]


async def _tick() -> None:
    now = _now()
    events = collect(now)
    pool = db.get_pool()
    async with pool.acquire() as conn:
        events.extend(await _session_cancel_events(conn, now))
        if not events:
            return
        await ingest(conn, events, get_registry())

    for event in events:
        log.warning("%s robot=%s %s", event.event_code, event.robot_id, event.payload)


async def monitor() -> None:
    """판정 루프. main.py 의 lifespan 이 태스크로 띄운다.

    한 번 실패했다고 루프를 죽이지 않는다. DB 가 잠깐 흔들렸다고 생존 감시가
    조용히 멈추면, 그 뒤로는 로봇이 죽어도 아무도 모른다.
    """
    log.info("heartbeat 감시 시작 (판정 %.0f초 주기, 무응답 %.0f초)",
             HEARTBEAT_CHECK_INTERVAL_SEC, HEARTBEAT_OFFLINE_AFTER_SEC)
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL_SEC)
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("heartbeat 판정 실패. 다음 주기에 다시 시도한다.")


def reset() -> None:
    """테스트용. 프로세스 안의 감시 상태를 비운다."""
    _last_seen.clear()
    _offline.clear()
    _pending_restored.clear()
