import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.db.models import AttemptStatus, ChapterStatus, JobStatus, ProjectStatus
from backend.worker import runner
from backend.worker.job_queue import DEFAULT_JOB_ERROR_MESSAGE, JobQueue
from backend.worker.pipeline import DEFERRED_RETRY_JOB_KEY


class _FakeSession:
    def __init__(self, *, get_result=None):
        self._get_result = get_result
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock()
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, *_args, **_kwargs):
        return self._get_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, sessions):
        self._sessions = iter(sessions)

    def __call__(self):
        return next(self._sessions)


class _StopWorkerLoop(RuntimeError):
    pass


class SilentWorkerError(Exception):
    pass


def test_summarize_worker_error_produces_non_empty_fallbacks():
    assert runner.summarize_worker_error(httpx.ReadTimeout("")) == "模型响应读取超时，请稍后重试。"
    assert runner.summarize_worker_error(asyncio.TimeoutError()) == "任务执行超时，请稍后重试。"
    assert runner.summarize_worker_error(SilentWorkerError()) == "SilentWorkerError（未提供详细错误信息）"
    assert runner.summarize_worker_error(Exception()) == DEFAULT_JOB_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_run_worker_failure_branch_records_non_empty_timeout_error(monkeypatch):
    project_id = uuid.uuid4()
    job = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        chapter_number=20,
        job_type="generate_chapter",
    )
    main_session = _FakeSession(get_result=job)
    failure_session = _FakeSession(get_result=job)
    idle_session = _FakeSession(get_result=None)
    appended_events: list[tuple[object, ...]] = []
    failed_errors: list[str] = []
    claim_count = 0

    class FakeJobQueue:
        def __init__(self, session):
            self.session = session

        async def claim_next_job(self):
            nonlocal claim_count
            claim_count += 1
            return job if claim_count == 1 else None

        async def mark_done(self, _job):  # pragma: no cover
            raise AssertionError("unexpected mark_done call")

        async def mark_failed(self, failed_job, error_message):
            assert failed_job is job
            failed_errors.append(error_message)

    class FakePipeline:
        def __init__(self, session):
            self.session = session

        async def process_generate_chapter(self, claimed_job):
            assert claimed_job is job
            raise httpx.ReadTimeout("")

    class FakeEventService:
        def __init__(self, session):
            self.session = session

        async def append(self, current_project_id, event_type, event_data, chapter_number=None):
            appended_events.append((current_project_id, event_type, event_data, chapter_number))

    async def fake_renew_job_lease(stop_event, _job_id, _project_id):
        await stop_event.wait()

    sleep = AsyncMock(side_effect=_StopWorkerLoop())

    monkeypatch.setattr(runner, "JobQueue", FakeJobQueue)
    monkeypatch.setattr(runner, "Pipeline", FakePipeline)
    monkeypatch.setattr(runner, "EventService", FakeEventService)
    monkeypatch.setattr(runner, "AsyncSessionLocal", _SessionFactory([main_session, failure_session, idle_session]))
    monkeypatch.setattr(runner, "renew_job_lease", fake_renew_job_lease)
    monkeypatch.setattr(runner.asyncio, "sleep", sleep)

    with pytest.raises(_StopWorkerLoop):
        await runner.run_worker()

    assert len(appended_events) == 1
    current_project_id, event_type, event_data, chapter_number = appended_events[0]
    assert isinstance(event_data, dict)
    assert current_project_id == project_id
    assert event_type == "job_failed"
    assert chapter_number == 20
    assert event_data["job_id"] == str(job.id)
    assert event_data["error"] == "模型响应读取超时，请稍后重试。"
    assert failed_errors == [event_data["error"]]
    main_session.rollback.assert_awaited_once()
    failure_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_failed_uses_default_message_when_error_is_blank():
    project_id = uuid.uuid4()
    chapter_id = uuid.uuid4()
    job = SimpleNamespace(
        project_id=project_id,
        chapter_number=7,
        status=JobStatus.LEASED.value,
        last_error=None,
        lease_owner="worker-1",
        lease_expires_at=object(),
    )
    project = SimpleNamespace(
        id=project_id,
        status=ProjectStatus.RUNNING.value,
        last_error=None,
        lease_owner="worker-1",
        lease_expires_at=object(),
    )
    chapter = SimpleNamespace(
        id=chapter_id,
        status=ChapterStatus.WRITING.value,
        last_error=None,
    )
    attempt = SimpleNamespace(
        status=AttemptStatus.RUNNING.value,
        finished_at=None,
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=project),
        scalar=AsyncMock(side_effect=[chapter, attempt]),
        execute=AsyncMock(),
        flush=AsyncMock(),
    )
    queue = JobQueue(session)  # type: ignore[arg-type]
    project.lease_owner = queue.settings.worker_name
    job.lease_owner = queue.settings.worker_name

    await queue.mark_failed(job, "   ")

    assert job.status == JobStatus.FAILED.value
    assert job.last_error == DEFAULT_JOB_ERROR_MESSAGE
    assert job.lease_owner is None
    assert job.lease_expires_at is None
    assert project.status == ProjectStatus.PAUSED.value
    assert project.last_error == DEFAULT_JOB_ERROR_MESSAGE
    assert project.lease_owner is None
    assert project.lease_expires_at is None
    assert chapter.status == ChapterStatus.FAILED.value
    assert chapter.last_error == DEFAULT_JOB_ERROR_MESSAGE
    assert attempt.status == AttemptStatus.ERRORED.value
    assert attempt.finished_at is not None
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_worker_enqueues_deferred_retry_after_marking_current_job_done(monkeypatch):
    project_id = uuid.uuid4()
    job = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        chapter_number=8,
        job_type="generate_chapter",
        payload={
            DEFERRED_RETRY_JOB_KEY: {
                "project_id": str(project_id),
                "chapter_number": 8,
                "payload": {"retry": True},
            }
        },
    )
    main_session = _FakeSession(get_result=job)
    idle_session = _FakeSession(get_result=None)
    claim_count = 0
    mark_done_calls: list[object] = []
    enqueue_calls: list[tuple[object, ...]] = []

    class FakeJobQueue:
        def __init__(self, session):
            self.session = session

        async def claim_next_job(self):
            nonlocal claim_count
            claim_count += 1
            return job if claim_count == 1 else None

        async def mark_done(self, finished_job):
            mark_done_calls.append(finished_job)

        async def mark_failed(self, _job, _error_message):  # pragma: no cover
            raise AssertionError("unexpected mark_failed call")

    class FakePipeline:
        def __init__(self, session):
            self.session = session

        async def process_generate_chapter(self, claimed_job):
            assert claimed_job is job

    async def fake_enqueue(self, project_id_arg, chapter_number, payload=None):
        enqueue_calls.append((project_id_arg, chapter_number, payload or {}))
        return SimpleNamespace(project_id=project_id_arg, chapter_number=chapter_number, payload=payload or {})

    async def fake_renew_job_lease(stop_event, _job_id, _project_id):
        await stop_event.wait()

    sleep = AsyncMock(side_effect=_StopWorkerLoop())

    monkeypatch.setattr(runner, "JobQueue", FakeJobQueue)
    monkeypatch.setattr(runner, "Pipeline", FakePipeline)
    monkeypatch.setattr(runner, "AsyncSessionLocal", _SessionFactory([main_session, idle_session]))
    monkeypatch.setattr(runner, "renew_job_lease", fake_renew_job_lease)
    monkeypatch.setattr(runner.asyncio, "sleep", sleep)
    monkeypatch.setattr(runner.GenerationService, "_enqueue_generate_job", fake_enqueue)

    with pytest.raises(_StopWorkerLoop):
        await runner.run_worker()

    assert mark_done_calls == [job]
    assert enqueue_calls == [(project_id, 8, {"retry": True})]
    assert DEFERRED_RETRY_JOB_KEY not in job.payload
    assert main_session.commit.await_count == 2
