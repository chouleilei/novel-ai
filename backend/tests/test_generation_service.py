import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.db.models import AttemptStatus, ChapterStatus, JobStatus, JobType, ProjectStatus
from backend.services.generation_service import GenerationService
from backend.services.memory_service import MemoryService


class _ScalarListResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self._rows


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def one_or_none(self):
        return self._row


class _ExecuteResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


def _stub_lock_project(service: GenerationService, project):
    service._lock_project = AsyncMock(return_value=project)
    return service._lock_project


@pytest.mark.asyncio
async def test_start_project_restores_soft_warning_when_pending_revisions_exist():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._has_active_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.READY.value, last_error=None)
    lock_project = _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        chapter_number=1,
        status=ChapterStatus.PENDING.value,
        last_error="old warning",
    )
    service._get_first_runnable_chapter = AsyncMock(return_value=chapter)

    async def sync_warning(_: uuid.UUID) -> None:
        project.last_error = MemoryService.MEMORY_REVIEW_WARNING

    service.memory_service.sync_project_review_warning = AsyncMock(side_effect=sync_warning)

    job = await service.start_project(project)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error == MemoryService.MEMORY_REVIEW_WARNING
    assert chapter.status == ChapterStatus.QUEUED.value
    assert chapter.last_error is None
    assert job.chapter_number == 1
    assert job.job_type == JobType.GENERATE_CHAPTER.value
    assert job.status == JobStatus.QUEUED.value
    assert job.payload == {}
    assert session.add.call_args.args[0] is job
    lock_project.assert_awaited_once_with(project_id, current_project=project)
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id)
    service.events.append.assert_awaited_once_with(
        project_id,
        "pipeline_start",
        {"chapter_number": 1},
        chapter_number=1,
    )
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_project_remains_strict_when_any_active_job_exists():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._has_active_job = AsyncMock(return_value=True)

    project = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(ValueError, match="项目已有进行中的生成任务"):
        await service.start_project(project)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_chapter_resets_failed_state_and_enqueues_job():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.FAILED.value, last_error="worker failed")
    lock_project = _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        chapter_number=3,
        status=ChapterStatus.FAILED.value,
        last_error="chapter failed",
    )
    service._get_chapter = AsyncMock(return_value=chapter)

    job = await service.retry_chapter(project, 3)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error is None
    assert chapter.status == ChapterStatus.QUEUED.value
    assert chapter.last_error is None
    assert job.chapter_number == 3
    assert job.job_type == JobType.GENERATE_CHAPTER.value
    assert job.status == JobStatus.QUEUED.value
    assert job.payload == {"retry": True, "chapter_retry": True}
    assert session.add.call_args.args[0] is job
    lock_project.assert_awaited_once_with(project_id, current_project=project)
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id, chapter_number=3)
    service._cleanup_paused_queued_generation_state.assert_not_awaited()
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)
    service.events.append.assert_awaited_once_with(
        project_id,
        "chapter_rewriting",
        {"queued": True, "reason": "retry_current_chapter"},
        chapter_number=3,
    )
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_chapter_restores_soft_warning_when_pending_revisions_exist():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.PAUSED.value, last_error="旧错误")
    chapter = SimpleNamespace(
        chapter_number=3,
        status=ChapterStatus.PAUSED.value,
        last_error="暂停中",
    )
    service._get_chapter = AsyncMock(return_value=chapter)

    async def sync_warning(_: uuid.UUID) -> None:
        project.last_error = MemoryService.MEMORY_REVIEW_WARNING

    service.memory_service.sync_project_review_warning = AsyncMock(side_effect=sync_warning)

    await service.retry_chapter(project, 3)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error == MemoryService.MEMORY_REVIEW_WARNING
    assert chapter.status == ChapterStatus.QUEUED.value
    assert chapter.last_error is None
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id, chapter_number=3)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id, chapter_number=3)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_chapter_rejects_passed_chapter():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.COMPLETED.value, last_error=None)
    chapter = SimpleNamespace(
        chapter_number=2,
        status=ChapterStatus.PASSED.value,
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        auto_accepted=False,
        improvement_notes=None,
        retry_count=0,
    )
    service._get_chapter = AsyncMock(return_value=chapter)

    job = await service.retry_chapter(project, 2)

    assert job is not None
    assert job.chapter_number == 2


@pytest.mark.asyncio
async def test_retry_chapter_allows_passed_chapter_and_clears_current_chapter_output_only():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.COMPLETED.value, last_error='done')
    lock_project = _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        chapter_number=2,
        status=ChapterStatus.PASSED.value,
        final_content='旧正文',
        final_score=8.4,
        accepted_attempt_id=uuid.uuid4(),
        auto_accepted=True,
        last_error='old',
    )
    service._get_chapter = AsyncMock(return_value=chapter)

    job = await service.retry_chapter(project, 2)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error is None
    assert chapter.status == ChapterStatus.QUEUED.value
    assert chapter.final_content is None
    assert chapter.final_score is None
    assert chapter.accepted_attempt_id is None
    assert chapter.auto_accepted is False
    assert chapter.last_error is None
    assert job.payload == {'retry': True, 'chapter_retry': True}
    lock_project.assert_awaited_once_with(project_id, current_project=project)


