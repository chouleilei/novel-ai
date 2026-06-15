import uuid

from sqlalchemy import or_, select, text, update
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.datetime_utils import utcnow
from backend.db.models import (
    AttemptStatus,
    Chapter,
    ChapterAttempt,
    ChapterPrompt,
    ChapterReview,
    ChapterStatus,
    GenerationJob,
    JobStatus,
    ProjectEvent,
    Project,
    ProjectStatus,
)
from backend.services.event_service import EventService
from backend.services.generation_state import (
    accept_chapter,
    advance_after_chapter_success,
    build_generate_chapter_job,
    mark_project_running,
    pause_queued_chapter,
    queue_chapter,
)
from backend.services.memory_service import MemoryService
from backend.services.project_events import (
    ProjectEventType,
    chapter_passed_payload,
    chapter_rewriting_payload,
    memory_updated_payload,
    pipeline_complete_payload,
    pipeline_start_payload,
    project_paused_payload,
)


class GenerationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.events = EventService(session)
        self.memory_service = MemoryService(session)

    async def start_project(self, project: Project) -> GenerationJob:
        project = await self._lock_project(project.id, current_project=project)
        await self._cancel_expired_leased_jobs(project.id)
        if await self._has_active_job(project.id):
            raise ValueError("项目已有进行中的生成任务")
        mark_project_running(project)
        first_pending = await self._get_first_runnable_chapter(project.id)
        if first_pending is None:
            raise ValueError("没有可执行章节，请先导入大纲")
        queue_chapter(first_pending)
        job = await self._enqueue_generate_job(
            project.id,
            first_pending.chapter_number,
        )
        await self.events.append(
            project.id,
            ProjectEventType.PIPELINE_START,
            pipeline_start_payload(chapter_number=first_pending.chapter_number),
            chapter_number=first_pending.chapter_number,
        )
        await self.memory_service.sync_project_review_warning(project.id)
        await self.session.flush()
        return job

    async def pause_project(self, project: Project, reason: str) -> None:
        project = await self._lock_project(project.id, current_project=project)
        if project.status != ProjectStatus.RUNNING.value:
            raise ValueError("仅运行中的项目支持暂停")

        project.status = ProjectStatus.PAUSED.value
        project.last_error = "已收到停止请求，当前章节会尽快停止。"

        cancelled_jobs = await self._cancel_queued_jobs(project.id)
        paused_chapters = await self._pause_queued_chapters(project.id)

        if cancelled_jobs > 0 and paused_chapters == 0 and project.current_chapter > 0:
            current = await self._get_chapter(project.id, project.current_chapter)
            if current is not None and current.status == ChapterStatus.QUEUED.value:
                pause_queued_chapter(current)
                paused_chapters = 1

        await self.events.append(
            project.id,
            ProjectEventType.PROJECT_PAUSED,
            project_paused_payload(
                reason=reason,
                phase="requested",
                cancelled_jobs=cancelled_jobs,
                paused_chapters=paused_chapters,
            ),
        )
        await self.session.flush()

    async def retry_chapter(self, project: Project, chapter_number: int) -> GenerationJob | None:
        project = await self._lock_project(project.id, current_project=project)
        await self._cancel_expired_leased_jobs(project.id, chapter_number=chapter_number)
        chapter: Chapter | None = None
        if getattr(project, "status", None) == ProjectStatus.PAUSED.value:
            await self._cleanup_paused_queued_generation_state(project.id, chapter_number=chapter_number)
            if hasattr(self.session, "execute"):
                chapter = await self._get_chapter(project.id, chapter_number)
                if chapter is None:
                    return None
                await self._cleanup_terminal_chapter_active_job(project.id, chapter)
        if await self._has_queued_job(project.id):
            raise ValueError("项目已有进行中的章节级生成任务，请等待当前任务完成后再重试")
        if await self._has_live_leased_job(project.id):
            raise ValueError("项目已有进行中的章节级生成任务，请等待当前任务完成后再重试")
        if chapter is None:
            chapter = await self._get_chapter(project.id, chapter_number)
            if chapter is None:
                return None
        if chapter.status not in {ChapterStatus.FAILED.value, ChapterStatus.PAUSED.value, ChapterStatus.PASSED.value}:
            raise ValueError("仅失败、暂停或已完成的章节支持重试")

        mark_project_running(project)
        queue_chapter(chapter)
        chapter.final_content = None
        chapter.final_score = None
        chapter.accepted_attempt_id = None
        chapter.auto_accepted = False
        chapter.last_error = None
        job = await self._enqueue_generate_job(
            project.id,
            chapter_number,
            payload={"retry": True, "chapter_retry": True},
        )
        await self.events.append(
            project.id,
            ProjectEventType.CHAPTER_REWRITING,
            chapter_rewriting_payload(queued=True, reason="retry_current_chapter"),
            chapter_number=chapter_number,
        )
        await self.memory_service.sync_project_review_warning(project.id)
        await self.session.flush()
        return job

    async def resume_project(self, project: Project) -> GenerationJob:
        project = await self._lock_project(project.id, current_project=project)
        await self._cancel_expired_leased_jobs(project.id)
        current: Chapter | None = None
        resume_target: Chapter | None = None
        if getattr(project, "status", None) == ProjectStatus.PAUSED.value:
            await self._cleanup_paused_queued_generation_state(project.id)
            if hasattr(self.session, "execute"):
                current = await self._get_stale_inflight_chapter(project.id)
                resume_target = current
                if resume_target is None:
                    resume_target = await self._get_first_runnable_chapter(project.id)
                if resume_target is not None:
                    await self._cleanup_terminal_chapter_active_job(project.id, resume_target)
        if await self._has_queued_job(project.id):
            raise ValueError("项目仍有排队中的生成任务，请稍后再继续")
        if await self._has_live_leased_job(project.id):
            raise ValueError("项目仍有正在收尾的生成任务，请稍后再继续")
        mark_project_running(project)
        if current is None:
            current = resume_target or await self._get_stale_inflight_chapter(project.id)
        if current is None:
            current = await self._get_first_runnable_chapter(project.id)
        if current is None:
            raise ValueError("没有可恢复章节")
        queue_chapter(current)
        resume_payload = await self._build_resume_payload(current)
        job = await self._enqueue_generate_job(
            project.id,
            current.chapter_number,
            payload=resume_payload,
        )
        await self.events.append(
            project.id,
            ProjectEventType.PIPELINE_START,
            pipeline_start_payload(resume=True, chapter_number=current.chapter_number),
            chapter_number=current.chapter_number,
        )
        await self.memory_service.sync_project_review_warning(project.id)
        await self.session.flush()
        return job

    async def manual_approve_chapter(self, project: Project, chapter_number: int) -> Chapter | None:
        project = await self._lock_project(project.id, current_project=project)
        await self._cancel_expired_leased_jobs(project.id)
        if getattr(project, "status", None) == ProjectStatus.PAUSED.value:
            await self._cleanup_paused_queued_generation_state(project.id)
        if await self._has_queued_job(project.id):
            raise ValueError("项目仍有排队中的生成任务，请稍后再人工通过")
        if await self._has_live_leased_job(project.id):
            raise ValueError("项目仍有正在收尾的生成任务，请等待当前任务完成后再人工通过")

        chapter = await self._get_chapter(project.id, chapter_number)
        if chapter is None:
            return None
        if chapter.status != ChapterStatus.FAILED.value:
            raise ValueError("仅失败中的章节支持人工通过")

        attempt = await self._get_latest_approvable_attempt(chapter.id)
        if attempt is None or not attempt.content or not attempt.content.strip():
            raise ValueError("当前章节没有可人工通过的草稿")

        review = await self._get_review_for_attempt(attempt.id)
        accepted_score = float(review.overall_score) if review is not None and review.overall_score is not None else None

        accept_chapter(
            project=project,
            chapter=chapter,
            attempt=attempt,
            content=attempt.content,
            score=accepted_score,
            auto_accepted=False,
        )

        memory_result = await self.memory_service.save_summary_and_revisions(
            project.id,
            chapter.chapter_number,
            attempt.content,
        )
        await self.events.append(
            project.id,
            ProjectEventType.MEMORY_UPDATED,
            memory_updated_payload(
                chapter_number=chapter.chapter_number,
                character_revision_count=memory_result["character_revision_count"],
                world_revision_count=memory_result["world_revision_count"],
                applied_revision_count=memory_result["applied_revision_count"],
                needs_review_count=memory_result["needs_review_count"],
            ),
            chapter.chapter_number,
        )
        await self.events.append(
            project.id,
            ProjectEventType.CHAPTER_PASSED,
            chapter_passed_payload(score=chapter.final_score, manual=True, auto_accepted=False),
            chapter.chapter_number,
        )

        next_chapter = await self._get_next_runnable_chapter(project.id, chapter.chapter_number)
        advance_result = advance_after_chapter_success(project=project, next_chapter=next_chapter)
        if advance_result.next_chapter is None:
            await self.events.append(project.id, ProjectEventType.PIPELINE_COMPLETE, pipeline_complete_payload(project.id))
        elif advance_result.queued_job is None:
            await self.events.append(
                project.id,
                ProjectEventType.PROJECT_PAUSED,
                project_paused_payload(reason="waiting_manual_resume", next_chapter=advance_result.next_chapter.chapter_number),
                chapter.chapter_number,
            )
        else:
            await self._enqueue_generate_job(
                advance_result.queued_job.project_id,
                advance_result.queued_job.chapter_number,
                payload=advance_result.queued_job.payload,
            )

        await self.memory_service.sync_project_review_warning(project.id)
        await self.session.flush()
        return chapter

    async def continue_chapter(self, project: Project, chapter_number: int) -> GenerationJob | None:
        project = await self._lock_project(project.id, current_project=project)
        await self._cancel_expired_leased_jobs(project.id, chapter_number=chapter_number)
        if getattr(project, "status", None) == ProjectStatus.PAUSED.value:
            await self._cleanup_paused_queued_generation_state(project.id, chapter_number=chapter_number)
        if await self._has_queued_job(project.id):
            raise ValueError("项目已有进行中的章节级生成任务，请等待当前任务完成后再续写")
        if await self._has_live_leased_job(project.id):
            raise ValueError("项目已有进行中的章节级生成任务，请等待当前任务完成后再续写")

        chapter = await self._get_chapter(project.id, chapter_number)
        if chapter is None:
            return None
        if chapter.status not in {
            ChapterStatus.PAUSED.value,
            ChapterStatus.FAILED.value,
            ChapterStatus.PASSED.value,
        }:
            raise ValueError("仅暂停、失败或已完成的章节支持断点续写")

        latest_attempt = await self._get_latest_approvable_attempt(chapter.id)
        if latest_attempt is None or not latest_attempt.content or not latest_attempt.content.strip():
            raise ValueError("当前章节没有可续写的草稿")

        mark_project_running(project)
        queue_chapter(chapter)
        chapter.final_content = None
        chapter.final_score = None
        chapter.accepted_attempt_id = None
        chapter.auto_accepted = False
        chapter.last_error = None

        payload: dict[str, object] = {
            "resume": True,
            "continue_from_attempt_id": str(latest_attempt.id),
            "continue_from_attempt_no": latest_attempt.attempt_no,
            "continue_from_content_chars": len(latest_attempt.content.strip()),
            "chapter_continue": True,
        }
        job = await self._enqueue_generate_job(
            project.id,
            chapter_number,
            payload=payload,
        )
        await self.events.append(
            project.id,
            ProjectEventType.CHAPTER_REWRITING,
            chapter_rewriting_payload(queued=True, reason="continue_from_breakpoint"),
            chapter_number=chapter_number,
        )
        await self.events.append(
            project.id,
            ProjectEventType.PIPELINE_START,
            pipeline_start_payload(chapter_number=chapter_number, resume=True),
            chapter_number=chapter_number,
        )
        await self.memory_service.sync_project_review_warning(project.id)
        await self.session.flush()
        return job

    async def rewrite_from_chapter(self, project: Project, chapter_number: int) -> GenerationJob | None:
        if chapter_number < 1:
            raise ValueError("章节号必须大于等于 1")

        project = await self._lock_project(project.id, current_project=project)
        await self._cancel_expired_leased_jobs(project.id)

        if getattr(project, "status", None) == ProjectStatus.PAUSED.value:
            await self._cleanup_paused_queued_generation_state(project.id)

        if await self._has_queued_job(project.id):
            raise ValueError("项目仍有排队中的生成任务，请稍后再重写")
        if await self._has_live_leased_job(project.id):
            raise ValueError("项目仍有正在收尾的生成任务，请等待当前任务完成后再重写")

        chapter = await self._get_chapter(project.id, chapter_number)
        if chapter is None:
            return None
        if chapter.status == ChapterStatus.PENDING.value:
            raise ValueError("待处理章节不能作为重写起点")

        await self._reset_generation_from_chapter(project.id, chapter_number)

        project.current_chapter = chapter_number - 1
        project.status = ProjectStatus.READY.value
        project.last_error = None
        project.lease_owner = None
        project.lease_expires_at = None
        project.distant_memory_cache = None
        project.distant_memory_updated_chapter = chapter_number - 1 if chapter_number > 1 else None

        queue_chapter(chapter)
        mark_project_running(project)
        job = await self._enqueue_generate_job(project.id, chapter_number)

        await self.events.append(
            project.id,
            ProjectEventType.CHAPTER_REWRITING,
            chapter_rewriting_payload(queued=True, reason="rewrite_from_here"),
            chapter_number=chapter_number,
        )
        await self.events.append(
            project.id,
            ProjectEventType.PIPELINE_START,
            pipeline_start_payload(chapter_number=chapter_number),
            chapter_number=chapter_number,
        )
        await self.memory_service.sync_project_review_warning(project.id)
        await self.session.flush()
        return job

    async def list_chapters(self, project_id: uuid.UUID) -> list[Chapter]:
        stmt = select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.chapter_number.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_chapter(self, project_id: uuid.UUID, chapter_number: int) -> Chapter | None:
        return await self._get_chapter(project_id, chapter_number)

    async def list_active_job_chapter_numbers(self, project_id: uuid.UUID) -> set[int]:
        now = utcnow()
        stmt = select(GenerationJob.chapter_number).where(
            GenerationJob.project_id == project_id,
            GenerationJob.chapter_number.is_not(None),
            or_(
                GenerationJob.status == JobStatus.QUEUED.value,
                (
                    GenerationJob.status == JobStatus.LEASED.value
                )
                & (GenerationJob.lease_expires_at.is_not(None))
                & (GenerationJob.lease_expires_at >= now),
            ),
        )
        result = await self.session.execute(stmt)
        return {chapter_number for chapter_number in result.scalars() if chapter_number is not None}

    async def chapter_has_active_job(self, project_id: uuid.UUID, chapter_number: int) -> bool:
        return await self._has_active_job(project_id, chapter_number)

    async def _get_first_runnable_chapter(self, project_id: uuid.UUID) -> Chapter | None:
        await self._repair_orphan_queued_chapters(project_id)
        stmt = (
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.accepted_attempt_id.is_(None),
                Chapter.status.in_(
                    [
                        ChapterStatus.PENDING.value,
                        ChapterStatus.FAILED.value,
                        ChapterStatus.PAUSED.value,
                    ]
                ),
            )
            .order_by(Chapter.chapter_number.asc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_stale_inflight_chapter(self, project_id: uuid.UUID) -> Chapter | None:
        stmt = (
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.status.in_(
                    [
                        ChapterStatus.WRITING.value,
                        ChapterStatus.REVIEWING.value,
                        ChapterStatus.QUEUED.value,
                    ]
                ),
            )
            .order_by(Chapter.chapter_number.asc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_next_runnable_chapter(self, project_id: uuid.UUID, after_chapter_number: int) -> Chapter | None:
        await self._repair_orphan_queued_chapters(project_id, after_chapter_number=after_chapter_number)
        stmt = (
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number > after_chapter_number,
                Chapter.status.in_(
                    [
                        ChapterStatus.PENDING.value,
                        ChapterStatus.FAILED.value,
                        ChapterStatus.PAUSED.value,
                    ]
                ),
            )
            .order_by(Chapter.chapter_number.asc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_latest_attempt(self, chapter_id: uuid.UUID) -> ChapterAttempt | None:
        stmt = (
            select(ChapterAttempt)
            .where(ChapterAttempt.chapter_id == chapter_id)
            .order_by(ChapterAttempt.attempt_no.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _build_resume_payload(self, chapter: Chapter) -> dict[str, object]:
        payload: dict[str, object] = {"resume": True}
        if not hasattr(self.session, "execute"):
            return payload
        chapter_id = getattr(chapter, "id", None)
        if chapter_id is None:
            return payload
        latest_attempt = await self._get_latest_attempt(chapter_id)
        if latest_attempt is None:
            return payload
        if not await self._attempt_can_resume(latest_attempt):
            return payload
        content = (latest_attempt.content or "").strip()
        if not content:
            return payload
        payload["continue_from_attempt_id"] = str(latest_attempt.id)
        payload["continue_from_attempt_no"] = latest_attempt.attempt_no
        payload["continue_from_content_chars"] = len(content)
        return payload

    async def _attempt_can_resume(self, attempt: ChapterAttempt) -> bool:
        if getattr(attempt, "status", None) not in {
            AttemptStatus.RUNNING.value,
            AttemptStatus.ERRORED.value,
        }:
            return False
        review = await self._get_review_for_attempt(attempt.id)
        return review is None

    async def _get_latest_approvable_attempt(self, chapter_id: uuid.UUID) -> ChapterAttempt | None:
        stmt = (
            select(ChapterAttempt)
            .where(
                ChapterAttempt.chapter_id == chapter_id,
                ChapterAttempt.content.is_not(None),
                ChapterAttempt.content != "",
            )
            .order_by(ChapterAttempt.attempt_no.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        attempt = result.scalar_one_or_none()
        if attempt is None or not attempt.content or not attempt.content.strip():
            return None
        return attempt

    async def _get_review_for_attempt(self, attempt_id: uuid.UUID) -> ChapterReview | None:
        stmt = select(ChapterReview).where(ChapterReview.attempt_id == attempt_id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_active_job(self, project_id: uuid.UUID, chapter_number: int | None = None) -> GenerationJob | None:
        now = utcnow()
        stmt = select(GenerationJob).where(
            GenerationJob.project_id == project_id,
            or_(
                GenerationJob.status == JobStatus.QUEUED.value,
                (
                    GenerationJob.status == JobStatus.LEASED.value
                )
                & (GenerationJob.lease_expires_at.is_not(None))
                & (GenerationJob.lease_expires_at >= now),
            ),
        )
        if chapter_number is not None:
            stmt = stmt.where(GenerationJob.chapter_number == chapter_number)
        stmt = stmt.order_by(GenerationJob.created_at.asc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _lock_project(self, project_id: uuid.UUID, *, current_project: Project | None = None) -> Project:
        if not hasattr(self.session, "scalar"):
            if current_project is None:
                raise ValueError("项目不存在")
            return current_project

        stmt = select(Project).where(Project.id == project_id).with_for_update()
        project = await self.session.scalar(stmt)
        if project is None:
            raise ValueError("项目不存在")
        return project

    async def _enqueue_generate_job(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        payload: dict[str, object] | None = None,
    ) -> GenerationJob:
        if not hasattr(self.session, "execute"):
            job = build_generate_chapter_job(
                project_id=project_id,
                chapter_number=chapter_number,
                payload=payload,
            )
            self.session.add(job)
            return job

        existing_job = await self._get_active_job(project_id, chapter_number)
        if existing_job is not None:
            return existing_job

        job = build_generate_chapter_job(
            project_id=project_id,
            chapter_number=chapter_number,
            payload=payload,
        )
        stmt = (
            insert(GenerationJob)
            .values(
                id=job.id,
                project_id=job.project_id,
                chapter_number=job.chapter_number,
                job_type=job.job_type,
                status=job.status,
                payload=job.payload,
                lease_owner=job.lease_owner,
                lease_expires_at=job.lease_expires_at,
                run_count=job.run_count,
                last_error=job.last_error,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    GenerationJob.project_id,
                    GenerationJob.chapter_number,
                    GenerationJob.job_type,
                ],
                index_where=text("chapter_number IS NOT NULL AND status IN ('queued', 'leased')"),
            )
        )
        result = await self.session.execute(stmt)
        if result.rowcount:
            return job

        existing_job = await self._get_active_job(project_id, chapter_number)
        if existing_job is not None:
            return existing_job

        raise ValueError("该章节的任务已在另一处处理完毕，无法重复入队")

    async def _has_active_job(self, project_id: uuid.UUID, chapter_number: int | None = None) -> bool:
        return await self._get_active_job(project_id, chapter_number) is not None

    async def _has_queued_job(self, project_id: uuid.UUID, chapter_number: int | None = None) -> bool:
        stmt = select(GenerationJob).where(
            GenerationJob.project_id == project_id,
            GenerationJob.status == JobStatus.QUEUED.value,
        )
        if chapter_number is not None:
            stmt = stmt.where(GenerationJob.chapter_number == chapter_number)
        stmt = stmt.limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _has_live_leased_job(self, project_id: uuid.UUID, chapter_number: int | None = None) -> bool:
        now = utcnow()
        stmt = select(GenerationJob).where(
            GenerationJob.project_id == project_id,
            GenerationJob.status == JobStatus.LEASED.value,
            GenerationJob.lease_expires_at.is_not(None),
            GenerationJob.lease_expires_at >= now,
        )
        if chapter_number is not None:
            stmt = stmt.where(GenerationJob.chapter_number == chapter_number)
        stmt = stmt.limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _cleanup_paused_queued_generation_state(
        self,
        project_id: uuid.UUID,
        chapter_number: int | None = None,
    ) -> None:
        await self._cancel_queued_jobs(project_id, chapter_number=chapter_number)
        await self._pause_queued_chapters(project_id, chapter_number=chapter_number)

    async def _cleanup_terminal_chapter_active_job(self, project_id: uuid.UUID, chapter: Chapter) -> bool:
        if chapter.status not in {ChapterStatus.FAILED.value, ChapterStatus.PAUSED.value}:
            return False

        active_job = await self._get_active_job(project_id, chapter.chapter_number)
        if active_job is None:
            return False

        active_job.status = JobStatus.CANCELLED.value
        active_job.lease_owner = None
        active_job.lease_expires_at = None
        active_job.last_error = "章节已进入终态，已清理残留生成任务。"
        return True

    async def _repair_orphan_queued_chapters(
        self,
        project_id: uuid.UUID,
        *,
        after_chapter_number: int | None = None,
    ) -> int:
        stmt = select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.status == ChapterStatus.QUEUED.value,
        )
        if after_chapter_number is not None:
            stmt = stmt.where(Chapter.chapter_number > after_chapter_number)
        result = await self.session.execute(stmt)
        chapters = list(result.scalars())

        repaired = 0
        for chapter in chapters:
            if not await self._has_active_job(project_id, chapter.chapter_number):
                pause_queued_chapter(chapter)
                repaired += 1
        return repaired

    async def _cancel_expired_leased_jobs(self, project_id: uuid.UUID, chapter_number: int | None = None) -> int:
        stmt = (
            update(GenerationJob)
            .where(
                GenerationJob.project_id == project_id,
                GenerationJob.status == JobStatus.LEASED.value,
                GenerationJob.lease_expires_at.is_not(None),
                GenerationJob.lease_expires_at < utcnow(),
            )
            .values(
                status=JobStatus.CANCELLED.value,
                lease_owner=None,
                lease_expires_at=None,
                last_error="项目恢复前已清理过期租约任务。",
            )
        )
        if chapter_number is not None:
            stmt = stmt.where(GenerationJob.chapter_number == chapter_number)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def _get_chapter(self, project_id: uuid.UUID, chapter_number: int) -> Chapter | None:
        stmt = select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _cancel_queued_jobs(self, project_id: uuid.UUID, chapter_number: int | None = None) -> int:
        stmt = (
            update(GenerationJob)
            .where(
                GenerationJob.project_id == project_id,
                GenerationJob.status == JobStatus.QUEUED.value,
            )
            .values(
                status=JobStatus.CANCELLED.value,
                last_error="项目已暂停，排队中的任务已取消。",
            )
        )
        if chapter_number is not None:
            stmt = stmt.where(GenerationJob.chapter_number == chapter_number)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def _reset_generation_from_chapter(self, project_id: uuid.UUID, chapter_number: int) -> None:
        target_chapter_ids_stmt = select(Chapter.id).where(
            Chapter.project_id == project_id,
            Chapter.chapter_number >= chapter_number,
        )
        target_chapter_ids_result = await self.session.execute(target_chapter_ids_stmt)
        target_chapter_ids = list(target_chapter_ids_result.scalars())

        if target_chapter_ids:
            attempt_ids_stmt = select(ChapterAttempt.id).where(ChapterAttempt.chapter_id.in_(target_chapter_ids))
            attempt_ids_result = await self.session.execute(attempt_ids_stmt)
            attempt_ids = list(attempt_ids_result.scalars())
            if attempt_ids:
                await self.session.execute(delete(ChapterReview).where(ChapterReview.attempt_id.in_(attempt_ids)))
            await self.session.execute(delete(ChapterAttempt).where(ChapterAttempt.chapter_id.in_(target_chapter_ids)))

        await self.session.execute(
            delete(ChapterPrompt).where(
                ChapterPrompt.project_id == project_id,
                ChapterPrompt.chapter_number >= chapter_number,
            )
        )
        await self.session.execute(
            delete(ProjectEvent).where(
                ProjectEvent.project_id == project_id,
                ProjectEvent.chapter_number.is_not(None),
                ProjectEvent.chapter_number >= chapter_number,
            )
        )
        await self.session.execute(
            delete(ProjectEvent).where(
                ProjectEvent.project_id == project_id,
                ProjectEvent.chapter_number.is_(None),
                ProjectEvent.event_type.in_(
                    [
                        ProjectEventType.PIPELINE_COMPLETE.value,
                        ProjectEventType.PROJECT_PAUSED.value,
                    ]
                ),
            )
        )
        await self.memory_service.reset_resources_from_chapter(project_id, chapter_number)
        await self.session.execute(
            update(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number >= chapter_number,
            )
            .values(
                status=ChapterStatus.PENDING.value,
                retry_count=0,
                final_content=None,
                final_score=None,
                accepted_attempt_id=None,
                improvement_notes=None,
                last_error=None,
            )
        )

    async def _pause_queued_chapters(self, project_id: uuid.UUID, chapter_number: int | None = None) -> int:
        stmt = select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.status == ChapterStatus.QUEUED.value,
        )
        if chapter_number is not None:
            stmt = stmt.where(Chapter.chapter_number == chapter_number)
        result = await self.session.execute(stmt)
        chapters = list(result.scalars())
        for chapter in chapters:
            pause_queued_chapter(chapter)
        return len(chapters)
