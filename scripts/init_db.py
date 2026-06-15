import asyncio

from backend.db.base import Base, engine
from backend.db.models import *  # noqa: F403


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(init_db())