@pytest.mark.asyncio
async def test_retry_chapter_cleans_paused_queued_remnants_before_requeue():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.PAUSED.value, last_error='paused')
    chapter = SimpleNamespace(chapter_number=4, status=ChapterStatus.PAUSED.value, last_error='queued remnant')
    service._get_chapter = AsyncMock(return_value=chapter)

    await service.retry_chapter(project, 4)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id, chapter_number=4)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id, chapter_number=4)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)


@pytest.mark.asyncio
async def test_retry_chapter_blocks_when_project_has_any_queued_job():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=True)
    service._has_live_leased_job = AsyncMock()

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.FAILED.value)

    with pytest.raises(ValueError, match='项目已有进行中的章节级生成任务，请等待当前任务完成后再重试'):
        await service.retry_chapter(project, 5)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id, chapter_number=5)
    service._cleanup_paused_queued_generation_state.assert_not_awaited()
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_chapter_blocks_when_project_has_live_leased_job():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=True)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.FAILED.value)

    with pytest.raises(ValueError, match='项目已有进行中的章节级生成任务，请等待当前任务完成后再重试'):
        await service.retry_chapter(project, 5)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id, chapter_number=5)
    service._cleanup_paused_queued_generation_state.assert_not_awaited()
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_awaited_once_with(project.id)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_chapter_cleans_same_chapter_active_job_for_paused_terminal_chapter():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock(), execute=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.PAUSED.value, last_error='paused')
    chapter = SimpleNamespace(chapter_number=2, status=ChapterStatus.FAILED.value, last_error='failed')
    active_job = SimpleNamespace(
        status=JobStatus.LEASED.value,
        lease_owner='worker-1',
        lease_expires_at=object(),
        last_error=None,
    )

    service._get_chapter = AsyncMock(return_value=chapter)
    service._get_active_job = AsyncMock(return_value=active_job)

    await service.retry_chapter(project, 2)

    assert active_job.status == JobStatus.CANCELLED.value
    assert active_job.lease_owner is None
    assert active_job.lease_expires_at is None
    assert active_job.last_error == '章节已进入终态，已清理残留生成任务。'
    assert chapter.status == ChapterStatus.QUEUED.value
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id, chapter_number=2)
    assert any(call.args == (project_id, 2) for call in service._get_active_job.await_args_list)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)


@pytest.mark.asyncio
async def test_list_active_job_chapter_numbers_returns_distinct_active_chapters():
    session = SimpleNamespace(execute=AsyncMock(return_value=_ScalarListResult([3, 3, 5])))
    service = GenerationService(session)  # type: ignore[arg-type]

    active_chapters = await service.list_active_job_chapter_numbers(uuid.uuid4())

    assert active_chapters == {3, 5}
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_project_reuses_existing_active_job_when_enqueue_detects_race():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock(), execute=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._has_active_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.READY.value, last_error=None)
    chapter = SimpleNamespace(
        chapter_number=1,
        status=ChapterStatus.PENDING.value,
        last_error=None,
    )
    existing_job = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        chapter_number=1,
        job_type=JobType.GENERATE_CHAPTER.value,
        status=JobStatus.QUEUED.value,
        payload={"resume": True},
        created_at=0,
    )
    service._get_first_runnable_chapter = AsyncMock(return_value=chapter)
    session.execute.return_value = _ScalarResult(existing_job)

    job = await service.start_project(project)

    assert job is existing_job
    assert chapter.status == ChapterStatus.QUEUED.value
    session.add.assert_not_called()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_enqueue_generate_job_returns_existing_job_after_insert_conflict():
    project_id = uuid.uuid4()
    existing_job = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        chapter_number=2,
        job_type=JobType.GENERATE_CHAPTER.value,
        status=JobStatus.QUEUED.value,
        payload={"retry": True},
        created_at=0,
    )
    session = SimpleNamespace(add=Mock(), flush=AsyncMock(), execute=AsyncMock(side_effect=[_ScalarResult(None), _ExecuteResult(0), _ScalarResult(existing_job)]))
    service = GenerationService(session)  # type: ignore[arg-type]

    job = await service._enqueue_generate_job(project_id, 2, payload={"retry": True})

    assert job is existing_job
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_lock_project_returns_current_project_when_session_lacks_scalar():
    project = SimpleNamespace(id=uuid.uuid4())
    session = SimpleNamespace()
    service = GenerationService(session)  # type: ignore[arg-type]

    locked = await service._lock_project(project.id, current_project=project)

    assert locked is project


@pytest.mark.asyncio
async def test_lock_project_selects_project_for_update_when_session_supports_scalar():
    project = SimpleNamespace(id=uuid.uuid4())
    session = SimpleNamespace(scalar=AsyncMock(return_value=project))
    service = GenerationService(session)  # type: ignore[arg-type]

    locked = await service._lock_project(project.id)

    assert locked is project
    session.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_project_raises_when_project_missing_under_lock():
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    service = GenerationService(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="项目不存在"):
        await service._lock_project(uuid.uuid4())


