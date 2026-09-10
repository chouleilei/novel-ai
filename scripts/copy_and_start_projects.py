"""Copy an existing project N times (no content) and start generation for each copy.

Usage: python scripts/copy_and_start_projects.py <source_project_id> <count>
"""

import asyncio
import sys

from sqlalchemy import select

from backend.db.base import AsyncSessionLocal
from backend.db.models import Project
from backend.services.generation_service import GenerationService
from backend.services.project_service import ProjectService


async def main() -> None:
    source_id = sys.argv[1]
    count = int(sys.argv[2])

    async with AsyncSessionLocal() as session:
        project_service = ProjectService(session)
        generation_service = GenerationService(session)

        source = await session.get(Project, source_id)
        if source is None:
            raise SystemExit(f"source project not found: {source_id}")
        print(f"source: {source.title} ({source.total_chapters} chapters, mode={source.generation_mode})")

        started = 0
        for index in range(1, count + 1):
            copied = await project_service.copy_project(source.id)
            assert copied is not None
            copied.title = f"{source.title}-副本{index:03d}"
            await session.flush()
            await generation_service.start_project(copied)
            started += 1
            if started % 20 == 0:
                await session.commit()
                print(f"progress: {started}/{count}")
        await session.commit()
        print(f"done: copied and started {started} projects")


if __name__ == "__main__":
    asyncio.run(main())
