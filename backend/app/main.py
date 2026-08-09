import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db, heartbeat, registry
from .routers import events, orders, patients, qr, robots, sessions

log = logging.getLogger("mingky")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 이벤트 코드 정본이 없거나 깨졌으면 여기서 죽는다.
    # 검증 없이 뜨면 미등록 코드가 조용히 쌓이므로 그쪽이 더 나쁘다.
    codes = registry.load()
    log.info("event_codes 로드: %s (코드 %d개)", codes.source, len(codes))

    await db.connect()

    # 생존 감시. 재시작하면 메모리가 비므로 감시 대상도 0 에서 시작한다.
    # 첫 heartbeat 를 받은 로봇부터 감시에 들어가므로 기동 직후 전원이
    # 두절로 찍히는 일은 없고, 별도 유예 로직도 필요 없다.
    monitor = asyncio.create_task(heartbeat.monitor())
    try:
        yield
    finally:
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass
        await db.disconnect()


app = FastAPI(title="Mingky Care Backend", lifespan=lifespan)
app.include_router(qr.router)
app.include_router(events.router)
app.include_router(sessions.router)
app.include_router(robots.router)
app.include_router(patients.router)
# robots.router 보다 뒤에 둔다. 경로 접두사가 같아 등록 순서가 매칭에 영향을 준다.
app.include_router(orders.router)


@app.get("/health")
async def health():
    return {"status": "ok", "event_codes": len(registry.get_registry())}