@pytest.mark.asyncio
async def test_pause_project_rejects_non_running_project():
    session = SimpleNamespace(flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service.events.append = AsyncMock()
    service._cancel_queued_jobs = AsyncMock()
    service._pause_queued_chapters = AsyncMock()

    project = SimpleNamespace(
        id=uuid.uuid4(),
        status=ProjectStatus.COMPLETED.value,
        last_error=None,
        current_chapter=3,
    )
    lock_project = _stub_lock_project(service, project)

    with pytest.raises(ValueError, match="仅运行中的项目支持暂停"):
        await service.pause_project(project, "manual_pause")

    lock_project.assert_awaited_once_with(project.id, current_project=project)
    assert project.status == ProjectStatus.COMPLETED.value
    service._cancel_queued_jobs.assert_not_awaited()
    service._pause_queued_chapters.assert_not_awaited()
    service.events.append.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_project_can_requeue_stale_writing_chapter_when_no_active_job():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service._get_stale_inflight_chapter = AsyncMock()
    service._get_first_runnable_chapter = AsyncMock(return_value=None)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.PAUSED.value, last_error="paused")
    lock_project = _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        chapter_number=1,
        status=ChapterStatus.WRITING.value,
        last_error="still writing",
    )
    service._get_stale_inflight_chapter.return_value = chapter
    service._build_resume_payload = AsyncMock(return_value={"resume": True})

    job = await service.resume_project(project)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error is None
    assert chapter.status == ChapterStatus.QUEUED.value
    assert chapter.last_error is None
    assert job.chapter_number == 1
    assert job.job_type == JobType.GENERATE_CHAPTER.value
    assert job.status == JobStatus.QUEUED.value
    assert job.payload == {"resume": True}
    assert session.add.call_args.args[0] is job
    lock_project.assert_awaited_once_with(project_id, current_project=project)
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id)
    service._build_resume_payload.assert_awaited_once_with(chapter)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)
    service.events.append.assert_awaited_once_with(
        project_id,
        "pipeline_start",
        {"chapter_number": 1, "resume": True},
        chapter_number=1,
    )
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_resume_payload_includes_continuation_fields_when_latest_attempt_has_content():
    session = SimpleNamespace(execute=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    chapter_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    chapter = SimpleNamespace(id=chapter_id)
    latest_attempt = SimpleNamespace(
        id=attempt_id,
        attempt_no=3,
        content='已有草稿内容',
        status=AttemptStatus.ERRORED.value,
    )
    service._get_latest_attempt = AsyncMock(return_value=latest_attempt)
    service._get_review_for_attempt = AsyncMock(return_value=None)

    payload = await service._build_resume_payload(chapter)

    assert payload == {
        'resume': True,
        'continue_from_attempt_id': str(attempt_id),
        'continue_from_attempt_no': 3,
        'continue_from_content_chars': len('已有草稿内容'),
    }
    service._get_latest_attempt.assert_awaited_once_with(chapter_id)
    service._get_review_for_attempt.assert_awaited_once_with(attempt_id)


@pytest.mark.asyncio
async def test_build_resume_payload_omits_continuation_fields_when_latest_attempt_has_no_content():
    session = SimpleNamespace(execute=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    chapter = SimpleNamespace(id=uuid.uuid4())
    latest_attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=2,
        content='   ',
        status=AttemptStatus.ERRORED.value,
    )
    service._get_latest_attempt = AsyncMock(return_value=latest_attempt)
    service._get_review_for_attempt = AsyncMock(return_value=None)

    payload = await service._build_resume_payload(chapter)

    assert payload == {'resume': True}


@pytest.mark.asyncio
async def test_build_resume_payload_omits_continuation_fields_when_latest_attempt_already_reviewed():
    session = SimpleNamespace(execute=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    chapter = SimpleNamespace(id=uuid.uuid4())
    latest_attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=2,
        content='已有终稿草稿',
        status=AttemptStatus.REVIEWED.value,
    )
    service._get_latest_attempt = AsyncMock(return_value=latest_attempt)
    service._get_review_for_attempt = AsyncMock(return_value=SimpleNamespace(attempt_id=latest_attempt.id))

    payload = await service._build_resume_payload(chapter)

    assert payload == {'resume': True}


@pytest.mark.asyncio
async def test_resume_project_restores_soft_warning_when_pending_revisions_exist():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service._get_stale_inflight_chapter = AsyncMock(return_value=None)
    service.events.append = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.PAUSED.value, last_error="旧错误")
    chapter = SimpleNamespace(
        chapter_number=2,
        status=ChapterStatus.PAUSED.value,
        last_error="暂停中",
    )
    service._get_first_runnable_chapter = AsyncMock(return_value=chapter)

    async def sync_warning(_: uuid.UUID) -> None:
        project.last_error = MemoryService.MEMORY_REVIEW_WARNING

    service.memory_service.sync_project_review_warning = AsyncMock(side_effect=sync_warning)

    await service.resume_project(project)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error == MemoryService.MEMORY_REVIEW_WARNING
    assert chapter.status == ChapterStatus.QUEUED.value
    assert chapter.last_error is None
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_project_cleans_paused_queued_remnants_before_resuming():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service._get_stale_inflight_chapter = AsyncMock(return_value=None)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.PAUSED.value, last_error='paused')
    chapter = SimpleNamespace(chapter_number=6, status=ChapterStatus.PAUSED.value, last_error='queued remnant')
    service._get_first_runnable_chapter = AsyncMock(return_value=chapter)

    await service.resume_project(project)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)


