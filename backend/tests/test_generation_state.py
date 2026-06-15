import uuid
from types import SimpleNamespace

from backend.db.models import AttemptStatus, ChapterStatus, ProjectStatus
from backend.services.generation_state import (
    MAX_RETRIES_PAUSED_MESSAGE,
    QUEUED_CHAPTER_PAUSED_MESSAGE,
    accept_chapter,
    advance_after_chapter_success,
    apply_job_failure,
    apply_review_failure,
    build_generate_chapter_job,
    pause_queued_chapter,
)


def test_advance_after_chapter_success_enqueues_next_chapter_in_auto_mode():
    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.PAUSED.value, last_error='old', auto_mode=True)
    next_chapter = SimpleNamespace(chapter_number=3, status=ChapterStatus.PENDING.value, last_error='warning')

    result = advance_after_chapter_success(project=project, next_chapter=next_chapter)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error is None
    assert next_chapter.status == ChapterStatus.QUEUED.value
    assert next_chapter.last_error is None
    assert result.next_chapter is next_chapter
    assert result.queued_job is not None
    assert result.queued_job.project_id == project_id
    assert result.queued_job.chapter_number == 3
    assert result.queued_job.payload == {}


def test_apply_review_failure_pauses_project_at_retry_limit():
    project = SimpleNamespace(id=uuid.uuid4(), max_retries=2, status=ProjectStatus.RUNNING.value, last_error=None)
    chapter = SimpleNamespace(chapter_number=4, retry_count=1, status=ChapterStatus.REVIEWING.value, last_error='old')

    result = apply_review_failure(project=project, chapter=chapter)

    assert result is None
    assert chapter.retry_count == 2
    assert chapter.status == ChapterStatus.FAILED.value
    assert chapter.last_error is None
    assert project.status == ProjectStatus.PAUSED.value
    assert project.last_error == MAX_RETRIES_PAUSED_MESSAGE


def test_apply_job_failure_marks_attempt_errored_only_when_running():
    project = SimpleNamespace(status=ProjectStatus.RUNNING.value, last_error=None)
    chapter = SimpleNamespace(status=ChapterStatus.WRITING.value, last_error=None)
    attempt = SimpleNamespace(status=AttemptStatus.RUNNING.value, finished_at=None)

    apply_job_failure(project=project, chapter=chapter, attempt=attempt, error_message='boom')

    assert project.status == ProjectStatus.PAUSED.value
    assert project.last_error == 'boom'
    assert chapter.status == ChapterStatus.FAILED.value
    assert chapter.last_error == 'boom'
    assert attempt.status == AttemptStatus.ERRORED.value
    assert attempt.finished_at is not None


def test_accept_chapter_sets_final_state_and_attempt_link():
    attempt_id = uuid.uuid4()
    project = SimpleNamespace(current_chapter=0, last_error='old')
    chapter = SimpleNamespace(
        chapter_number=5,
        status=ChapterStatus.REVIEWING.value,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        improvement_notes=None,
        last_error='old',
    )
    attempt = SimpleNamespace(id=attempt_id, status=AttemptStatus.REVIEWED.value)

    accept_chapter(
        project=project,
        chapter=chapter,
        attempt=attempt,
        content='正文',
        score=8.6,
        improvement_notes='加强收尾',
    )

    assert attempt.status == AttemptStatus.ACCEPTED.value
    assert chapter.status == ChapterStatus.PASSED.value
    assert chapter.final_content == '正文'
    assert chapter.final_score == 8.6
    assert chapter.accepted_attempt_id == attempt_id
    assert chapter.improvement_notes == '加强收尾'
    assert chapter.last_error is None
    assert project.current_chapter == 5
    assert project.last_error is None


def test_pause_queued_chapter_uses_shared_message():
    chapter = SimpleNamespace(status=ChapterStatus.QUEUED.value, last_error=None)

    pause_queued_chapter(chapter)

    assert chapter.status == ChapterStatus.PAUSED.value
    assert chapter.last_error == QUEUED_CHAPTER_PAUSED_MESSAGE


def test_build_generate_chapter_job_uses_existing_enums():
    project_id = uuid.uuid4()

    job = build_generate_chapter_job(project_id=project_id, chapter_number=8, payload={'resume': True})

    assert job.project_id == project_id
    assert job.chapter_number == 8
    assert job.job_type == 'generate_chapter'
    assert job.status == 'queued'
    assert job.payload == {'resume': True}
