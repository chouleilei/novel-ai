import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from backend.db.base import get_db_session
from backend.main import app


async def override_session():
    yield object()


RUNTIME_SETTINGS_PAYLOAD = {
    "review_policy": {
        "overall_score_threshold": 7.5,
        "outline_score_threshold": 7.0,
        "instruction_score_threshold": 6.5,
    },
    "memory_policy": {
        "auto_apply_confidence_threshold": 0.82,
    },
    "context_budget": {
        "writer_target_input_tokens": 12000,
        "writer_hard_limit_tokens": 18000,
        "critic_target_input_tokens": 8000,
        "critic_hard_limit_tokens": 12000,
    },
    "execution": {
        "llm_stage_timeout_seconds": 95.0,
    },
}


def test_get_system_settings_returns_safe_payload(monkeypatch):
    fake_service = SimpleNamespace(
        get_settings_payload=AsyncMock(
            return_value={
                "project_defaults": {
                    "default_total_chapters": 22,
                    "default_auto_mode": False,
                    "default_max_retries": 6,
                },
                "model_configs": [
                    {
                        "role": "writer",
                        "provider": "openai_compatible",
                        "base_url": "https://writer.example/v1",
                        "model_name": "writer-model",
                        "temperature": 1.2,
                        "max_tokens": 4096,
                        "extra_config": {
                            "api_key_env_var": "WRITER_API_KEY",
                            "api_key_encrypted": "enc::saved",
                        },
                    }
                ],
            }
        )
    )
    fake_runtime_service = SimpleNamespace(get_settings_payload=AsyncMock(return_value=RUNTIME_SETTINGS_PAYLOAD))

    app.dependency_overrides[get_db_session] = override_session
    system_module = __import__("backend.api.system", fromlist=["SystemSettingsService", "SystemRuntimeSettingsService"])
    original_service = system_module.SystemSettingsService
    original_runtime_service = system_module.SystemRuntimeSettingsService
    monkeypatch.setattr(system_module, "SystemSettingsService", lambda _session: fake_service)
    monkeypatch.setattr(system_module, "SystemRuntimeSettingsService", lambda _session: fake_runtime_service)
    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: True)
    try:
        client = TestClient(app)
        response = client.get("/api/system/settings")
    finally:
        system_module.SystemSettingsService = original_service
        system_module.SystemRuntimeSettingsService = original_runtime_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "project_defaults": {
            "default_total_chapters": 22,
            "default_auto_mode": False,
            "default_max_retries": 6,
        },
        "model_configs": [
            {
                "role": "writer",
                "provider": "openai_compatible",
                "base_url": "https://writer.example/v1",
                "model_name": "writer-model",
                "temperature": 1.2,
                "max_tokens": 4096,
                "extra_config": {
                    "api_key_env_var": "WRITER_API_KEY",
                    "has_api_key": True,
                    "can_save_api_key": True,
                },
            }
        ],
        "runtime_settings": RUNTIME_SETTINGS_PAYLOAD,
    }


def test_save_system_project_defaults(monkeypatch):
    saved = SimpleNamespace(
        default_total_chapters=18,
        default_auto_mode=True,
        default_max_retries=9,
    )
    fake_session = SimpleNamespace(commit=AsyncMock())
    fake_service = SimpleNamespace(save_project_defaults=AsyncMock(return_value=saved))

    async def override_local_session():
        yield fake_session

    app.dependency_overrides[get_db_session] = override_local_session
    system_module = __import__("backend.api.system", fromlist=["SystemSettingsService"])
    original_service = system_module.SystemSettingsService
    monkeypatch.setattr(system_module, "SystemSettingsService", lambda _session: fake_service)
    try:
        client = TestClient(app)
        response = client.put(
            "/api/system/project-defaults",
            json={
                "default_total_chapters": 18,
                "default_auto_mode": True,
                "default_max_retries": 9,
            },
        )
    finally:
        system_module.SystemSettingsService = original_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "default_total_chapters": 18,
        "default_auto_mode": True,
        "default_max_retries": 9,
    }
    fake_service.save_project_defaults.assert_awaited_once_with(
        default_total_chapters=18,
        default_auto_mode=True,
        default_max_retries=9,
    )
    fake_session.commit.assert_awaited_once()


