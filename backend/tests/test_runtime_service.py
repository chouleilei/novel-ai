import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.db.models import ProviderChannel
from backend.services.runtime_service import RuntimeService


@pytest.mark.asyncio
async def test_resolve_api_key_prefers_inline_plaintext_key_for_runtime_tests():
    service = RuntimeService(session=None)  # type: ignore[arg-type]
    config = SimpleNamespace(
        role="writer",
        extra_config={
            "api_key": "plain-inline-key",
            "api_key_encrypted": "enc::writer",
            "api_key_env_var": "CUSTOM_WRITER_KEY",
        },
    )

    api_key, env_key = await service.resolve_api_key(config)  # type: ignore[arg-type]

    assert api_key == "plain-inline-key"
    assert env_key is None


@pytest.mark.asyncio
async def test_resolve_api_key_prefers_encrypted_project_key(monkeypatch):
    service = RuntimeService(session=None)  # type: ignore[arg-type]
    config = SimpleNamespace(role="writer", extra_config={"api_key_encrypted": "enc::writer"})

    monkeypatch.setattr("backend.services.runtime_service.decrypt_model_api_key", lambda ciphertext: f"plain::{ciphertext}")

    api_key, env_key = await service.resolve_api_key(config)  # type: ignore[arg-type]

    assert api_key == "plain::enc::writer"
    assert env_key is None


@pytest.mark.asyncio
async def test_resolve_api_key_prefers_model_specific_env_var(monkeypatch):
    monkeypatch.setenv("CUSTOM_WRITER_KEY", "secret-value")
    service = RuntimeService(session=None)  # type: ignore[arg-type]
    config = SimpleNamespace(role="writer", extra_config={"api_key_env_var": "CUSTOM_WRITER_KEY"})

    api_key, env_key = await service.resolve_api_key(config)  # type: ignore[arg-type]

    assert env_key == "CUSTOM_WRITER_KEY"
    assert api_key == "secret-value"


@pytest.mark.asyncio
async def test_resolve_api_key_falls_back_to_channel_saved_key(monkeypatch):
    channel_id = uuid.uuid4()
    session = SimpleNamespace(
        get=AsyncMock(
            return_value=ProviderChannel(
                id=channel_id,
                name="Saved Channel",
                provider="openai_compatible",
                base_url="https://saved.example/v1",
                default_model_name="writer-model",
                api_key="legacy-plain-channel-key",
                api_key_env_var=None,
                is_enabled=True,
            )
        )
    )
    service = RuntimeService(session=session)  # type: ignore[arg-type]
    config = SimpleNamespace(role="writer", channel_id=channel_id, extra_config={})

    monkeypatch.setattr("backend.services.runtime_service.has_configured_encryption_key", lambda: False)

    api_key, env_key = await service.resolve_api_key(config)  # type: ignore[arg-type]

    assert env_key is None
    assert api_key == "legacy-plain-channel-key"


@pytest.mark.asyncio
async def test_resolve_api_key_falls_back_to_channel_env_var(monkeypatch):
    channel_id = uuid.uuid4()
    monkeypatch.setenv("CHANNEL_WRITER_KEY", "channel-secret")
    session = SimpleNamespace(
        get=AsyncMock(
            return_value=ProviderChannel(
                id=channel_id,
                name="Env Channel",
                provider="openai_compatible",
                base_url="https://saved.example/v1",
                default_model_name="writer-model",
                api_key=None,
                api_key_env_var="CHANNEL_WRITER_KEY",
                is_enabled=True,
            )
        )
    )
    service = RuntimeService(session=session)  # type: ignore[arg-type]
    config = SimpleNamespace(role="writer", channel_id=channel_id, extra_config={})

    api_key, env_key = await service.resolve_api_key(config)  # type: ignore[arg-type]

    assert env_key == "CHANNEL_WRITER_KEY"
    assert api_key == "channel-secret"


@pytest.mark.asyncio
async def test_resolve_api_key_falls_back_to_default_role_env(monkeypatch):
    monkeypatch.setenv("MEMORY_API_KEY", "memory-secret")
    service = RuntimeService(session=None)  # type: ignore[arg-type]
    config = SimpleNamespace(role="memory", extra_config={})

    api_key, env_key = await service.resolve_api_key(config)  # type: ignore[arg-type]

    assert env_key == "MEMORY_API_KEY"
    assert api_key == "memory-secret"


@pytest.mark.asyncio
async def test_resolve_api_key_treats_non_mapping_extra_config_as_empty(monkeypatch):
    monkeypatch.setenv("WRITER_API_KEY", "writer-secret")
    service = RuntimeService(session=None)  # type: ignore[arg-type]
    config = SimpleNamespace(role="writer", extra_config=["not-a-dict"])

    api_key, env_key = await service.resolve_api_key(config)  # type: ignore[arg-type]

    assert env_key == "WRITER_API_KEY"
    assert api_key == "writer-secret"


@pytest.mark.asyncio
async def test_get_model_configs_uses_project_defaults_when_database_has_none():
    empty_result = SimpleNamespace(scalars=lambda: [])
    session = SimpleNamespace(execute=AsyncMock(return_value=empty_result))
    service = RuntimeService(session=session)  # type: ignore[arg-type]

    default_writer = SimpleNamespace(role="writer")
    default_memory = SimpleNamespace(role="memory")

    async def fake_project_defaults(project_id):
        return [default_writer, default_memory]

    service_module = __import__("backend.services.project_service", fromlist=["ProjectService"])
    original_project_service = service_module.ProjectService

    class FakeProjectService:
        def __init__(self, current_session):
            assert current_session is session

        get_model_configs = staticmethod(fake_project_defaults)

    service_module.ProjectService = FakeProjectService
    try:
        configs = await service.get_model_configs("project-id")  # type: ignore[arg-type]
    finally:
        service_module.ProjectService = original_project_service

    assert configs == {
        "writer": default_writer,
        "memory": default_memory,
    }
