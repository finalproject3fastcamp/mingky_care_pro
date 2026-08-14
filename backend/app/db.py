import asyncpg

from .config import database_url

_pool: asyncpg.Pool | None = None


async def connect() -> None:
    global _pool
    _pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=5)


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool
