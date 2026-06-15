import uuid
from dataclasses import dataclass
from typing import Any

from backend.datetime_utils import utcnow
from backend.db.models import (
    AttemptStatus,
    Chapter,
    ChapterAttempt,
    ChapterStatus,
    GenerationJob,
    JobStatus,
    JobType,
    Project,
    ProjectStatus,
)


MAX_RETRIES_PAUSED_MESSAGE = '章节达到最大重试次数，项目已暂停。'
QUEUED_CHAPTER_PAUSED_MESSAGE = '已在执行前停止，可点击继续后重跑本章。'
MANUAL_STOP_CHAPTER_MESSAGE = '已手动停止当前章节，可继续后从当前断点续写；也可手动重试整章重写。'
MANUAL_STOP_PROJECT_MESSAGE = '已停止当前章节，已保留本次生成内容，可点击继续后从当前断点续写。'


@dataclass
class ChapterAdvanceResult:
    queued_job: GenerationJob | None
    next_chapter: Chapter | None


def build_generate_chapter_job(
    *,
    project_id: uuid.UUID,
    chapter_number: int,
    payload: dict[str, Any] | None = None,
) -> GenerationJob:
    now = utcnow()
    return GenerationJob(
        id=uuid.uuid4(),
        project_id=project_id,
        chapter_number=chapter_number,
        job_type=JobType.GENERATE_CHAPTER.value,
        status=JobStatus.QUEUED.value,
        payload=payload or {},
        run_count=0,
        created_at=now,
        updated_at=now,
    )


def mark_project_running(project: Project) -> None:
    project.status = ProjectStatus.RUNNING.value
    project.last_error = None


def queue_chapter(chapter: Chapter) -> None:
    chapter.status = ChapterStatus.QUEUED.value
    chapter.last_error = None


def pause_queued_chapter(chapter: Chapter) -> None:
    chapter.status = ChapterStatus.PAUSED.value
    chapter.last_error = QUEUED_CHAPTER_PAUSED_MESSAGE


def accept_chapter(
    *,
    project: Project,
    chapter: Chapter,
    attempt: ChapterAttempt,
    content: str,
    score: float | None,
    auto_accepted: bool = False,
    improvement_notes: str | None = None,
) -> None:
    attempt.status = AttemptStatus.ACCEPTED.value
    chapter.status = ChapterStatus.PASSED.value
    chapter.final_content = content
    chapter.final_score = score
    chapter.accepted_attempt_id = attempt.id
    chapter.auto_accepted = auto_accepted
    chapter.last_error = None
    if improvement_notes is not None:
        chapter.improvement_notes = improvement_notes
    project.current_chapter = chapter.chapter_number
    project.last_error = None


def advance_after_chapter_success(
    *,
    project: Project,
    next_chapter: Chapter | None,
) -> ChapterAdvanceResult:
    if next_chapter is None:
        project.status = ProjectStatus.COMPLETED.value
        project.last_error = None
        return ChapterAdvanceResult(queued_job=None, next_chapter=None)
    if not project.auto_mode:
        project.status = ProjectStatus.PAUSED.value
        project.last_error = None
        return ChapterAdvanceResult(queued_job=None, next_chapter=next_chapter)

    mark_project_running(project)
    queue_chapter(next_chapter)
    return ChapterAdvanceResult(
        queued_job=build_generate_chapter_job(
            project_id=project.id,
            chapter_number=next_chapter.chapter_number,
        ),
        next_chapter=next_chapter,
    )


def apply_review_failure(*, project: Project, chapter: Chapter) -> GenerationJob | None:
    chapter.retry_count += 1
    chapter.last_error = None
    project.last_error = None
    if chapter.retry_count >= project.max_retries:
        chapter.status = ChapterStatus.FAILED.value
        project.status = ProjectStatus.PAUSED.value
        project.last_error = MAX_RETRIES_PAUSED_MESSAGE
        return None

    chapter.status = ChapterStatus.QUEUED.value
    return build_generate_chapter_job(
        project_id=project.id,
        chapter_number=chapter.chapter_number,
        payload={'retry': True},
    )


def apply_job_failure(
    *,
    project: Project | None,
    chapter: Chapter | None,
    attempt: ChapterAttempt | None,
    error_message: str,
) -> None:
    if project is not None:
        project.status = ProjectStatus.PAUSED.value
        project.last_error = error_message

    if chapter is not None and chapter.status not in {ChapterStatus.PASSED.value, ChapterStatus.PAUSED.value}:
        chapter.status = ChapterStatus.FAILED.value
        chapter.last_error = error_message

    if attempt is not None and attempt.status == AttemptStatus.RUNNING.value:
        attempt.status = AttemptStatus.ERRORED.value
        attempt.finished_at = utcnow()


def pause_current_generation(
    *,
    project: Project,
    chapter: Chapter,
    attempt: ChapterAttempt | None,
    content: str,
    input_snapshot: dict[str, Any] | None,
    input_tokens: int | None,
    output_tokens: int,
) -> None:
    chapter.status = ChapterStatus.PAUSED.value
    chapter.last_error = MANUAL_STOP_CHAPTER_MESSAGE
    project.status = ProjectStatus.PAUSED.value
    project.last_error = MANUAL_STOP_PROJECT_MESSAGE

    if attempt is None:
        return

    attempt.content = content or attempt.content
    attempt.status = AttemptStatus.ERRORED.value
    attempt.finished_at = utcnow()
    if input_snapshot is not None:
        attempt.input_snapshot = input_snapshot
    if input_tokens is not None:
        attempt.input_tokens = input_tokens
    attempt.output_tokens = output_tokens
