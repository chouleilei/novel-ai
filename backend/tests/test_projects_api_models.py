import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from backend.db.base import get_db_session
from backend.main import app


def test_list_projects_serializes_style(monkeypatch):
    project_id = uuid.uuid4()
    fake_project = SimpleNamespace(
        id=project_id,
        title="测试项目",
        genre="玄幻",
        style="冷峻",
        total_chapters=10,
        current_chapter=0,
        status="ready",
        auto_mode=True,
        auto_accept_critic_failed=False,
        auto_accept_on_max_retries=False,
        hard_review_gates_enabled=True,
        generation_mode="standard",
        rush_previous_chapter_count=10,
        updated_at=None,
    )
    fake_service = SimpleNamespace(list_projects=AsyncMock(return_value=[fake_project]))

    async def override_session():
        yield SimpleNamespace(commit=AsyncMock())

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_service)
    monkeypatch.setattr(project_module, "serialize_datetime", lambda _value: None)
    try:
        client = TestClient(app)
        response = client.get("/api/projects")
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["style"] == "冷峻"


def test_copy_project_endpoint_returns_copied_project(monkeypatch):
    source_id = uuid.uuid4()
    copied_id = uuid.uuid4()
    copied_project = SimpleNamespace(
        id=copied_id,
        title="测试项目（副本）",
        genre="玄幻",
        style="冷峻",
        global_prompt="prompt",
        total_chapters=10,
        current_chapter=0,
        status="ready",
        auto_mode=True,
        auto_accept_critic_failed=False,
        auto_accept_on_max_retries=False,
        hard_review_gates_enabled=True,
        max_retries=5,
        writer_streaming_enabled=False,
        generation_mode="standard",
        rush_previous_chapter_count=10,
        last_error=None,
        created_at=None,
        updated_at=None,
    )
    fake_service = SimpleNamespace(copy_project=AsyncMock(return_value=copied_project))
    fake_session = SimpleNamespace(commit=AsyncMock())

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_service)
    monkeypatch.setattr(project_module, "serialize_datetime", lambda _value: None)
    try:
        client = TestClient(app)
        response = client.post(f"/api/projects/{source_id}/copy")
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] == str(copied_id)
    assert payload["title"] == "测试项目（副本）"
    assert payload["style"] == "冷峻"
    fake_service.copy_project.assert_awaited_once_with(source_id)
    fake_session.commit.assert_awaited_once()


@pytest.mark.parametrize("can_save_api_key", [True, False])
def test_get_project_models_hides_api_key_fields(monkeypatch, can_save_api_key):
    project_id = uuid.uuid4()
    fake_service = SimpleNamespace(
        get_project=AsyncMock(return_value=SimpleNamespace(id=project_id)),
        get_model_configs=AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=uuid.uuid4(),
                    role="writer",
                    provider="openai_compatible",
                    base_url="https://writer.example/v1",
                    model_name="writer-model",
                    temperature=1.2,
                    max_tokens=2048,
                    extra_config={
                        "api_key_env_var": "CUSTOM_WRITER_KEY",
                        "api_key_encrypted": "enc::secret",
                    },
                )
            ]
        ),
    )

    async def override_session():
        yield SimpleNamespace(commit=AsyncMock())

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_service)
    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: can_save_api_key)
    try:
        client = TestClient(app)
        response = client.get(f"/api/projects/{project_id}/models")
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["extra_config"] == {
        "api_key_env_var": "CUSTOM_WRITER_KEY",
        "has_api_key": True,
        "can_save_api_key": can_save_api_key,
    }
    assert "api_key" not in payload[0]["extra_config"]
    assert "api_key_encrypted" not in payload[0]["extra_config"]


