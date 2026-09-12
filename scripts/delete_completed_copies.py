"""Delete all completed copy projects (keeps the original source project).

Usage: python scripts/delete_completed_copies.py <source_project_id>
"""

import asyncio
import sys

from sqlalchemy import select

from backend.db.base import AsyncSessionLocal
from backend.db.models import Project, ProjectStatus
from backend.services.project_service import ProjectService


async def main() -> None:
    source_id = sys.argv[1]

    async with AsyncSessionLocal() as session:
        project_service = ProjectService(session)
        source = await session.get(Project, source_id)
        if source is None:
            raise SystemExit(f"source project not found: {source_id}")
        prefix = f"{source.title}-副本"

        result = await session.execute(
            select(Project).where(
                Project.title.like(f"{prefix}%"),
                Project.status == ProjectStatus.COMPLETED.value,
            )
        )
        targets = list(result.scalars())
        print(f"deleting {len(targets)} completed copies with prefix '{prefix}'")
        for index, project in enumerate(targets, 1):
            await project_service.delete_project(project.id)
            if index % 20 == 0:
                await session.commit()
                print(f"progress: {index}/{len(targets)}")
        await session.commit()
        print(f"done: deleted {len(targets)} completed copies")


if __name__ == "__main__":
    asyncio.run(main())
