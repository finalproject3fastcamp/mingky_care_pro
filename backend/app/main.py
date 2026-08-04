import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db, registry
from .routers import events, qr, robots, sessions

log = logging.getLogger("mingky")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 이벤트 코드 정본이 없거나 깨졌으면 여기서 죽는다.
    # 검증 없이 뜨면 미등록 코드가 조용히 쌓이므로 그쪽이 더 나쁘다.
    codes = registry.load()
    log.info("event_codes 로드: %s (코드 %d개)", codes.source, len(codes))

    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(title="Mingky Care Backend", lifespan=lifespan)
app.include_router(qr.router)
app.include_router(events.router)
app.include_router(sessions.router)
app.include_router(robots.router)


@app.get("/health")
async def health():
    return {"status": "ok", "event_codes": len(registry.get_registry())}