@pytest.mark.parametrize("can_save_api_key", [True, False])
def test_get_default_model_configs_returns_safe_extra_config_fields(monkeypatch, can_save_api_key):
    fake_service = SimpleNamespace(
        get_default_model_configs=AsyncMock(
            return_value=[
                {
                    "role": "writer",
                    "provider": "openai_compatible",
                    "base_url": "https://writer.example/v1",
                    "model_name": "writer-model",
                    "temperature": 1.2,
                    "max_tokens": 2048,
                    "extra_config": {
                        "api_key_env_var": "WRITER_API_KEY",
                        "api_key_encrypted": "enc::default-should-hide",
                    },
                }
            ]
        )
    )

    async def override_session():
        yield SimpleNamespace(commit=AsyncMock())

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_service)
    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: can_save_api_key)
    try:
        client = TestClient(app)
        response = client.get("/api/projects/defaults/model-configs")
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["extra_config"] == {
        "api_key_env_var": "WRITER_API_KEY",
        "has_api_key": True,
        "can_save_api_key": can_save_api_key,
    }
    assert "api_key" not in payload[0]["extra_config"]
    assert "api_key_encrypted" not in payload[0]["extra_config"]


def test_get_project_defaults_returns_system_seed(monkeypatch):
    fake_service = SimpleNamespace(
        get_default_project_seed=AsyncMock(
            return_value={
                "total_chapters": 12,
                "auto_mode": False,
                "auto_accept_critic_failed": True,
                "auto_accept_on_max_retries": False,
                "hard_review_gates_enabled": False,
                "max_retries": 7,
                "writer_streaming_enabled": False,
            }
        )
    )

    async def override_session():
        yield SimpleNamespace(commit=AsyncMock())

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_service)
    try:
        client = TestClient(app)
        response = client.get("/api/projects/defaults")
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "total_chapters": 12,
        "auto_mode": False,
        "auto_accept_critic_failed": True,
        "auto_accept_on_max_retries": False,
        "hard_review_gates_enabled": False,
        "max_retries": 7,
        "writer_streaming_enabled": False,
    }


def test_get_project_detail_serializes_auto_accept_on_max_retries(monkeypatch):
    project_id = uuid.uuid4()
    fake_project = SimpleNamespace(
        id=project_id,
        title="测试项目",
        genre="玄幻",
        style="冷峻",
        global_prompt="prompt",
        total_chapters=10,
        current_chapter=3,
        status="paused",
        auto_mode=False,
        auto_accept_critic_failed=False,
        auto_accept_on_max_retries=True,
        hard_review_gates_enabled=False,
        max_retries=4,
        writer_streaming_enabled=False,
        generation_mode="rush",
        rush_previous_chapter_count=10,
        last_error=None,
        created_at=None,
        updated_at=None,
    )
    fake_service = SimpleNamespace(get_project=AsyncMock(return_value=fake_project))

    async def override_session():
        yield SimpleNamespace(commit=AsyncMock())

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_service)
    monkeypatch.setattr(project_module, "serialize_datetime", lambda _value: None)
    try:
        client = TestClient(app)
        response = client.get(f"/api/projects/{project_id}")
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["auto_accept_on_max_retries"] is True
    assert response.json()["hard_review_gates_enabled"] is False
    assert response.json()["generation_mode"] == "rush"
    assert response.json()["rush_previous_chapter_count"] == 10


def test_get_project_models_defaults_critic_schema_flag_when_missing(monkeypatch):
    project_id = uuid.uuid4()
    fake_service = SimpleNamespace(
        get_project=AsyncMock(return_value=SimpleNamespace(id=project_id)),
        get_model_configs=AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=uuid.uuid4(),
                    role="critic",
                    provider="openai_compatible",
                    base_url="https://critic.example/v1",
                    model_name="critic-model",
                    temperature=None,
                    max_tokens=None,
                    extra_config={"api_key_env_var": "CRITIC_API_KEY"},
                )
            ]
        ),
    )

    async def override_session():
        yield SimpleNamespace(commit=AsyncMock())

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_service)
    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: True)
    try:
        client = TestClient(app)
        response = client.get(f"/api/projects/{project_id}/models")
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["extra_config"]["supports_json_schema_output"] is True