def test_save_runtime_settings(monkeypatch):
    runtime_snapshot = {
        "review_overall_score_threshold": 7.5,
        "review_outline_score_threshold": 7.0,
        "review_instruction_score_threshold": 6.5,
        "memory_auto_apply_confidence_threshold": 0.82,
        "writer_target_input_tokens": 12000,
        "writer_hard_limit_tokens": 18000,
        "critic_target_input_tokens": 8000,
        "critic_hard_limit_tokens": 12000,
        "llm_stage_timeout_seconds": 95.0,
    }
    fake_session = SimpleNamespace(commit=AsyncMock())
    fake_saved = SimpleNamespace(id=1)
    fake_service = SimpleNamespace(
        get_runtime_snapshot_from_payload=Mock(return_value=runtime_snapshot),
        save_runtime_settings=AsyncMock(return_value=fake_saved),
        serialize_runtime_settings=Mock(return_value=RUNTIME_SETTINGS_PAYLOAD),
    )

    async def override_local_session():
        yield fake_session

    app.dependency_overrides[get_db_session] = override_local_session
    system_module = __import__("backend.api.system", fromlist=["SystemRuntimeSettingsService"])
    original_runtime_service = system_module.SystemRuntimeSettingsService
    monkeypatch.setattr(system_module, "SystemRuntimeSettingsService", lambda _session: fake_service)
    try:
        client = TestClient(app)
        response = client.put("/api/system/runtime-settings", json=RUNTIME_SETTINGS_PAYLOAD)
    finally:
        system_module.SystemRuntimeSettingsService = original_runtime_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == RUNTIME_SETTINGS_PAYLOAD
    fake_service.get_runtime_snapshot_from_payload.assert_called_once_with(RUNTIME_SETTINGS_PAYLOAD)
    fake_service.save_runtime_settings.assert_awaited_once_with(**runtime_snapshot)
    fake_service.serialize_runtime_settings.assert_called_once_with(fake_saved)
    fake_session.commit.assert_awaited_once()


def test_save_runtime_settings_rejects_invalid_budget():
    client = TestClient(app)
    invalid_payload = {
        **RUNTIME_SETTINGS_PAYLOAD,
        "context_budget": {
            **RUNTIME_SETTINGS_PAYLOAD["context_budget"],
            "writer_target_input_tokens": 20000,
            "writer_hard_limit_tokens": 10000,
        },
    }

    response = client.put("/api/system/runtime-settings", json=invalid_payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("writer_hard_limit_tokens 必须大于等于 writer_target_input_tokens" in str(item) for item in detail)


def test_save_system_model_configs(monkeypatch):
    fake_session = SimpleNamespace(commit=AsyncMock())
    fake_service = SimpleNamespace(save_model_configs=AsyncMock(return_value=[object(), object(), object()]))

    async def override_local_session():
        yield fake_session

    app.dependency_overrides[get_db_session] = override_local_session
    system_module = __import__("backend.api.system", fromlist=["SystemSettingsService"])
    original_service = system_module.SystemSettingsService
    monkeypatch.setattr(system_module, "SystemSettingsService", lambda _session: fake_service)
    try:
        client = TestClient(app)
        response = client.put(
            "/api/system/model-configs",
            json=[
                {
                    "role": "writer",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "mock-writer",
                    "temperature": None,
                    "max_tokens": None,
                    "extra_config": {},
                },
                {
                    "role": "critic",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "mock-critic",
                    "temperature": None,
                    "max_tokens": None,
                    "extra_config": {},
                },
                {
                    "role": "memory",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "mock-memory",
                    "temperature": None,
                    "max_tokens": None,
                    "extra_config": {},
                },
            ],
        )
    finally:
        system_module.SystemSettingsService = original_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"count": 3}
    fake_service.save_model_configs.assert_awaited_once()
    fake_session.commit.assert_awaited_once()