@pytest.mark.asyncio
async def test_resume_project_blocks_when_project_has_queued_job_outside_paused_recovery():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=True)
    service._has_live_leased_job = AsyncMock()

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.READY.value)

    with pytest.raises(ValueError, match='项目仍有排队中的生成任务，请稍后再继续'):
        await service.resume_project(project)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._cleanup_paused_queued_generation_state.assert_not_awaited()
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_project_blocks_only_on_live_leased_job():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=True)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.PAUSED.value)

    with pytest.raises(ValueError, match='项目仍有正在收尾的生成任务，请稍后再继续'):
        await service.resume_project(project)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project.id)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_awaited_once_with(project.id)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_project_cleans_same_chapter_active_job_for_paused_terminal_chapter():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock(), execute=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.PAUSED.value, last_error='paused')
    chapter = SimpleNamespace(chapter_number=2, status=ChapterStatus.PAUSED.value, last_error='manual stop')
    active_job = SimpleNamespace(
        status=JobStatus.LEASED.value,
        lease_owner='worker-1',
        lease_expires_at=object(),
        last_error=None,
    )

    service._get_stale_inflight_chapter = AsyncMock(return_value=chapter)
    service._get_first_runnable_chapter = AsyncMock(return_value=None)
    service._get_active_job = AsyncMock(return_value=active_job)

    await service.resume_project(project)

    assert active_job.status == JobStatus.CANCELLED.value
    assert active_job.lease_owner is None
    assert active_job.lease_expires_at is None
    assert active_job.last_error == '章节已进入终态，已清理残留生成任务。'
    assert chapter.status == ChapterStatus.QUEUED.value
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id)
    assert any(call.args == (project_id, 2) for call in service._get_active_job.await_args_list)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)


@pytest.mark.asyncio
async def test_resume_project_cleans_active_job_for_first_runnable_terminal_chapter():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock(), execute=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.PAUSED.value, last_error='paused')
    chapter = SimpleNamespace(chapter_number=2, status=ChapterStatus.FAILED.value, last_error='failed')
    active_job = SimpleNamespace(
        status=JobStatus.LEASED.value,
        lease_owner='worker-1',
        lease_expires_at=object(),
        last_error=None,
    )

    service._get_stale_inflight_chapter = AsyncMock(side_effect=[None, None])
    service._get_first_runnable_chapter = AsyncMock(return_value=chapter)
    service._get_active_job = AsyncMock(return_value=active_job)

    await service.resume_project(project)

    assert active_job.status == JobStatus.CANCELLED.value
    assert active_job.lease_owner is None
    assert active_job.lease_expires_at is None
    assert active_job.last_error == '章节已进入终态，已清理残留生成任务。'
    assert chapter.status == ChapterStatus.QUEUED.value
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id)
    assert any(call.args == (project_id, 2) for call in service._get_active_job.await_args_list)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)


