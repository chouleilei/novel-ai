import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.db.models import Chapter, ChapterOutline, Project, ProjectStatus, ProjectModelConfig
from backend.services.project_service import ProjectService
from backend.services.system_settings_service import SystemSettingsService


@pytest.mark.asyncio
async def test_import_outlines_clears_generated_artifacts_and_resets_project_state():
    session = SimpleNamespace(execute=AsyncMock(), add=Mock(), flush=AsyncMock())
    service = ProjectService(session)  # type: ignore[arg-type]
    project = SimpleNamespace(
        current_chapter=8,
        last_error="旧错误",
        total_chapters=8,
        status=ProjectStatus.RUNNING.value,
        lease_owner="worker-1",
        lease_expires_at=object(),
        distant_memory_cache="旧缓存",
        distant_memory_updated_chapter=8,
    )
    service.get_project = AsyncMock(return_value=project)

    await service.import_outlines(
        uuid.uuid4(),
        [
            {"chapter_number": 1, "outline_text": "第一章", "tags": {}},
            {"chapter_number": 2, "outline_text": "第二章", "tags": {}},
        ],
    )

    deleted_tables = {call.args[0].table.name for call in session.execute.await_args_list}
    assert deleted_tables == {
        "generation_jobs",
        "project_events",
        "chapter_prompts",
        "chapter_summaries",
        "character_revisions",
        "world_setting_revisions",
        "characters",
        "world_settings",
        "chapter_outlines",
        "chapters",
    }
    assert session.add.call_count == 4
    assert project.current_chapter == 0
    assert project.last_error is None
    assert project.total_chapters == 2
    assert project.status == ProjectStatus.READY.value
    assert project.lease_owner is None
    assert project.lease_expires_at is None
    assert project.distant_memory_cache is None
    assert project.distant_memory_updated_chapter is None
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_copy_project_copies_settings_models_and_outlines_without_generated_content():
    source_id = uuid.uuid4()
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = ProjectService(session)  # type: ignore[arg-type]
    service.get_project = AsyncMock(
        return_value=SimpleNamespace(
            id=source_id,
            title="源项目",
            genre="科幻",
            style="冷峻",
            global_prompt="全局提示词",
            total_chapters=2,
            auto_mode=False,
            auto_accept_critic_failed=True,
            auto_accept_on_max_retries=False,
            hard_review_gates_enabled=False,
            max_retries=7,
            writer_streaming_enabled=True,
            generation_mode="rush",
            rush_previous_chapter_count=6,
        )
    )
    service.get_model_configs = AsyncMock(
        return_value=[
            ProjectModelConfig(
                project_id=source_id,
                role="writer",
                provider="openai_compatible",
                base_url="https://writer.example/v1",
                model_name="writer-model",
                temperature=1.2,
                max_tokens=4096,
                extra_config={"api_key_encrypted": "enc::secret"},
            )
        ]
    )
    service.list_outlines = AsyncMock(
        return_value=[
            SimpleNamespace(chapter_number=1, outline_text="第一章大纲", tags={"arc": "a"}),
            SimpleNamespace(chapter_number=2, outline_text="第二章大纲", tags={}),
        ]
    )

    copied = await service.copy_project(source_id)

    assert copied is not None
    assert copied.title == "源项目（副本）"
    assert copied.style == "冷峻"
    assert copied.current_chapter == 0
    assert copied.status == ProjectStatus.READY.value
    assert copied.total_chapters == 2

    added_items = [call.args[0] for call in session.add.call_args_list]
    assert any(isinstance(item, Project) for item in added_items)
    assert sum(isinstance(item, ProjectModelConfig) for item in added_items) == 1
    assert sum(isinstance(item, ChapterOutline) for item in added_items) == 2
    copied_chapters = [item for item in added_items if isinstance(item, Chapter)]
    assert len(copied_chapters) == 2
    assert all(item.final_content is None for item in copied_chapters)
    assert all(item.status == "pending" for item in copied_chapters)
    assert session.flush.await_count == 2