def test_get_default_model_configs_defaults_critic_schema_flag_when_missing(monkeypatch):
    fake_service = SimpleNamespace(
        get_default_model_configs=AsyncMock(
            return_value=[
                {
                    "role": "critic",
                    "provider": "openai_compatible",
                    "base_url": "https://critic.example/v1",
                    "model_name": "critic-model",
                    "temperature": None,
                    "max_tokens": None,
                    "extra_config": {"api_key_env_var": "CRITIC_API_KEY"},
                }
            ]
        )
    )

    async def override_session():
        yield object()

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_service)
    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: True)
    try:
        client = TestClient(app)
        response = client.get("/api/projects/defaults/model-configs")
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["extra_config"]["supports_json_schema_output"] is True


def test_get_project_models_preserves_explicitly_disabled_critic_schema_flag(monkeypatch):
    project_id = uuid.uuid4()
    fake_service = SimpleNamespace(
        get_project=AsyncMock(return_value=SimpleNamespace(id=project_id)),
        get_model_configs=AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=uuid.uuid4(),
                    role="critic",
                    provider="openai_compatible",
                    base_url="https://critic.example/v1",
                    model_name="critic-model",
                    temperature=None,
                    max_tokens=None,
                    extra_config={
                        "api_key_env_var": "CRITIC_API_KEY",
                        "supports_json_schema_output": False,
                    },
                )
            ]
        ),
    )

    async def override_session():
        yield object()

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_service)
    monkeypatch.setattr("backend.model_config_utils.has_configured_encryption_key", lambda: True)
    try:
        client = TestClient(app)
        response = client.get(f"/api/projects/{project_id}/models")
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["extra_config"]["supports_json_schema_output"] is False


def test_save_project_models_omitted_critic_schema_flag_stays_default_enabled(monkeypatch):
    project_id = uuid.uuid4()
    captured_payloads: list[list[dict[str, object]]] = []
    fake_project = SimpleNamespace(id=project_id)

    class FakeProjectService:
        def __init__(self, _session):
            pass

        async def get_project(self, current_project_id):
            assert current_project_id == project_id
            return fake_project

        async def save_model_configs(self, current_project_id, payload):
            assert current_project_id == project_id
            captured_payloads.append(payload)
            return []

    async def override_session():
        yield SimpleNamespace(commit=AsyncMock())

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService"])
    original_project_service = project_module.ProjectService
    monkeypatch.setattr(project_module, "ProjectService", FakeProjectService)
    try:
        client = TestClient(app)
        response = client.put(
            f"/api/projects/{project_id}/models",
            json=[
                {
                    "role": "writer",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "writer-model",
                    "extra_config": {},
                },
                {
                    "role": "critic",
                    "provider": "openai_compatible",
                    "base_url": "https://critic.example/v1",
                    "model_name": "critic-model",
                    "extra_config": {"api_key_env_var": "CRITIC_API_KEY"},
                },
                {
                    "role": "memory",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "memory-model",
                    "extra_config": {},
                },
            ],
        )
    finally:
        project_module.ProjectService = original_project_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    critic_payload = next(item for item in captured_payloads[0] if item["role"] == "critic")
    extra_config = cast(dict[str, Any], critic_payload.get("extra_config") or {})
    assert "supports_json_schema_output" not in extra_config


def test_test_model_connectivity_returns_structured_success_result(monkeypatch):
    project_id = uuid.uuid4()
    fake_project_service = SimpleNamespace(get_project=AsyncMock(return_value=SimpleNamespace(id=project_id)))
    fake_result = {
        "success": True,
        "role": "writer",
        "provider": "openai_compatible",
        "base_url": "https://writer.example/v1",
        "model_name": "writer-model",
        "latency_ms": 321,
        "message": "模型接口可用，已完成轻量冒烟测试。",
        "output_preview": "连接成功",
        "raw_preview": None,
        "api_key_source": "env_var",
    }

    async def override_session():
        yield object()

    class FakeConnectivityService:
        def __init__(self, _session):
            pass

        async def test_model(self, current_project_id, payload):
            assert current_project_id == project_id
            assert payload["role"] == "writer"
            return fake_result

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService", "ModelConnectivityService"])
    original_project_service = project_module.ProjectService
    original_connectivity_service = project_module.ModelConnectivityService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_project_service)
    monkeypatch.setattr(project_module, "ModelConnectivityService", FakeConnectivityService)
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/projects/{project_id}/models/test",
            json={
                "role": "writer",
                "provider": "openai_compatible",
                "base_url": "https://writer.example/v1",
                "model_name": "writer-model",
                "extra_config": {"api_key_env_var": "WRITER_API_KEY"},
            },
        )
    finally:
        project_module.ProjectService = original_project_service
        project_module.ModelConnectivityService = original_connectivity_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == fake_result


