from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.datetime_utils import utcnow
from backend.db.models import (
    AttemptStatus,
    Chapter,
    ChapterAttempt,
    ChapterStatus,
    GenerationJob,
    JobStatus,
    Project,
    ProjectStatus,
)
from backend.services.generation_state import apply_job_failure
from backend.services.retention_cleanup_service import RetentionCleanupService

DEFAULT_JOB_ERROR_MESSAGE = "任务执行失败，未返回详细错误信息。"


def normalize_job_error_message(error_message: str | None) -> str:
    message = (error_message or "").strip()
    return message or DEFAULT_JOB_ERROR_MESSAGE


class JobQueue:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def claim_next_job(self) -> GenerationJob | None:
        now = utcnow()
        stmt = (
            select(GenerationJob)
            .join(Project, Project.id == GenerationJob.project_id)
            .where(
                or_(
                    GenerationJob.status == JobStatus.QUEUED.value,
                    (
                        GenerationJob.status == JobStatus.LEASED.value
                    )
                    & (GenerationJob.lease_expires_at < now),
                )
            )
            .where(
                or_(
                    Project.lease_expires_at.is_(None),
                    Project.lease_expires_at < now,
                )
            )
            .where(Project.status == ProjectStatus.RUNNING.value)
            .order_by(GenerationJob.created_at.asc())
            .with_for_update(skip_locked=True, of=(GenerationJob, Project))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        job = result.scalar_one_or_none()
        if job is None:
            return None
        job.status = JobStatus.LEASED.value
        job.lease_owner = self.settings.worker_name
        job.lease_expires_at = now + timedelta(seconds=self.settings.worker_lease_seconds)
        job.run_count += 1
        project = await self.session.get(Project, job.project_id)
        if project is not None:
            project.lease_owner = self.settings.worker_name
            project.lease_expires_at = job.lease_expires_at
        await self.session.flush()
        return job

    async def renew_lease(self, job_id, project_id) -> bool:
        now = utcnow()
        expires_at = now + timedelta(seconds=self.settings.worker_lease_seconds)
        result = await self.session.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job_id,
                GenerationJob.status == JobStatus.LEASED.value,
                GenerationJob.lease_owner == self.settings.worker_name,
            )
            .values(lease_expires_at=expires_at)
        )
        if result.rowcount == 0:
            return False
        await self.session.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.lease_owner == self.settings.worker_name,
            )
            .values(lease_expires_at=expires_at)
        )
        await self.session.flush()
        return True

    async def mark_done(self, job: GenerationJob) -> None:
        job.status = JobStatus.DONE.value
        job.lease_owner = None
        job.lease_expires_at = None
        project = await self.session.get(Project, job.project_id)
        if project is not None and project.lease_owner == self.settings.worker_name:
            project.lease_owner = None
            project.lease_expires_at = None
        await self.session.flush()

    async def mark_failed(self, job: GenerationJob, error_message: str) -> None:
        normalized_error_message = normalize_job_error_message(error_message)
        job.status = JobStatus.FAILED.value
        job.last_error = normalized_error_message
        job.lease_owner = None
        job.lease_expires_at = None
        project = await self.session.get(Project, job.project_id)
        if project is not None:
            if project.lease_owner == self.settings.worker_name:
                project.lease_owner = None
                project.lease_expires_at = None
        if job.chapter_number is not None:
            await RetentionCleanupService(self.session).cleanup_chapter_content_chunks(
                job.project_id,
                job.chapter_number,
            )
        chapter = None
        attempt = None
        if job.chapter_number is not None:
            chapter = await self.session.scalar(
                select(Chapter).where(
                    Chapter.project_id == job.project_id,
                    Chapter.chapter_number == job.chapter_number,
                )
            )
            if chapter is not None:
                attempt = await self.session.scalar(
                    select(ChapterAttempt)
                    .where(ChapterAttempt.chapter_id == chapter.id)
                    .order_by(ChapterAttempt.attempt_no.desc())
                    .limit(1)
                )
        apply_job_failure(
            project=project,
            chapter=chapter,
            attempt=attempt,
            error_message=normalized_error_message,
        )
        await self.session.flush()
