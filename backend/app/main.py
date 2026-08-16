import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db, heartbeat, inventory_rules, registry, topic_watch
from .routers import (
    events, fleet, maps, orders, patients, qr, robots, sessions, slo, teleop,
    waypoints)

log = logging.getLogger("mingky")

# 단일 인스턴스 가드용 advisory lock 키. 이 DB 를 쓰는 다른 무엇과도 겹치면
# 안 되므로 값 자체에 의미는 없고 고유하기만 하면 된다.
_LOCK_KEY = 424242

# 락 보유를 다시 확인하는 주기.
_LOCK_RECHECK_SEC = 10

# 이 세션이 그 키의 락을 실제로 쥐고 있는지. 단일 bigint 로 잡은 advisory lock 은
# 상위 32비트가 classid, 하위 32비트가 objid 로 쪼개져 pg_locks 에 들어간다.
_HOLDS_LOCK = """
    SELECT EXISTS (
        SELECT 1 FROM pg_locks
        WHERE locktype = 'advisory'
          AND pid = pg_backend_pid()
          AND ((classid::bigint << 32) | objid::bigint) = $1
          AND granted
    )
"""

_lock_conn = None


async def _claim_single_instance() -> None:
    """이 프로세스가 이 DB 를 쓰는 유일한 인스턴스임을 확인한다.

    heartbeat · arming · orders 가 인메모리다. 둘 이상이 뜨면 판정이 인스턴스마다
    갈려서 두절이 간헐적으로 찍히고 arming 이 보였다 안 보였다 한다. 증상이 로봇
    문제처럼 보이므로 원인 추적에 며칠이 간다.

    워커 수를 argv 나 환경 변수로 세지 않는다. `--workers N` `--workers=N`
    `WEB_CONCURRENCY` 로 형태가 갈리고 그때마다 구멍이 하나씩 생긴다. 파일 락도
    쓰지 않는다 — 컨테이너마다 /tmp 가 따로라 레플리카를 못 잡는다. DB 는 모든
    인스턴스가 반드시 공유하는 유일한 지점이다.
    """
    global _lock_conn

    _lock_conn = await db.get_pool().acquire()
    if not await _lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", _LOCK_KEY):
        log.critical(
            "다중 워커/레플리카 감지 — 이 DB 를 쓰는 인스턴스가 이미 있습니다. "
            "heartbeat·arming·orders 가 인메모리라 하나만 떠야 합니다.")
        sys.exit(3)


async def _still_holding_lock() -> bool:
    """세션이 살아 있고 락도 그대로 우리 것인지 본다.

    확인 전체를 try 로 감싸는 것이 핵심이다. pool.acquire() 가 돌려주는 것은
    커넥션이 아니라 프록시고, 밑의 커넥션이 죽으면 asyncpg 가 프록시를 풀에
    되돌려버린다. 그 뒤로는 `is_closed()` 같은 조회조차
    `InterfaceError: connection has been released back to the pool` 를 던진다.
    이 한 줄을 try 밖에 두는 바람에 워치독 태스크가 조용히 죽어서, 락이 풀린
    뒤에도 아무것도 감지하지 못하는 상태를 실측으로 확인했다.
    """
    if _lock_conn is None:
        return False
    try:
        if _lock_conn.is_closed():
            return False
        return bool(await _lock_conn.fetchval(_HOLDS_LOCK, _LOCK_KEY))
    except Exception as exc:
        log.warning("락 확인 실패 — 잃은 것으로 본다: %s", exc)
        return False