@pytest.mark.asyncio
async def test_manual_approve_chapter_accepts_latest_draft_and_marks_manual_event():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service._get_next_runnable_chapter = AsyncMock(return_value=None)
    service.events.append = AsyncMock()
    service.memory_service.save_summary_and_revisions = AsyncMock(
        return_value={
            "character_revision_count": 1,
            "world_revision_count": 2,
            "applied_revision_count": 3,
            "needs_review_count": 0,
        }
    )
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        status=ProjectStatus.PAUSED.value,
        last_error="章节达到最大重试次数，项目已暂停。",
        current_chapter=1,
        auto_mode=True,
    )
    lock_project = _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status=ChapterStatus.FAILED.value,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        last_error="failed",
    )
    attempt = SimpleNamespace(
        id=attempt_id,
        content="可用草稿",
        status=AttemptStatus.REJECTED.value,
    )
    review = SimpleNamespace(overall_score=72.5)

    service._get_chapter = AsyncMock(return_value=chapter)
    service._get_latest_approvable_attempt = AsyncMock(return_value=attempt)
    service._get_review_for_attempt = AsyncMock(return_value=review)

    result = await service.manual_approve_chapter(project, 2)

    assert result is chapter
    assert chapter.status == ChapterStatus.PASSED.value
    assert chapter.final_content == "可用草稿"
    assert chapter.final_score == 72.5
    assert chapter.accepted_attempt_id == attempt_id
    assert chapter.last_error is None
    assert attempt.status == AttemptStatus.ACCEPTED.value
    assert project.current_chapter == 2
    assert project.status == ProjectStatus.COMPLETED.value
    assert project.last_error is None
    lock_project.assert_awaited_once_with(project_id, current_project=project)
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)
    service.memory_service.save_summary_and_revisions.assert_awaited_once_with(project_id, 2, "可用草稿")
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    assert service.events.append.await_args_list[0].args == (
        project_id,
        "memory_updated",
        {
            "chapter_number": 2,
            "character_revision_count": 1,
            "world_revision_count": 2,
            "applied_revision_count": 3,
            "needs_review_count": 0,
        },
        2,
    )
    assert service.events.append.await_args_list[1].args == (
        project_id,
        "chapter_passed",
        {"score": 72.5, "manual": True},
        2,
    )
    assert service.events.append.await_args_list[2].args == (
        project_id,
        "pipeline_complete",
        {"project_id": str(project_id)},
    )
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_approve_chapter_rejects_missing_draft():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.PAUSED.value)
    chapter = SimpleNamespace(id=uuid.uuid4(), chapter_number=2, status=ChapterStatus.FAILED.value)

    service._get_chapter = AsyncMock(return_value=chapter)
    service._get_latest_approvable_attempt = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="当前章节没有可人工通过的草稿"):
        await service.manual_approve_chapter(project, 2)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project.id)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_awaited_once_with(project.id)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_approve_chapter_accepts_latest_contentful_attempt_when_newer_attempts_are_empty():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service._get_next_runnable_chapter = AsyncMock(return_value=None)
    service.events.append = AsyncMock()
    service.memory_service.save_summary_and_revisions = AsyncMock(
        return_value={
            'character_revision_count': 0,
            'world_revision_count': 0,
            'applied_revision_count': 0,
            'needs_review_count': 0,
        }
    )
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    accepted_attempt_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        status=ProjectStatus.PAUSED.value,
        last_error='章节达到最大重试次数，项目已暂停。',
        current_chapter=55,
        auto_mode=True,
    )
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=56,
        status=ChapterStatus.FAILED.value,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        last_error='failed',
    )
    fallback_attempt = SimpleNamespace(
        id=accepted_attempt_id,
        content='保留草稿正文',
        status=AttemptStatus.REJECTED.value,
    )
    review = SimpleNamespace(overall_score=81.2)

    service._get_chapter = AsyncMock(return_value=chapter)
    service._get_latest_approvable_attempt = AsyncMock(return_value=fallback_attempt)
    service._get_review_for_attempt = AsyncMock(return_value=review)

    result = await service.manual_approve_chapter(project, 56)

    assert result is chapter
    assert chapter.status == ChapterStatus.PASSED.value
    assert chapter.final_content == '保留草稿正文'
    assert chapter.final_score == 81.2
    assert chapter.accepted_attempt_id == accepted_attempt_id
    assert fallback_attempt.status == AttemptStatus.ACCEPTED.value
    assert project.current_chapter == 56
    assert project.status == ProjectStatus.COMPLETED.value
    service._get_latest_approvable_attempt.assert_awaited_once_with(chapter.id)
    service._get_review_for_attempt.assert_awaited_once_with(accepted_attempt_id)
    service.memory_service.save_summary_and_revisions.assert_awaited_once_with(project_id, 56, '保留草稿正文')
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_approve_chapter_rejects_non_failed_chapter():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.PAUSED.value)
    chapter = SimpleNamespace(id=uuid.uuid4(), chapter_number=2, status=ChapterStatus.PASSED.value)
    service._get_chapter = AsyncMock(return_value=chapter)

    with pytest.raises(ValueError, match="仅失败中的章节支持人工通过"):
        await service.manual_approve_chapter(project, 2)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project.id)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_awaited_once_with(project.id)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_approve_chapter_pauses_when_auto_mode_disabled():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.save_summary_and_revisions = AsyncMock(
        return_value={
            "character_revision_count": 0,
            "world_revision_count": 0,
            "applied_revision_count": 0,
            "needs_review_count": 0,
        }
    )
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        status=ProjectStatus.PAUSED.value,
        last_error="章节达到最大重试次数，项目已暂停。",
        current_chapter=1,
        auto_mode=False,
    )
    _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status=ChapterStatus.FAILED.value,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        last_error="failed",
    )
    next_chapter = SimpleNamespace(chapter_number=3, status=ChapterStatus.PENDING.value, last_error="old")
    attempt = SimpleNamespace(id=uuid.uuid4(), content="当前草稿", status=AttemptStatus.REJECTED.value)

    service._get_chapter = AsyncMock(return_value=chapter)
    service._get_latest_approvable_attempt = AsyncMock(return_value=attempt)
    service._get_review_for_attempt = AsyncMock(return_value=None)
    service._get_next_runnable_chapter = AsyncMock(return_value=next_chapter)

    await service.manual_approve_chapter(project, 2)

    assert project.status == ProjectStatus.PAUSED.value
    assert project.last_error is None
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)
    assert service.events.append.await_args_list[-1].args == (
        project_id,
        "project_paused",
        {"reason": "waiting_manual_resume", "next_chapter": 3},
        2,
    )
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    session.add.assert_not_called()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_approve_chapter_enqueues_next_chapter_when_auto_mode_enabled():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.save_summary_and_revisions = AsyncMock(
        return_value={
            "character_revision_count": 0,
            "world_revision_count": 0,
            "applied_revision_count": 0,
            "needs_review_count": 0,
        }
    )
    service.memory_service.sync_project_review_warning = AsyncMock()

    project = SimpleNamespace(
        id=uuid.uuid4(),
        status=ProjectStatus.PAUSED.value,
        last_error="章节达到最大重试次数，项目已暂停。",
        current_chapter=1,
        auto_mode=True,
    )
    _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status=ChapterStatus.FAILED.value,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        last_error="failed",
    )
    next_chapter = SimpleNamespace(chapter_number=3, status=ChapterStatus.PENDING.value, last_error="old")
    attempt = SimpleNamespace(id=uuid.uuid4(), content="当前草稿", status=AttemptStatus.REJECTED.value)

    service._get_chapter = AsyncMock(return_value=chapter)
    service._get_latest_approvable_attempt = AsyncMock(return_value=attempt)
    service._get_review_for_attempt = AsyncMock(return_value=None)
    service._get_next_runnable_chapter = AsyncMock(return_value=next_chapter)

    await service.manual_approve_chapter(project, 2)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error is None
    assert next_chapter.status == ChapterStatus.QUEUED.value
    assert next_chapter.last_error is None
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project.id)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_awaited_once_with(project.id)
    queued_job = session.add.call_args.args[0]
    assert queued_job.chapter_number == 3
    assert queued_job.job_type == JobType.GENERATE_CHAPTER.value
    assert queued_job.status == JobStatus.QUEUED.value
    assert queued_job.payload == {}
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project.id)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_approve_chapter_keeps_running_when_memory_revisions_are_auto_applied():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.save_summary_and_revisions = AsyncMock(
        return_value={
            "character_revision_count": 1,
            "world_revision_count": 1,
            "applied_revision_count": 2,
            "needs_review_count": 0,
        }
    )

    project_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        status=ProjectStatus.PAUSED.value,
        last_error="章节达到最大重试次数，项目已暂停。",
        current_chapter=1,
        auto_mode=True,
    )
    _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status=ChapterStatus.FAILED.value,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        last_error="failed",
    )
    next_chapter = SimpleNamespace(chapter_number=3, status=ChapterStatus.PENDING.value, last_error="old")
    attempt = SimpleNamespace(id=uuid.uuid4(), content="当前草稿", status=AttemptStatus.REJECTED.value)

    service._get_chapter = AsyncMock(return_value=chapter)
    service._get_latest_approvable_attempt = AsyncMock(return_value=attempt)
    service._get_review_for_attempt = AsyncMock(return_value=None)
    service._get_next_runnable_chapter = AsyncMock(return_value=next_chapter)

    async def sync_warning(_: uuid.UUID) -> None:
        project.last_error = None

    service.memory_service.sync_project_review_warning = AsyncMock(side_effect=sync_warning)

    await service.manual_approve_chapter(project, 2)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error is None
    assert next_chapter.status == ChapterStatus.QUEUED.value
    assert next_chapter.last_error is None
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)
    queued_job = session.add.call_args.args[0]
    assert queued_job.chapter_number == 3
    assert queued_job.job_type == JobType.GENERATE_CHAPTER.value
    assert queued_job.status == JobStatus.QUEUED.value
    assert queued_job.payload == {}
    assert all(call.args[1] != "project_paused" for call in service.events.append.await_args_list)
    memory_updated = service.events.append.await_args_list[0]
    assert memory_updated.args[2]["applied_revision_count"] == 2
    assert memory_updated.args[2]["needs_review_count"] == 0
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_approve_chapter_cleans_paused_queued_remnants_before_project_scoped_leased_check():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=True)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.PAUSED.value)

    with pytest.raises(ValueError, match="项目仍有正在收尾的生成任务，请等待当前任务完成后再人工通过"):
        await service.manual_approve_chapter(project, 2)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project.id)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_awaited_once_with(project.id)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_approve_chapter_blocks_when_project_has_queued_job_outside_paused_recovery():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=True)
    service._has_live_leased_job = AsyncMock()

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.FAILED.value)

    with pytest.raises(ValueError, match='项目仍有排队中的生成任务，请稍后再人工通过'):
        await service.manual_approve_chapter(project, 2)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._cleanup_paused_queued_generation_state.assert_not_awaited()
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_expired_leased_jobs_returns_updated_rowcount():
    class Result:
        def __init__(self, rowcount: int) -> None:
            self.rowcount = rowcount

    session = SimpleNamespace(execute=AsyncMock(return_value=Result(2)))
    service = GenerationService(session)  # type: ignore[arg-type]

    updated = await service._cancel_expired_leased_jobs(uuid.uuid4(), chapter_number=7)

    assert updated == 2
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_first_runnable_chapter_repairs_orphan_queued_before_selecting() -> None:
    project_id = uuid.uuid4()
    chapter = SimpleNamespace(
        chapter_number=2,
        status=ChapterStatus.QUEUED.value,
        last_error=None,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarListResult([chapter]),
                _ScalarResult(chapter),
            ]
        )
    )
    service = GenerationService(session)  # type: ignore[arg-type]
    service._has_active_job = AsyncMock(return_value=False)

    result = await service._get_first_runnable_chapter(project_id)

    assert result is chapter
    assert chapter.status == ChapterStatus.PAUSED.value
    assert chapter.last_error == '已在执行前停止，可点击继续后重跑本章。'
    service._has_active_job.assert_awaited_once_with(project_id, 2)
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_get_next_runnable_chapter_repairs_orphan_queued_before_selecting() -> None:
    project_id = uuid.uuid4()
    chapter = SimpleNamespace(
        chapter_number=3,
        status=ChapterStatus.QUEUED.value,
        last_error=None,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarListResult([chapter]),
                _ScalarResult(chapter),
            ]
        )
    )
    service = GenerationService(session)  # type: ignore[arg-type]
    service._has_active_job = AsyncMock(return_value=False)

    result = await service._get_next_runnable_chapter(project_id, 2)

    assert result is chapter
    assert chapter.status == ChapterStatus.PAUSED.value
    assert chapter.last_error == '已在执行前停止，可点击继续后重跑本章。'
    service._has_active_job.assert_awaited_once_with(project_id, 3)
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_rewrite_from_chapter_resets_project_state_and_requeues_target_chapter():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service._reset_generation_from_chapter = AsyncMock()
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        status=ProjectStatus.PAUSED.value,
        current_chapter=8,
        last_error='old error',
        lease_owner='worker-1',
        lease_expires_at=object(),
        distant_memory_cache='cached',
        distant_memory_updated_chapter=8,
    )
    lock_project = _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        chapter_number=5,
        status=ChapterStatus.PASSED.value,
        last_error='old chapter error',
    )
    service._get_chapter = AsyncMock(return_value=chapter)

    job = await service.rewrite_from_chapter(project, 5)

    assert project.current_chapter == 4
    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error is None
    assert project.lease_owner is None
    assert project.lease_expires_at is None
    assert project.distant_memory_cache is None
    assert project.distant_memory_updated_chapter == 4
    assert chapter.status == ChapterStatus.QUEUED.value
    assert chapter.last_error is None
    assert job.chapter_number == 5
    assert job.job_type == JobType.GENERATE_CHAPTER.value
    assert job.status == JobStatus.QUEUED.value
    assert job.payload == {}
    assert session.add.call_args.args[0] is job
    lock_project.assert_awaited_once_with(project_id, current_project=project)
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project_id)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)
    service._reset_generation_from_chapter.assert_awaited_once_with(project_id, 5)
    assert service.events.append.await_args_list[0].args == (
        project_id,
        'chapter_rewriting',
        {'queued': True, 'reason': 'rewrite_from_here'},
    )
    assert service.events.append.await_args_list[0].kwargs == {'chapter_number': 5}
    assert service.events.append.await_args_list[1].args == (
        project_id,
        'pipeline_start',
        {'chapter_number': 5},
    )
    assert service.events.append.await_args_list[1].kwargs == {'chapter_number': 5}
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_rewrite_from_chapter_blocks_when_project_has_queued_jobs():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=True)
    service._has_live_leased_job = AsyncMock()

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.PAUSED.value)

    with pytest.raises(ValueError, match='项目仍有排队中的生成任务，请稍后再重写'):
        await service.rewrite_from_chapter(project, 3)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project.id)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_rewrite_from_chapter_blocks_when_project_has_live_leased_jobs():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=True)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.READY.value)

    with pytest.raises(ValueError, match='项目仍有正在收尾的生成任务，请等待当前任务完成后再重写'):
        await service.rewrite_from_chapter(project, 3)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_awaited_once_with(project.id)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_rewrite_from_chapter_rejects_pending_chapter():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.READY.value)
    chapter = SimpleNamespace(chapter_number=3, status=ChapterStatus.PENDING.value)
    service._get_chapter = AsyncMock(return_value=chapter)

    with pytest.raises(ValueError, match='待处理章节不能作为重写起点'):
        await service.rewrite_from_chapter(project, 3)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_awaited_once_with(project.id)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_continue_chapter_enqueues_breakpoint_continuation_without_resetting_later_chapters():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)
    service.events.append = AsyncMock()
    service.memory_service.sync_project_review_warning = AsyncMock()

    project_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.COMPLETED.value, last_error='done')
    lock_project = _stub_lock_project(service, project)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=3,
        status=ChapterStatus.PASSED.value,
        final_content='原终稿',
        final_score=8.6,
        accepted_attempt_id=uuid.uuid4(),
        auto_accepted=True,
        last_error='old',
    )
    latest_attempt = SimpleNamespace(
        id=attempt_id,
        attempt_no=2,
        content='已保存草稿内容',
    )
    service._get_chapter = AsyncMock(return_value=chapter)
    service._get_latest_approvable_attempt = AsyncMock(return_value=latest_attempt)

    job = await service.continue_chapter(project, 3)

    assert project.status == ProjectStatus.RUNNING.value
    assert project.last_error is None
    assert chapter.status == ChapterStatus.QUEUED.value
    assert chapter.final_content is None
    assert chapter.final_score is None
    assert chapter.accepted_attempt_id is None
    assert chapter.auto_accepted is False
    assert chapter.last_error is None
    assert job.chapter_number == 3
    assert job.payload == {
        'resume': True,
        'continue_from_attempt_id': str(attempt_id),
        'continue_from_attempt_no': 2,
        'continue_from_content_chars': len('已保存草稿内容'),
        'chapter_continue': True,
    }
    lock_project.assert_awaited_once_with(project_id, current_project=project)
    service._cancel_expired_leased_jobs.assert_awaited_once_with(project_id, chapter_number=3)
    service._has_queued_job.assert_awaited_once_with(project_id)
    service._has_live_leased_job.assert_awaited_once_with(project_id)
    service._get_latest_approvable_attempt.assert_awaited_once_with(chapter.id)
    assert service.events.append.await_args_list[0].args == (
        project_id,
        'chapter_rewriting',
        {'queued': True, 'reason': 'continue_from_breakpoint'},
    )
    assert service.events.append.await_args_list[0].kwargs == {'chapter_number': 3}
    assert service.events.append.await_args_list[1].args == (
        project_id,
        'pipeline_start',
        {'chapter_number': 3, 'resume': True},
    )
    assert service.events.append.await_args_list[1].kwargs == {'chapter_number': 3}
    service.memory_service.sync_project_review_warning.assert_awaited_once_with(project_id)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_continue_chapter_rejects_when_no_saved_draft_exists():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.PAUSED.value)
    chapter = SimpleNamespace(id=uuid.uuid4(), chapter_number=2, status=ChapterStatus.PAUSED.value)
    service._get_chapter = AsyncMock(return_value=chapter)
    service._get_latest_approvable_attempt = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match='当前章节没有可续写的草稿'):
        await service.continue_chapter(project, 2)


