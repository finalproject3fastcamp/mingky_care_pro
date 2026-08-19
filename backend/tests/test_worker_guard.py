"""단일 인스턴스 가드.

heartbeat · arming · orders 가 인메모리다. 둘 이상이 뜨면 판정이 인스턴스마다
갈려서 두절이 간헐적으로 찍히고 arming 이 보였다 안 보였다 한다.

워커 수를 argv 로 세는 방식은 `--workers N` `--workers=N` `WEB_CONCURRENCY` 로
형태가 갈려 구멍이 생기고, 파일 락은 컨테이너마다 /tmp 가 따로라 레플리카를
못 잡는다. DB advisory lock 은 모든 인스턴스가 반드시 공유하는 지점을 쓴다.

대신 락이 프로세스가 아니라 DB 세션에 붙는다는 성질이 따라온다. 세션이 끊기면
락만 조용히 사라지므로 기동 시 한 번 잡는 것으로는 부족하다 — 아래 마지막 두
테스트가 그 경로다.

pytest-asyncio 를 쓰지 않는다. requirements.txt 에 없고, 없으면 async 테스트가
스킵이 아니라 실패로 잡힌다. 이 저장소의 다른 테스트와 같이 asyncio.run() 으로
감싼다.
"""

import asyncio

import pytest

from app import main


class FakeConnection:
    def __init__(self, lock_granted=True):
        self.lock_granted = lock_granted
        self.closed = False
        self.unlocked = False
        self.session_alive = True
        # asyncpg 의 pool.acquire() 는 프록시를 준다. 밑의 커넥션이 죽으면
        # 프록시가 풀로 되돌아가고, 그 뒤로는 is_closed() 조차 InterfaceError
        # 를 던진다. 이걸 재현하지 않으면 워치독이 조용히 죽는 버그를 놓친다.
        self.detached = False

    def is_closed(self):
        if self.detached:
            raise RuntimeError("connection has been released back to the pool")
        return self.closed

    async def fetchval(self, query, *args):
        assert args == (main._LOCK_KEY,)
        if not self.session_alive:
            raise ConnectionError("session terminated")
        if "pg_try_advisory_lock" in query:
            return self.lock_granted
        assert "pg_locks" in query
        return self.lock_granted

    async def execute(self, query, *args):
        assert "pg_advisory_unlock" in query
        self.unlocked = True


class FakePool:
    """acquire() 마다 새 커넥션을 준다. 재확보 경로를 재현하기 위함이다."""

    def __init__(self, lock_granted=True):
        self.lock_granted = lock_granted
        self.handed_out = []
        self.released = []

    async def acquire(self):
        conn = FakeConnection(self.lock_granted)
        self.handed_out.append(conn)
        return conn

    async def release(self, conn):
        self.released.append(conn)


@pytest.fixture
def pool(monkeypatch):
    """DB 와 heartbeat 를 걷어내고 가드만 남긴다."""
    fake = FakePool()

    async def noop():
        pass

    async def forever():
        await asyncio.Event().wait()

    monkeypatch.setattr(main.db, "connect", noop)
    monkeypatch.setattr(main.db, "disconnect", noop)
    monkeypatch.setattr(main.db, "get_pool", lambda: fake)
    monkeypatch.setattr(main.heartbeat, "monitor", forever)
    monkeypatch.setattr(main, "_lock_conn", None)
    return fake


def _run_lifespan(body=None):
    async def scenario():
        async with main.lifespan(None):
            if body is not None:
                body()

    asyncio.run(scenario())


def test_first_instance_takes_the_lock(pool):
    seen = {}
    _run_lifespan(lambda: seen.update(conn=main._lock_conn))

    assert seen["conn"] is not None


def test_lock_is_released_on_shutdown(pool):
    """재기동이 자기 자신의 락에 걸리면 안 된다."""
    _run_lifespan()

    assert pool.handed_out[0].unlocked is True
    assert pool.released == [pool.handed_out[0]]
    assert main._lock_conn is None


def test_second_instance_refuses_to_start(pool):
    """이미 누가 락을 쥐고 있으면 기동을 실패시킨다."""
    pool.lock_granted = False

    with pytest.raises(SystemExit) as exc:
        _run_lifespan()

    assert exc.value.code == 3


def test_a_terminated_session_is_detected_by_querying_not_by_is_closed(pool):
    """서버가 세션을 끊어도 asyncpg 는 써보기 전까지 모른다.

    실측에서 `pg_terminate_backend` 뒤에도 `is_closed()` 가 False 였다.
    질의를 한 번 던져야 안다.
    """
    async def scenario():
        await main._claim_single_instance()
        main._lock_conn.session_alive = False
        assert main._lock_conn.is_closed() is False

        return await main._still_holding_lock()

    assert asyncio.run(scenario()) is False


def test_a_detached_proxy_does_not_kill_the_watchdog(pool):
    """프록시가 풀로 되돌아가면 `is_closed()` 조차 예외를 던진다.

    이 호출이 try 밖에 있으면 예외가 워치독 태스크를 조용히 죽인다. 태스크는
    shutdown 때까지 await 되지 않으므로 로그도 안 남고, 락이 풀린 뒤에도
    아무것도 감지되지 않는다. 실측에서 정확히 그렇게 됐다.
    """
    async def scenario():
        await main._claim_single_instance()
        main._lock_conn.detached = True

        return await main._still_holding_lock()

    assert asyncio.run(scenario()) is False


def test_a_dropped_lock_is_reclaimed(monkeypatch, pool):
    """DB 재시작·네트워크 블립으로 세션이 끊겨도 계속 돈다.

    락이 풀린 사이 아무도 안 가져갔으면 우리가 여전히 유일한 인스턴스다.
    여기서 죽으면 DB 가 한 번 재시작할 때마다 백엔드가 같이 죽는다.
    """
    monkeypatch.setattr(main, "_LOCK_RECHECK_SEC", 0)

    async def scenario():
        await main._claim_single_instance()
        first = main._lock_conn
        first.session_alive = False

        watch = asyncio.create_task(main._hold_single_instance())
        await asyncio.sleep(0.05)
        watch.cancel()

        assert main._lock_conn is not first     # 새 커넥션으로 다시 잡았다
        assert main._lock_conn.lock_granted is True

    asyncio.run(scenario())


def test_losing_the_lock_to_another_instance_terminates_this_one(monkeypatch, pool):
    """락을 잃은 사이 남이 들어왔으면 늦게 안 쪽이 물러난다.

    둘 다 살아 있으면 인메모리 상태가 갈린다 — 가드가 막으려던 바로 그 상태다.
    """
    monkeypatch.setattr(main, "_LOCK_RECHECK_SEC", 0)
    signalled = {}
    monkeypatch.setattr(main.os, "kill", lambda pid, sig: signalled.update(pid=pid, sig=sig))

    async def scenario():
        await main._claim_single_instance()
        main._lock_conn.session_alive = False
        pool.lock_granted = False               # 그 사이 남이 가져갔다

        await main._hold_single_instance()

    asyncio.run(scenario())

    assert signalled["pid"] == main.os.getpid()   # 남이 아니라 자기 자신에게
    assert signalled["sig"] == main.signal.SIGTERM
