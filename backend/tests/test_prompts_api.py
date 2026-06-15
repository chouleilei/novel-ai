import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.db.base import get_db_session
from backend.main import app
from backend.services.prompt_service import PromptBuilderGenerationError


async def override_session():
    yield object()


def test_generate_outlines_returns_outlines_and_builder_payload(monkeypatch):
    project_id = uuid.uuid4()
    fake_service = SimpleNamespace(
        generate_outlines_from_global_prompt=AsyncMock(
            return_value={
                "outlines": [
                    {
                        "chapter_number": 1,
                        "outline_text": "第1章：雨夜来信\n摘要：主角收到来信。",
                        "tags": {"arc": "开篇"},
                    }
                ],
                "builder_payload": {"chapter_outlines": []},
            }
        )
    )

    prompts_module = __import__("backend.api.prompts", fromlist=["PromptService"])
    original_prompt_service = prompts_module.PromptService
    app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr(prompts_module, "PromptService", lambda _session: fake_service)
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/projects/{project_id}/outlines/generate",
            json={
                "title": "测试书名",
                "genre": "悬疑",
                "style": "冷峻",
                "global_prompt": "全局提示词",
                "total_chapters": 1,
            },
        )
    finally:
        prompts_module.PromptService = original_prompt_service
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["outlines"][0]["chapter_number"] == 1
    assert payload["outlines"][0]["outline_text"].startswith("第1章：雨夜来信")
    assert payload["builder_payload"] == {"chapter_outlines": []}


def test_generate_outlines_returns_400_on_validation_error(monkeypatch):
    project_id = uuid.uuid4()
    fake_service = SimpleNamespace(
        generate_outlines_from_global_prompt=AsyncMock(side_effect=ValueError("请先填写全局系统提示词，再生成章节大纲草案"))
    )

    prompts_module = __import__("backend.api.prompts", fromlist=["PromptService"])
    original_prompt_service = prompts_module.PromptService
    app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr(prompts_module, "PromptService", lambda _session: fake_service)
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/projects/{project_id}/outlines/generate",
            json={
                "title": "测试书名",
                "total_chapters": 1,
            },
        )
    finally:
        prompts_module.PromptService = original_prompt_service
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "请先填写全局系统提示词，再生成章节大纲草案"


def test_generate_outlines_returns_builder_diagnostics_on_prompt_builder_failure(monkeypatch):
    project_id = uuid.uuid4()
    fake_service = SimpleNamespace(
        generate_outlines_from_global_prompt=AsyncMock(
            side_effect=PromptBuilderGenerationError(
                "provider unavailable",
                {
                    "failure_type": "request_failed",
                    "fallback_used": False,
                    "model_name": "builder-model",
                },
            )
        )
    )

    prompts_module = __import__("backend.api.prompts", fromlist=["PromptService"])
    original_prompt_service = prompts_module.PromptService
    app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr(prompts_module, "PromptService", lambda _session: fake_service)
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/projects/{project_id}/outlines/generate",
            json={
                "title": "测试书名",
                "genre": "悬疑",
                "style": "冷峻",
                "global_prompt": "全局提示词",
                "total_chapters": 1,
            },
        )
    finally:
        prompts_module.PromptService = original_prompt_service
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["detail"]["message"] == "provider unavailable"
    assert response.json()["detail"]["builder_diagnostics"]["failure_type"] == "request_failed"
    assert response.json()["detail"]["builder_diagnostics"]["fallback_used"] is False