async def _hold_single_instance() -> None:
    """락을 계속 쥐고 있는지 주기적으로 확인한다.

    advisory lock 은 프로세스가 아니라 **DB 세션**에 붙어 있다. DB 재시작,
    네트워크 블립, `idle_session_timeout`, failover, 앞단 pgbouncer 로 세션이
    끊기면 락은 서버에서 사라지는데 프로세스는 그대로 살아 요청을 받는다. 그
    틈에 두 번째 인스턴스가 락을 잡고 정상 기동하면, 가드가 막으려던 상태
    — 인메모리가 갈린 인스턴스 둘 — 가 그대로 된다.

    가정이 아니라 실측이다. `pg_terminate_backend` 로 락 보유 세션을 끊으면
    기존 인스턴스는 /health 200 을 계속 돌려주고 새 인스턴스가 그 위에 뜬다.

    기동 시 한 번 잡는 것으로는 부족하고, 들고 있는지 계속 봐야 한다.
    """
    global _lock_conn

    while True:
        await asyncio.sleep(_LOCK_RECHECK_SEC)

        if await _still_holding_lock():
            continue

        # 락을 잃었다. 다시 잡아본다 — 그 사이 아무도 안 가져갔으면 우리가
        # 계속 유일한 인스턴스다.
        log.warning("단일 인스턴스 락을 잃었습니다. 다시 잡습니다.")
        try:
            _lock_conn = await db.get_pool().acquire()
            regained = await _lock_conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", _LOCK_KEY)
        except Exception as exc:
            # DB 가 아직 안 돌아왔다. 다음 주기에 다시 본다 — 여기서 죽으면
            # DB 재시작 때마다 백엔드가 같이 죽는다.
            log.warning("락 재확보 실패, 다음 주기에 재시도: %s", exc)
            continue

        if regained:
            log.info("단일 인스턴스 락을 다시 확보했습니다.")
            continue

        # 다른 인스턴스가 그 틈에 들어왔다. 둘 다 살아 있으면 안 되고,
        # 늦게 안 쪽이 물러난다.
        log.critical(
            "락을 잃은 사이 다른 인스턴스가 들어왔습니다. 인메모리 상태 충돌을 "
            "막기 위해 종료합니다.")
        # 백그라운드 태스크에서 sys.exit 는 프로세스를 못 죽인다. 자신에게
        # SIGTERM 을 보내 uvicorn 의 정상 종료 경로를 태운다.
        os.kill(os.getpid(), signal.SIGTERM)
        return


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 이벤트 코드 정본이 없거나 깨졌으면 여기서 죽는다.
    # 검증 없이 뜨면 미등록 코드가 조용히 쌓이므로 그쪽이 더 나쁘다.
    codes = registry.load()
    log.info("event_codes 로드: %s (코드 %d개)", codes.source, len(codes))

    # 중복 노드 심각도. 없어도 기본 등급으로 동작하므로 기동을 막지 않는다.
    inventory_rules.load()

    # 토픽 임계도 같다. 없으면 나이만 보이고 판정이 빠질 뿐이다.
    topic_watch.load()

    await db.connect()
    await _claim_single_instance()

    # 생존 감시. 재시작하면 메모리가 비므로 감시 대상도 0 에서 시작한다.
    # 첫 heartbeat 를 받은 로봇부터 감시에 들어가므로 기동 직후 전원이
    # 두절로 찍히는 일은 없고, 별도 유예 로직도 필요 없다.
    monitor = asyncio.create_task(heartbeat.monitor())
    # 살아 있는 로봇 안에서 데이터가 흐르는지는 다른 질문이다 (§7.2).
    topics = asyncio.create_task(topic_watch.monitor())
    lock_watch = asyncio.create_task(_hold_single_instance())
    try:
        yield
    finally:
        for task in (monitor, topics, lock_watch):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await _release_single_instance()
        await db.disconnect()


async def _release_single_instance() -> None:
    global _lock_conn

    if _lock_conn is None:
        return
    # 여기도 프록시가 이미 풀에 돌아가 있을 수 있다 (_still_holding_lock 참고).
    # 종료 경로에서 예외를 내면 db.disconnect() 까지 못 간다.
    try:
        if not _lock_conn.is_closed():
            await _lock_conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)
            await db.get_pool().release(_lock_conn)
    except Exception as exc:
        log.warning("락 해제 생략 (커넥션이 이미 정리됨): %s", exc)
    _lock_conn = None


app = FastAPI(title="Mingky Care Backend", lifespan=lifespan)
app.include_router(qr.router)
app.include_router(events.router)
app.include_router(sessions.router)
app.include_router(robots.router)
app.include_router(patients.router)
# robots.router 보다 뒤에 둔다. 경로 접두사가 같아 등록 순서가 매칭에 영향을 준다.
app.include_router(orders.router)
app.include_router(slo.router)
app.include_router(fleet.router)
app.include_router(maps.router)
app.include_router(waypoints.router)
app.include_router(teleop.router)


@app.get("/health")
async def health():
    return {"status": "ok", "event_codes": len(registry.get_registry())}
