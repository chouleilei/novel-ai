import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.api.resources import build_content_disposition
from backend.db.base import get_db_session
from backend.main import app


async def override_session():
    yield object()


def test_build_content_disposition_supports_unicode_filename():
    header = build_content_disposition("雨城折纸", "md")

    assert 'filename="novel.md"' in header
    assert "filename*=UTF-8''%E9%9B%A8%E5%9F%8E%E6%8A%98%E7%BA%B8.md" in header


def test_build_content_disposition_preserves_ascii_filename():
    header = build_content_disposition("rain-city", "txt")

    assert 'filename="rain-city.txt"' in header
    assert "filename*=UTF-8''rain-city.txt" in header


def test_list_characters_prefers_canonical_resources_over_summary_fallback(monkeypatch):
    project_id = uuid.uuid4()
    fake_project_service = SimpleNamespace(get_project=AsyncMock(return_value=SimpleNamespace(id=project_id)))
    fake_memory_service = SimpleNamespace(
        list_characters=AsyncMock(
            return_value=[
                SimpleNamespace(
                    name="沈夜",
                    role="调查员",
                    profile_json={
                        "latest_state": "已根据自动应用的 memory revision 更新到正式资源表",
                        "review_required": True,
                    },
                    is_active=True,
                )
            ]
        ),
        build_character_resources_from_summaries=AsyncMock(
            return_value=[
                {
                    "name": "旧版沈夜",
                    "role": "旧身份",
                    "profile_json": {"latest_state": "这是 summary fallback，不应被读取"},
                    "is_active": True,
                }
            ]
        ),
    )

    resources_module = __import__("backend.api.resources", fromlist=["ProjectService", "MemoryService"])
    original_project_service = resources_module.ProjectService
    original_memory_service = resources_module.MemoryService
    app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr(resources_module, "ProjectService", lambda _session: fake_project_service)
    monkeypatch.setattr(resources_module, "MemoryService", lambda _session: fake_memory_service)
    try:
        client = TestClient(app)
        response = client.get(f"/api/projects/{project_id}/characters")
    finally:
        resources_module.ProjectService = original_project_service
        resources_module.MemoryService = original_memory_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "沈夜",
            "role": "调查员",
            "profile_json": {
                "latest_state": "已根据自动应用的 memory revision 更新到正式资源表",
                "review_required": True,
            },
            "is_active": True,
        }
    ]
    fake_memory_service.list_characters.assert_awaited_once_with(project_id)
    fake_memory_service.build_character_resources_from_summaries.assert_not_awaited()


def test_list_world_settings_prefers_canonical_resources_over_summary_fallback(monkeypatch):
    project_id = uuid.uuid4()
    fake_project_service = SimpleNamespace(get_project=AsyncMock(return_value=SimpleNamespace(id=project_id)))
    fake_memory_service = SimpleNamespace(
        list_world_settings=AsyncMock(
            return_value=[
                SimpleNamespace(
                    category="location",
                    name="钟楼",
                    setting_json={
                        "description": "已根据自动应用的 world revision 更新到正式资源表",
                        "review_required": True,
                    },
                )
            ]
        ),
        build_world_setting_resources_from_summaries=AsyncMock(
            return_value=[
                {
                    "category": "location",
                    "name": "旧钟楼",
                    "setting_json": {"description": "这是 summary fallback，不应被读取"},
                }
            ]
        ),
    )

    resources_module = __import__("backend.api.resources", fromlist=["ProjectService", "MemoryService"])
    original_project_service = resources_module.ProjectService
    original_memory_service = resources_module.MemoryService
    app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr(resources_module, "ProjectService", lambda _session: fake_project_service)
    monkeypatch.setattr(resources_module, "MemoryService", lambda _session: fake_memory_service)
    try:
        client = TestClient(app)
        response = client.get(f"/api/projects/{project_id}/world-settings")
    finally:
        resources_module.ProjectService = original_project_service
        resources_module.MemoryService = original_memory_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "category": "location",
            "name": "钟楼",
            "setting_json": {
                "description": "已根据自动应用的 world revision 更新到正式资源表",
                "review_required": True,
            },
        }
    ]
    fake_memory_service.list_world_settings.assert_awaited_once_with(project_id)
    fake_memory_service.build_world_setting_resources_from_summaries.assert_not_awaited()