def test_test_model_connectivity_returns_structured_failure_result(monkeypatch):
    project_id = uuid.uuid4()
    fake_project_service = SimpleNamespace(get_project=AsyncMock(return_value=SimpleNamespace(id=project_id)))
    fake_result = {
        "success": False,
        "role": "prompt_builder",
        "provider": "openai_compatible",
        "base_url": "https://builder.example/v1",
        "model_name": "builder-model",
        "latency_ms": 912,
        "message": "连通性测试失败：prompt_builder 返回的 system_prompt 不是有效字符串",
        "output_preview": None,
        "raw_preview": "{\"system_prompt\":123}",
        "api_key_source": "inline",
    }

    async def override_session():
        yield object()

    class FakeConnectivityService:
        def __init__(self, _session):
            pass

        async def test_model(self, current_project_id, payload):
            assert current_project_id == project_id
            assert payload["role"] == "prompt_builder"
            return fake_result

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService", "ModelConnectivityService"])
    original_project_service = project_module.ProjectService
    original_connectivity_service = project_module.ModelConnectivityService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_project_service)
    monkeypatch.setattr(project_module, "ModelConnectivityService", FakeConnectivityService)
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/projects/{project_id}/models/test",
            json={
                "role": "prompt_builder",
                "provider": "openai_compatible",
                "base_url": "https://builder.example/v1",
                "model_name": "builder-model",
                "extra_config": {"api_key": "sk-test"},
            },
        )
    finally:
        project_module.ProjectService = original_project_service
        project_module.ModelConnectivityService = original_connectivity_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == fake_result


def test_test_model_connectivity_allows_critic_payload_without_passed(monkeypatch):
    project_id = uuid.uuid4()
    fake_project_service = SimpleNamespace(get_project=AsyncMock(return_value=SimpleNamespace(id=project_id)))
    fake_result = {
        "success": True,
        "role": "critic",
        "provider": "openai_compatible",
        "base_url": "https://critic.example/v1",
        "model_name": "critic-model",
        "latency_ms": 455,
        "message": "模型接口可用，已完成轻量冒烟测试。",
        "output_preview": '{"overall_score": 8.6, "dimensions": {"outline_adherence": {"score": 9}}}',
        "raw_preview": None,
        "api_key_source": "env_var",
    }

    async def override_session():
        yield object()

    class FakeConnectivityService:
        def __init__(self, _session):
            pass

        async def test_model(self, current_project_id, payload):
            assert current_project_id == project_id
            assert payload["role"] == "critic"
            return fake_result

    app.dependency_overrides[get_db_session] = override_session
    project_module = __import__("backend.api.projects", fromlist=["ProjectService", "ModelConnectivityService"])
    original_project_service = project_module.ProjectService
    original_connectivity_service = project_module.ModelConnectivityService
    monkeypatch.setattr(project_module, "ProjectService", lambda _session: fake_project_service)
    monkeypatch.setattr(project_module, "ModelConnectivityService", FakeConnectivityService)
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/projects/{project_id}/models/test",
            json={
                "role": "critic",
                "provider": "openai_compatible",
                "base_url": "https://critic.example/v1",
                "model_name": "critic-model",
                "extra_config": {"api_key_env_var": "CRITIC_API_KEY"},
            },
        )
    finally:
        project_module.ProjectService = original_project_service
        project_module.ModelConnectivityService = original_connectivity_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == fake_result