def test_test_system_model_connectivity(monkeypatch):
    fake_result = {
        "success": True,
        "role": "writer",
        "provider": "openai_compatible",
        "base_url": "https://writer.example/v1",
        "model_name": "writer-model",
        "latency_ms": 123,
        "message": "模型接口可用，已完成轻量冒烟测试。",
        "output_preview": "连接成功",
        "raw_preview": None,
        "api_key_source": "saved",
    }

    class FakeConnectivityService:
        def __init__(self, _session):
            pass

        async def test_system_model(self, payload):
            assert payload["role"] == "writer"
            return fake_result

    app.dependency_overrides[get_db_session] = override_session
    system_module = __import__("backend.api.system", fromlist=["ModelConnectivityService"])
    original_connectivity_service = system_module.ModelConnectivityService
    monkeypatch.setattr(system_module, "ModelConnectivityService", FakeConnectivityService)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/system/model-configs/test",
            json={
                "role": "writer",
                "provider": "openai_compatible",
                "base_url": "https://writer.example/v1",
                "model_name": "writer-model",
                "temperature": 1.2,
                "max_tokens": 4096,
                "extra_config": {"api_key_env_var": "WRITER_API_KEY"},
            },
        )
    finally:
        system_module.ModelConnectivityService = original_connectivity_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == fake_result


def test_create_channel_persists_discovered_models(monkeypatch):
    fake_session = SimpleNamespace(commit=AsyncMock())
    channel_id = uuid.uuid4()
    captured_models: list[dict] = []

    class FakeChannelService:
        def __init__(self, _session):
            pass

        async def create_channel(self, **payload):
            assert payload["name"] == "DeepSeek"
            return SimpleNamespace(
                id=channel_id,
                name="DeepSeek",
                provider="openai_compatible",
                base_url="https://deepseek.example/v1",
                default_model_name="deepseek-chat",
                api_key_env_var=None,
                api_key="enc::saved",
                is_enabled=True,
                created_at=None,
                updated_at=None,
            )

        async def replace_channel_models(self, current_channel_id, models, default_model_name):
            assert current_channel_id == channel_id
            captured_models.extend(models)
            assert default_model_name == "deepseek-chat"

    async def override_local_session():
        yield fake_session

    app.dependency_overrides[get_db_session] = override_local_session
    system_module = __import__("backend.api.system", fromlist=["ChannelService"])
    original_service = system_module.ChannelService
    monkeypatch.setattr(system_module, "ChannelService", FakeChannelService)
    monkeypatch.setattr(system_module, "serialize_datetime", lambda _value: None)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/system/channels",
            json={
                "name": "DeepSeek",
                "provider": "openai_compatible",
                "base_url": "https://deepseek.example/v1",
                "default_model_name": "deepseek-chat",
                "discovered_models": [
                    {
                        "model_name": "deepseek-chat",
                        "display_name": "DeepSeek Chat",
                        "provider_model_id": "deepseek-chat",
                        "is_default": True,
                        "is_enabled": True,
                    }
                ],
            },
        )
    finally:
        system_module.ChannelService = original_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured_models == [
        {
            "model_name": "deepseek-chat",
            "display_name": "DeepSeek Chat",
            "provider_model_id": "deepseek-chat",
            "is_default": True,
            "is_enabled": True,
        }
    ]
    fake_session.commit.assert_awaited_once()


def test_refresh_channel_models_accepts_override_payload(monkeypatch):
    fake_session = SimpleNamespace(commit=AsyncMock())
    channel_id = uuid.uuid4()

    class FakeChannelService:
        def __init__(self, _session):
            pass

        async def refresh_channel_models(self, current_channel_id, provider_override=None, base_url_override=None, api_key_override=None):
            assert current_channel_id == channel_id
            assert provider_override == "openai_compatible"
            assert base_url_override == "https://override.example/v1"
            assert api_key_override == "sk-override"
            return {"success": True, "models": [], "default_model_name": None}

    async def override_local_session():
        yield fake_session

    app.dependency_overrides[get_db_session] = override_local_session
    system_module = __import__("backend.api.system", fromlist=["ChannelService"])
    original_service = system_module.ChannelService
    monkeypatch.setattr(system_module, "ChannelService", FakeChannelService)
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/system/channels/{channel_id}/refresh-models",
            json={
                "provider": "openai_compatible",
                "base_url": "https://override.example/v1",
                "api_key": "sk-override",
            },
        )
    finally:
        system_module.ChannelService = original_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["success"] is True
    fake_session.commit.assert_awaited_once()
