from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
from .routers import qr


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(title="Mingky Care Backend", lifespan=lifespan)
app.include_router(qr.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
