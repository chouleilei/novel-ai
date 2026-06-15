import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.db.models import ModelRole, SystemModelConfig
from backend.services.system_settings_service import SystemSettingsService


@pytest.mark.asyncio
async def test_get_project_defaults_falls_back_when_missing():
    session = SimpleNamespace(get=AsyncMock(return_value=None))
    service = SystemSettingsService(session)  # type: ignore[arg-type]

    defaults = await service.get_project_defaults()

    assert defaults.id == 1
    assert defaults.default_total_chapters == 60
    assert defaults.default_auto_mode is True
    assert defaults.default_max_retries == 5


@pytest.mark.asyncio
async def test_save_project_defaults_creates_row_when_missing():
    session = SimpleNamespace(get=AsyncMock(return_value=None), add=Mock(), flush=AsyncMock())
    service = SystemSettingsService(session)  # type: ignore[arg-type]

    saved = await service.save_project_defaults(
        default_total_chapters=30,
        default_auto_mode=False,
        default_max_retries=8,
    )

    assert saved.default_total_chapters == 30
    assert saved.default_auto_mode is False
    assert saved.default_max_retries == 8
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_model_configs_falls_back_to_env_defaults_when_empty():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [])))
    service = SystemSettingsService(session)  # type: ignore[arg-type]

    configs = await service.get_model_configs()

    assert [item.role for item in configs] == [
        ModelRole.WRITER.value,
        ModelRole.CRITIC.value,
        ModelRole.MEMORY.value,
        ModelRole.PROMPT_BUILDER.value,
    ]


@pytest.mark.asyncio
async def test_get_model_configs_backfills_missing_required_roles_only():
    existing_prompt_builder = SystemModelConfig(
        role=ModelRole.PROMPT_BUILDER.value,
        provider="openai_compatible",
        base_url="https://builder.example/v1",
        model_name="builder-model",
        temperature=None,
        max_tokens=None,
        extra_config={"api_key_env_var": "PROMPT_BUILDER_API_KEY"},
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [existing_prompt_builder])))
    service = SystemSettingsService(session)  # type: ignore[arg-type]

    configs = await service.get_model_configs()

    roles = [item.role for item in configs]
    assert roles.count(ModelRole.PROMPT_BUILDER.value) == 1
    assert ModelRole.WRITER.value in roles
    assert ModelRole.CRITIC.value in roles
    assert ModelRole.MEMORY.value in roles


@pytest.mark.asyncio
async def test_save_model_configs_encrypts_new_api_key_and_does_not_store_plaintext(monkeypatch):
    existing_writer = SystemModelConfig(
        role=ModelRole.WRITER.value,
        provider="openai_compatible",
        base_url="https://old.example/v1",
        model_name="writer-old",
        temperature=1.2,
        max_tokens=2048,
        extra_config={"api_key_env_var": "WRITER_API_KEY"},
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [existing_writer])),
        add=Mock(),
        delete=AsyncMock(),
        flush=AsyncMock(),
    )
    service = SystemSettingsService(session)  # type: ignore[arg-type]

    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: True)
    monkeypatch.setattr("backend.model_config_utils.encrypt_model_api_key", lambda plain: f"enc::{plain}")

    saved = await service.save_model_configs(
        [
            {
                "role": "writer",
                "provider": "openai_compatible",
                "base_url": "https://new.example/v1",
                "model_name": "writer-new",
                "temperature": 1.0,
                "max_tokens": 4096,
                "extra_config": {
                    "api_key": "sk-system-secret",
                    "api_key_env_var": "SYSTEM_WRITER_KEY",
                },
            }
        ]
    )

    assert saved == [existing_writer]
    assert existing_writer.extra_config == {
        "api_key_env_var": "SYSTEM_WRITER_KEY",
        "api_key_encrypted": "enc::sk-system-secret",
    }
    assert "api_key" not in existing_writer.extra_config
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_model_configs_clears_saved_api_key():
    existing_writer = SystemModelConfig(
        role=ModelRole.WRITER.value,
        provider="openai_compatible",
        base_url="https://old.example/v1",
        model_name="writer-old",
        temperature=1.2,
        max_tokens=2048,
        extra_config={
            "api_key_env_var": "WRITER_API_KEY",
            "api_key_encrypted": "enc::persisted",
        },
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [existing_writer])),
        add=Mock(),
        delete=AsyncMock(),
        flush=AsyncMock(),
    )
    service = SystemSettingsService(session)  # type: ignore[arg-type]

    await service.save_model_configs(
        [
            {
                "role": "writer",
                "provider": "openai_compatible",
                "base_url": "https://writer.example/v1",
                "model_name": "writer-model",
                "temperature": 1.2,
                "max_tokens": 4096,
                "extra_config": {
                    "api_key_env_var": "WRITER_API_KEY",
                    "clear_api_key": True,
                },
            }
        ]
    )

    assert existing_writer.extra_config == {"api_key_env_var": "WRITER_API_KEY"}


@pytest.mark.asyncio
async def test_build_default_model_snapshots_for_project_omits_prompt_builder_when_same_as_writer():
    writer = SystemModelConfig(
        id=uuid.uuid4(),
        role=ModelRole.WRITER.value,
        provider="openai_compatible",
        base_url="https://writer.example/v1",
        model_name="writer-model",
        temperature=1.2,
        max_tokens=4096,
        extra_config={"api_key_env_var": "WRITER_API_KEY"},
    )
    prompt_builder = SystemModelConfig(
        id=uuid.uuid4(),
        role=ModelRole.PROMPT_BUILDER.value,
        provider="openai_compatible",
        base_url="https://writer.example/v1",
        model_name="writer-model",
        temperature=1.2,
        max_tokens=4096,
        extra_config={"api_key_env_var": "WRITER_API_KEY"},
    )
    service = SystemSettingsService(SimpleNamespace())  # type: ignore[arg-type]
    service.get_model_configs = AsyncMock(return_value=[writer, prompt_builder])

    snapshots = await service.build_default_model_snapshots_for_project()

    assert [item["role"] for item in snapshots] == [ModelRole.WRITER.value]


@pytest.mark.asyncio
async def test_build_project_seed_payload_async_uses_persisted_values():
    persisted = SimpleNamespace(
        default_total_chapters=24,
        default_auto_mode=False,
        default_max_retries=6,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=persisted))
    service = SystemSettingsService(session)  # type: ignore[arg-type]

    payload = await service.build_project_seed_payload_async()

    assert payload == {
        "total_chapters": 24,
        "auto_mode": False,
        "auto_accept_critic_failed": False,
        "auto_accept_on_max_retries": False,
        "hard_review_gates_enabled": True,
        "max_retries": 6,
        "writer_streaming_enabled": False,
    }