@pytest.mark.asyncio
async def test_continue_chapter_blocks_when_project_has_any_queued_job():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=True)
    service._has_live_leased_job = AsyncMock()

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.PAUSED.value)

    with pytest.raises(ValueError, match='项目已有进行中的章节级生成任务，请等待当前任务完成后再续写'):
        await service.continue_chapter(project, 2)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id, chapter_number=2)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project.id, chapter_number=2)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_continue_chapter_blocks_when_project_has_live_leased_job():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._cleanup_paused_queued_generation_state = AsyncMock()
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=True)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.PAUSED.value)

    with pytest.raises(ValueError, match='项目已有进行中的章节级生成任务，请等待当前任务完成后再续写'):
        await service.continue_chapter(project, 2)

    service._cancel_expired_leased_jobs.assert_awaited_once_with(project.id, chapter_number=2)
    service._cleanup_paused_queued_generation_state.assert_awaited_once_with(project.id, chapter_number=2)
    service._has_queued_job.assert_awaited_once_with(project.id)
    service._has_live_leased_job.assert_awaited_once_with(project.id)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_continue_chapter_rejects_pending_chapter():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service._cancel_expired_leased_jobs = AsyncMock(return_value=0)
    service._has_queued_job = AsyncMock(return_value=False)
    service._has_live_leased_job = AsyncMock(return_value=False)

    project = SimpleNamespace(id=uuid.uuid4(), status=ProjectStatus.READY.value)
    chapter = SimpleNamespace(id=uuid.uuid4(), chapter_number=1, status=ChapterStatus.PENDING.value)
    service._get_chapter = AsyncMock(return_value=chapter)

    with pytest.raises(ValueError, match='仅暂停、失败或已完成的章节支持断点续写'):
        await service.continue_chapter(project, 1)