@pytest.mark.asyncio
async def test_get_model_configs_returns_default_models_without_persisting_when_project_has_none():
    empty_result = SimpleNamespace(scalars=lambda: [])
    session = SimpleNamespace(execute=AsyncMock(return_value=empty_result))
    service = ProjectService(session)  # type: ignore[arg-type]
    service.get_project = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    service.save_model_configs = AsyncMock()

    configs = await service.get_model_configs(uuid.uuid4())

    assert [item.role for item in configs] == ["writer", "critic", "memory", "prompt_builder"]
    service.save_model_configs.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_project_copies_system_defaults_and_writer_streaming_flag():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = ProjectService(session)  # type: ignore[arg-type]
    service.save_model_configs = AsyncMock()
    service.system_settings.build_default_model_snapshots_for_project = AsyncMock(
        return_value=[
            {
                "role": "writer",
                "provider": "openai_compatible",
                "base_url": "https://writer.example/v1",
                "model_name": "writer-model",
                "temperature": 1.2,
                "max_tokens": 4096,
                "extra_config": {"api_key_env_var": "WRITER_API_KEY"},
            }
        ]
    )

    project = await service.create_project(
        title="测试项目",
        genre="科幻",
        style="冷峻",
        global_prompt="prompt",
        total_chapters=12,
        auto_mode=False,
        auto_accept_critic_failed=True,
        auto_accept_on_max_retries=True,
        hard_review_gates_enabled=False,
        max_retries=7,
        writer_streaming_enabled=True,
        generation_mode="rush",
        rush_previous_chapter_count=10,
    )

    assert project.title == "测试项目"
    assert project.auto_accept_critic_failed is True
    assert project.auto_accept_on_max_retries is True
    assert project.hard_review_gates_enabled is False
    assert project.writer_streaming_enabled is True
    assert project.generation_mode == "rush"
    assert project.rush_previous_chapter_count == 10
    assert project.status == ProjectStatus.DRAFT.value
    service.save_model_configs.assert_awaited_once_with(project.id, await service.system_settings.build_default_model_snapshots_for_project())
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_model_configs_encrypts_new_api_key_and_never_stores_plaintext(monkeypatch):
    existing_writer = ProjectModelConfig(
        project_id=uuid.uuid4(),
        role="writer",
        provider="openai_compatible",
        base_url="https://old.example/v1",
        model_name="writer-old",
        temperature=1.2,
        max_tokens=1024,
        extra_config={"api_key_env_var": "WRITER_API_KEY"},
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [existing_writer])), add=Mock(), delete=AsyncMock(), flush=AsyncMock())
    service = ProjectService(session)  # type: ignore[arg-type]

    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: True)
    monkeypatch.setattr("backend.model_config_utils.encrypt_model_api_key", lambda plain: f"enc::{plain}")

    saved = await service.save_model_configs(
        existing_writer.project_id,
        [
            {
                "role": "writer",
                "provider": "openai_compatible",
                "base_url": "https://new.example/v1",
                "model_name": "writer-new",
                "temperature": 1.0,
                "max_tokens": 2048,
                "extra_config": {
                    "api_key": "sk-new-secret",
                    "api_key_env_var": "CUSTOM_WRITER_KEY",
                    "clear_api_key": False,
                },
            }
        ],
    )

    assert saved == [existing_writer]
    assert existing_writer.extra_config == {
        "api_key_env_var": "CUSTOM_WRITER_KEY",
        "api_key_encrypted": "enc::sk-new-secret",
    }
    assert "api_key" not in existing_writer.extra_config
    session.delete.assert_not_awaited()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_model_configs_keeps_existing_encrypted_api_key_when_not_resubmitted(monkeypatch):
    project_id = uuid.uuid4()
    existing_writer = ProjectModelConfig(
        project_id=project_id,
        role="writer",
        provider="openai_compatible",
        base_url="https://old.example/v1",
        model_name="writer-old",
        temperature=1.2,
        max_tokens=1024,
        extra_config={
            "api_key_env_var": "WRITER_API_KEY",
            "api_key_encrypted": "enc::persisted",
        },
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [existing_writer])), add=Mock(), delete=AsyncMock(), flush=AsyncMock())
    service = ProjectService(session)  # type: ignore[arg-type]

    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: True)

    await service.save_model_configs(
        project_id,
        [
            {
                "role": "writer",
                "provider": "openai_compatible",
                "base_url": "https://updated.example/v1",
                "model_name": "writer-updated",
                "temperature": 0.8,
                "max_tokens": 2048,
                "extra_config": {
                    "api_key_env_var": "UPDATED_WRITER_KEY",
                    "clear_api_key": False,
                },
            }
        ],
    )

    assert existing_writer.extra_config == {
        "api_key_env_var": "UPDATED_WRITER_KEY",
        "api_key_encrypted": "enc::persisted",
    }


