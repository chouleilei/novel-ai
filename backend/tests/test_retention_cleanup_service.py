import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.db.models import Project, ProjectStatus
from backend.services.retention_cleanup_service import RetentionCleanupService


class _ScalarOneResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


@pytest.mark.asyncio
async def test_cleanup_chapter_content_chunks_deletes_only_chunk_events_for_chapter():
    session = SimpleNamespace(execute=AsyncMock())
    service = RetentionCleanupService(session)  # type: ignore[arg-type]
    project_id = uuid.uuid4()

    await service.cleanup_chapter_content_chunks(project_id, 7)

    statement = session.execute.await_args.args[0]
    assert statement.table.name == "project_events"
    compiled = str(statement)
    assert "project_events.project_id" in compiled
    assert "project_events.chapter_number" in compiled
    assert "project_events.event_type" in compiled


@pytest.mark.asyncio
async def test_cleanup_completed_project_removes_process_artifacts_without_visible_tables():
    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, status=ProjectStatus.COMPLETED.value)
    session = SimpleNamespace(
        get=AsyncMock(return_value=project),
        execute=AsyncMock(side_effect=[_ScalarOneResult(None), None, None, None, None, None, None]),
    )
    service = RetentionCleanupService(session)  # type: ignore[arg-type]

    await service.cleanup_completed_project(project_id)

    session.get.assert_awaited_once_with(Project, project_id)
    statements = [item.args[0] for item in session.execute.await_args_list]
    assert statements[1].table.name == "chapter_attempts"
    deleted_tables = {statement.table.name for statement in statements[2:]}
    assert deleted_tables == {
        "chapter_prompts",
        "project_events",
        "character_revisions",
        "world_setting_revisions",
        "generation_jobs",
    }
    assert "chapters" not in deleted_tables
    assert "chapter_reviews" not in deleted_tables
    assert "chapter_summaries" not in deleted_tables
    assert "characters" not in deleted_tables
    assert "world_settings" not in deleted_tables


@pytest.mark.asyncio
async def test_cleanup_completed_project_noops_for_non_completed_project():
    project_id = uuid.uuid4()
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id=project_id, status=ProjectStatus.RUNNING.value)),
        execute=AsyncMock(),
    )
    service = RetentionCleanupService(session)  # type: ignore[arg-type]

    await service.cleanup_completed_project(project_id)

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_completed_project_noops_when_active_job_exists():
    project_id = uuid.uuid4()
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id=project_id, status=ProjectStatus.COMPLETED.value)),
        execute=AsyncMock(return_value=_ScalarOneResult(uuid.uuid4())),
    )
    service = RetentionCleanupService(session)  # type: ignore[arg-type]

    await service.cleanup_completed_project(project_id)

    assert session.execute.await_count == 1