@pytest.mark.asyncio
async def test_reset_generation_from_chapter_deletes_project_level_terminal_events():
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock())
    service = GenerationService(session)  # type: ignore[arg-type]
    service.memory_service.reset_resources_from_chapter = AsyncMock()

    session.execute.side_effect = [
        _ScalarListResult([]),
        _ExecuteResult(0),
        _ExecuteResult(0),
        _ExecuteResult(0),
        _ExecuteResult(0),
        _ExecuteResult(0),
        _ExecuteResult(0),
    ]

    await service._reset_generation_from_chapter(uuid.uuid4(), 4)

    project_event_delete_stmt = session.execute.await_args_list[2].args[0]
    project_level_delete_stmt = session.execute.await_args_list[3].args[0]
    assert 'project_events' in str(project_event_delete_stmt)
    assert 'chapter_number >=' in str(project_event_delete_stmt)
    compiled_params = project_level_delete_stmt.compile().params
    event_types = compiled_params['event_type_1']
    assert 'pipeline_complete' in event_types
    assert 'project_paused' in event_types
    service.memory_service.reset_resources_from_chapter.assert_awaited_once()


@pytest.mark.asyncio
async def test_repair_orphan_queued_chapters_keeps_chapter_queued_when_job_is_active() -> None:
    project_id = uuid.uuid4()
    chapter = SimpleNamespace(
        chapter_number=4,
        status=ChapterStatus.QUEUED.value,
        last_error=None,
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=_ScalarListResult([chapter])))
    service = GenerationService(session)  # type: ignore[arg-type]
    service._has_active_job = AsyncMock(return_value=True)

    repaired = await service._repair_orphan_queued_chapters(project_id)

    assert repaired == 0
    assert chapter.status == ChapterStatus.QUEUED.value
    assert chapter.last_error is None
    service._has_active_job.assert_awaited_once_with(project_id, 4)
    session.execute.assert_awaited_once()