@pytest.mark.asyncio
async def test_save_model_configs_clears_existing_encrypted_api_key():
    project_id = uuid.uuid4()
    existing_writer = ProjectModelConfig(
        project_id=project_id,
        role="writer",
        provider="openai_compatible",
        base_url="https://old.example/v1",
        model_name="writer-old",
        temperature=1.2,
        max_tokens=1024,
        extra_config={
            "api_key_env_var": "WRITER_API_KEY",
            "api_key_encrypted": "enc::persisted",
        },
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [existing_writer])), add=Mock(), delete=AsyncMock(), flush=AsyncMock())
    service = ProjectService(session)  # type: ignore[arg-type]

    await service.save_model_configs(
        project_id,
        [
            {
                "role": "writer",
                "provider": "openai_compatible",
                "base_url": "https://updated.example/v1",
                "model_name": "writer-updated",
                "temperature": 0.8,
                "max_tokens": 2048,
                "extra_config": {
                    "api_key_env_var": "WRITER_API_KEY",
                    "clear_api_key": True,
                },
            }
        ],
    )

    assert existing_writer.extra_config == {"api_key_env_var": "WRITER_API_KEY"}


@pytest.mark.asyncio
async def test_save_model_configs_rejects_plain_api_key_without_encryption_key(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [])), add=Mock(), delete=AsyncMock(), flush=AsyncMock())
    service = ProjectService(session)  # type: ignore[arg-type]

    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: False)

    with pytest.raises(ValueError, match="NOVEL_AI_MODEL_SECRET_ENCRYPTION_KEY"):
        await service.save_model_configs(
            uuid.uuid4(),
            [
                {
                    "role": "writer",
                    "provider": "openai_compatible",
                    "base_url": "https://new.example/v1",
                    "model_name": "writer-new",
                    "temperature": 1.0,
                    "max_tokens": 2048,
                    "extra_config": {
                        "api_key": "sk-new-secret",
                    },
                }
            ],
        )


@pytest.mark.asyncio
async def test_get_default_model_configs_reads_from_system_settings_service():
    service = ProjectService(SimpleNamespace())  # type: ignore[arg-type]
    expected = [
        {"role": "writer", "model_name": "writer-model"},
        {"role": "critic", "model_name": "critic-model"},
    ]
    service.system_settings.build_default_model_snapshots_for_project = AsyncMock(return_value=expected)

    configs = await service.get_default_model_configs()

    assert configs == expected


@pytest.mark.asyncio
async def test_get_default_project_seed_reads_from_system_settings_service():
    service = ProjectService(SimpleNamespace())  # type: ignore[arg-type]
    expected = {
        "total_chapters": 18,
        "auto_mode": False,
        "auto_accept_critic_failed": False,
        "auto_accept_on_max_retries": False,
        "hard_review_gates_enabled": True,
        "max_retries": 9,
        "writer_streaming_enabled": False,
    }
    service.system_settings.build_project_seed_payload_async = AsyncMock(return_value=expected)

    payload = await service.get_default_project_seed()

    assert payload == expected


@pytest.mark.asyncio
async def test_standard_mode_requires_writer_critic_and_memory_models():
    service = ProjectService(SimpleNamespace())  # type: ignore[arg-type]
    service.get_project = AsyncMock(return_value=SimpleNamespace(generation_mode="standard"))
    service.get_model_configs = AsyncMock(
        return_value=[
            SimpleNamespace(role="writer"),
            SimpleNamespace(role="critic"),
        ]
    )

    assert await service.ensure_required_model_roles(uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_rush_mode_requires_writer_only():
    service = ProjectService(SimpleNamespace())  # type: ignore[arg-type]
    service.get_project = AsyncMock(return_value=SimpleNamespace(generation_mode="rush"))
    service.get_model_configs = AsyncMock(return_value=[SimpleNamespace(role="writer")])

    assert await service.ensure_required_model_roles(uuid.uuid4()) is True


@pytest.mark.asyncio
async def test_system_settings_build_project_seed_payload_defaults_writer_streaming_disabled():
    service = SystemSettingsService(SimpleNamespace())  # type: ignore[arg-type]

    payload = service.build_project_seed_payload()

    assert payload == {
        "total_chapters": 60,
        "auto_mode": True,
        "auto_accept_critic_failed": False,
        "auto_accept_on_max_retries": False,
        "hard_review_gates_enabled": True,
        "max_retries": 5,
        "writer_streaming_enabled": False,
    }
