import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import (
    Chapter,
    ChapterAttempt,
    ChapterPrompt,
    CharacterRevision,
    GenerationJob,
    JobStatus,
    Project,
    ProjectEvent,
    ProjectStatus,
    WorldSettingRevision,
)
from backend.services.project_events import ProjectEventType


class RetentionCleanupService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cleanup_chapter_content_chunks(self, project_id: uuid.UUID, chapter_number: int) -> None:
        await self.session.execute(
            delete(ProjectEvent).where(
                ProjectEvent.project_id == project_id,
                ProjectEvent.chapter_number == chapter_number,
                ProjectEvent.event_type == ProjectEventType.CONTENT_CHUNK.value,
            )
        )

    async def cleanup_completed_project(self, project_id: uuid.UUID, *, exclude_job_id: uuid.UUID | None = None) -> None:
        project = await self.session.get(Project, project_id)
        if project is None or project.status != ProjectStatus.COMPLETED.value:
            return
        if await self._has_active_jobs(project_id, exclude_job_id=exclude_job_id):
            return

        chapter_ids = select(Chapter.id).where(Chapter.project_id == project_id)
        await self.session.execute(
            update(ChapterAttempt)
            .where(ChapterAttempt.chapter_id.in_(chapter_ids))
            .values(input_snapshot={}, content=None)
        )
        await self.session.execute(delete(ChapterPrompt).where(ChapterPrompt.project_id == project_id))
        await self.session.execute(delete(ProjectEvent).where(ProjectEvent.project_id == project_id))
        await self.session.execute(delete(CharacterRevision).where(CharacterRevision.project_id == project_id))
        await self.session.execute(delete(WorldSettingRevision).where(WorldSettingRevision.project_id == project_id))
        await self.session.execute(
            delete(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.status.in_(
                    [
                        JobStatus.DONE.value,
                        JobStatus.FAILED.value,
                        JobStatus.CANCELLED.value,
                    ]
                ),
            )
        )

    async def _has_active_jobs(self, project_id: uuid.UUID, *, exclude_job_id: uuid.UUID | None = None) -> bool:
        stmt = select(GenerationJob.id).where(
            GenerationJob.project_id == project_id,
            GenerationJob.status.in_([JobStatus.QUEUED.value, JobStatus.LEASED.value]),
        )
        if exclude_job_id is not None:
            stmt = stmt.where(GenerationJob.id != exclude_job_id)
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None
