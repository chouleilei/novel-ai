from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.db.models import ChapterPrompt, PromptStatus
from backend.services.prompt_service import PromptBuilderGenerationError, PromptGenerationResult, PromptService


@pytest.mark.asyncio
async def test_get_or_create_effective_prompt_prefers_approved_prompt():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    approved_prompt = object()
    service.get_approved_prompt = AsyncMock(return_value=approved_prompt)
    service.generate_prompt_result = AsyncMock()

    result = await service.get_or_create_effective_prompt(
        project_id="project-id",
        chapter_number=3,
        precheck={"issues": []},
        retry_info={"failed_reasons": []},
    )

    assert result is approved_prompt
    service.generate_prompt_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_effective_prompt_can_force_regenerate_when_no_manual_prompt():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    generated_prompt = object()
    approved = ChapterPrompt(
        project_id="project-id",
        chapter_number=3,
        version_no=2,
        generated_system_prompt="旧提示词",
        user_edited_prompt=None,
        effective_system_prompt="旧提示词",
        source_payload={},
        status=PromptStatus.APPROVED.value,
    )
    service.get_approved_prompt = AsyncMock(return_value=approved)
    service.generate_prompt_result = AsyncMock(
        return_value=PromptGenerationResult(prompt=generated_prompt, source="builder")
    )

    result = await service.get_or_create_effective_prompt(
        project_id="project-id",
        chapter_number=3,
        precheck={},
        retry_info={},
        force_regenerate=True,
    )

    assert result is generated_prompt
    service.generate_prompt_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_or_create_effective_prompt_preserves_manual_approved_prompt_on_retry():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    approved = ChapterPrompt(
        project_id="project-id",
        chapter_number=3,
        version_no=2,
        generated_system_prompt="旧提示词",
        user_edited_prompt="人工修订版提示词",
        effective_system_prompt="人工修订版提示词",
        source_payload={},
        status=PromptStatus.APPROVED.value,
    )
    service.get_approved_prompt = AsyncMock(return_value=approved)
    service.generate_prompt_result = AsyncMock()

    result = await service.get_or_create_effective_prompt(
        project_id="project-id",
        chapter_number=3,
        precheck={},
        retry_info={"failed_reasons": ["补强冲突"]},
        force_regenerate=True,
    )

    assert result is approved
    service.generate_prompt_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_prompt_payload_uses_writer_as_prompt_builder_fallback():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    client = SimpleNamespace(generate_json=AsyncMock(return_value={"system_prompt": "可执行提示词"}))
    service._resolve_prompt_builder_config = AsyncMock(
        return_value=(
            SimpleNamespace(provider="openai_compatible", base_url="https://writer.example/v1", model_name="writer-model"),
            "writer",
        )
    )
    service._build_prompt_builder_client = lambda _config: client

    payload = await service._build_prompt_payload(
        "project-id",
        {
            "outline": "第一章大纲",
            "global_prompt": "全局要求",
            "precheck": {},
            "retry_info": {},
        },
    )

    assert payload["system_prompt"] == "可执行提示词"
    service._resolve_prompt_builder_config.assert_awaited_once_with("project-id")


@pytest.mark.asyncio
async def test_build_global_prompt_payload_uses_writer_as_prompt_builder_fallback():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    client = SimpleNamespace(
        generate_json=AsyncMock(
            return_value={
                "title": "测试书名",
                "genre": "悬疑",
                "style": "冷峻",
                "global_prompt": "全局提示词",
            }
        )
    )
    service.runtime.get_client = AsyncMock(return_value=client)

    payload = await service._build_global_prompt_payload(
        "project-id",
        {
            "title": "测试书名",
            "genre": "悬疑",
            "style": "冷峻",
            "outlines_text": "第一章\n\n第二章",
            "chapter_count": 2,
        },
    )

    assert payload["global_prompt"] == "全局提示词"
    assert payload["title"] == "测试书名"
    assert payload["genre"] == "悬疑"
    assert payload["style"] == "冷峻"
    service.runtime.get_client.assert_awaited_once_with("project-id", "prompt_builder", fallback_role="writer")


@pytest.mark.asyncio
async def test_build_global_prompt_payload_falls_back_safely_when_builder_returns_list():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    client = SimpleNamespace(generate_json=AsyncMock(return_value=[{"global_prompt": "unexpected"}]))
    service.runtime.get_client = AsyncMock(return_value=client)
    service._resolve_prompt_builder_config = AsyncMock(return_value=(
        SimpleNamespace(provider="openai_compatible", base_url="https://builder.example/v1", model_name="builder-model"),
        "prompt_builder",
    ))

    payload = await service._build_global_prompt_payload(
        "project-id",
        {
            "title": "测试书名",
            "genre": "悬疑",
            "style": "冷峻",
            "outlines_text": "第一章\n\n第二章",
            "chapter_count": 2,
        },
    )

    assert payload["title"] == "测试书名"
    assert payload["genre"] == "悬疑"
    assert payload["style"] == "冷峻"
    assert payload["reasoning_notes"] == ["fallback global prompt builder"]
    assert payload["builder_diagnostics"]["failure_type"] == "invalid_payload"
    assert payload["builder_diagnostics"]["fallback_used"] is True


@pytest.mark.asyncio
async def test_build_global_prompt_payload_returns_fallback_diagnostics_on_json_failure():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    config = SimpleNamespace(provider="openai_compatible", base_url="https://builder.example/v1", model_name="builder-model")
    service.runtime.get_client = AsyncMock(side_effect=ValueError("invalid json"))
    service._resolve_prompt_builder_config = AsyncMock(return_value=(config, "prompt_builder"))

    payload = await service._build_global_prompt_payload(
        "project-id",
        {
            "title": "测试书名",
            "genre": "悬疑",
            "style": "冷峻",
            "outlines_text": "第一章\n\n第二章",
            "chapter_count": 2,
        },
    )

    assert payload["reasoning_notes"] == ["fallback global prompt builder"]
    assert payload["builder_diagnostics"]["failure_type"] == "json_parse_error"
    assert payload["builder_diagnostics"]["fallback_used"] is True
    assert payload["builder_diagnostics"]["model_name"] == "builder-model"


@pytest.mark.asyncio
async def test_build_global_prompt_payload_raises_on_request_failure():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    service.runtime.get_client = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    service._resolve_prompt_builder_config = AsyncMock(return_value=(
        SimpleNamespace(provider="openai_compatible", base_url="https://builder.example/v1", model_name="builder-model"),
        "prompt_builder",
    ))

    with pytest.raises(PromptBuilderGenerationError) as exc_info:
        await service._build_global_prompt_payload(
            "project-id",
            {
                "title": "测试书名",
                "genre": "悬疑",
                "style": "冷峻",
                "outlines_text": "第一章\n\n第二章",
                "chapter_count": 2,
            },
        )

    assert exc_info.value.diagnostics["failure_type"] == "request_failed"
    assert exc_info.value.diagnostics["fallback_used"] is False


@pytest.mark.asyncio
async def test_generate_global_prompt_returns_autofill_fields():
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(title="", genre=None, style=None))
    )
    service = PromptService(session)  # type: ignore[arg-type]
    service._resolve_global_prompt_outlines = AsyncMock(return_value="第一章：开端\n\n第二章：转折")
    service._build_global_prompt_payload = AsyncMock(
        return_value={
            "title": "锦瑟成灰 第二卷：金粉陷阱",
            "genre": "民国乱世 / 权谋",
            "style": "冷峻压抑、残酷现实",
            "global_prompt": "项目级全局提示词",
        }
    )

    result = await service.generate_global_prompt(
        project_id="project-id",
        outlines_text="第一章：开端\n\n第二章：转折",
    )

    assert result["title"] == "锦瑟成灰 第二卷：金粉陷阱"
    assert "民国乱世" in result["genre"]
    assert "权谋" in result["genre"]
    assert "冷峻压抑" in result["style"]
    assert result["global_prompt"] == "项目级全局提示词"


def test_render_builder_user_message_renders_structured_retry_info():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]

    message = service._render_builder_user_message(
        {
            "global_prompt": "全局要求",
            "outline": "本章大纲",
            "precheck": {
                "continuity_notes": ["保持上一章结尾的时间顺序"],
                "suggested_focus": ["突出钟楼压迫感"],
            },
            "retry_info": {
                "recent_failed_attempts": 2,
                "recent_blocking_issues": ["缺少核心对峙"],
                "recent_uncovered_outline_points": ["没有写出钟楼密钥激活规则"],
                "recent_improvement_suggestions": ["补强主角的主动决策"],
            },
        }
    )

    assert "【连续性预检查】" in message
    assert "保持上一章结尾的时间顺序" in message
    assert "【重试上下文】" in message
    assert "最近失败尝试次数：2" in message
    assert "缺少核心对峙" in message


def test_fallback_prompt_text_renders_structured_retry_info():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]

    prompt_text = service._fallback_prompt_text(
        {
            "outline": "本章大纲",
            "global_prompt": "全局要求",
            "precheck": {"continuity_notes": ["接续上一章结尾"]},
            "retry_info": {
                "recent_failed_attempts": 2,
                "failed_reasons": ["优先修复：缺少核心对峙"],
                "recent_violated_instructions": ["文风偏离冷峻要求"],
            },
        }
    )

    assert "连续性注意事项：接续上一章结尾" in prompt_text
    assert "最近失败尝试次数：2" in prompt_text
    assert "优先修复：缺少核心对峙" in prompt_text
    assert "文风偏离冷峻要求" in prompt_text


def test_resolve_project_info_can_infer_values_from_outlines():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]

    result = service._resolve_project_info(
        {},
        {
            "title": "",
            "genre": "",
            "style": "",
            "outlines_text": "\n".join(
                [
                    "社会全景铺设",
                    "《锦瑟成灰》第二卷：《金粉陷阱》",
                    "政治层：袁世凯死后的权力真空，直系与皖系在国务院、参议院的拉锯",
                    "文化层：《新青年》创刊，留洋派与旧学派的冲突",
                    "第一章：凤冠与勃朗宁",
                    "苏宛清在霍公馆中被迫登台，气氛冷硬压抑。",
                ]
            ),
        },
    )

    assert result["title"] == "《锦瑟成灰》第二卷：《金粉陷阱》"
    assert "民国乱世" in result["genre"]
    assert "权谋" in result["genre"]
    assert "冷峻压抑" in result["style"]


@pytest.mark.asyncio
async def test_build_prompt_payload_result_returns_fallback_diagnostics_on_json_failure():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    service.settings.llm_stage_timeout_seconds = 180.0
    config = SimpleNamespace(provider="openai_compatible", base_url="https://builder.example/v1", model_name="builder-model")
    service._resolve_prompt_builder_config = AsyncMock(return_value=(config, "prompt_builder"))
    failing_client = SimpleNamespace(generate_json=AsyncMock(side_effect=ValueError("invalid json")))
    service._build_prompt_builder_client = lambda _config: failing_client

    payload, source, diagnostics = await service._build_prompt_payload_result(
        "project-id",
        {
            "outline": "第一章大纲",
            "global_prompt": "全局要求",
            "precheck": {},
            "retry_info": {},
        },
    )

    assert source == "fallback"
    assert payload["system_prompt"].startswith("本章执行要求")
    assert diagnostics is not None
    assert diagnostics["failure_type"] == "json_parse_error"
    assert diagnostics["fallback_used"] is True
    assert diagnostics["model_name"] == "builder-model"


@pytest.mark.asyncio
async def test_build_prompt_payload_result_raises_on_request_failure_instead_of_silent_fallback():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    service.settings.llm_stage_timeout_seconds = 180.0
    config = SimpleNamespace(provider="openai_compatible", base_url="https://builder.example/v1", model_name="builder-model")
    service._resolve_prompt_builder_config = AsyncMock(return_value=(config, "prompt_builder"))
    failing_client = SimpleNamespace(generate_json=AsyncMock(side_effect=RuntimeError("upstream unavailable")))
    service._build_prompt_builder_client = lambda _config: failing_client

    with pytest.raises(PromptBuilderGenerationError) as exc_info:
        await service._build_prompt_payload_result(
            "project-id",
            {
                "outline": "第一章大纲",
                "global_prompt": "全局要求",
                "precheck": {},
                "retry_info": {},
            },
        )

    assert exc_info.value.diagnostics["failure_type"] == "request_failed"
    assert exc_info.value.diagnostics["fallback_used"] is False
    assert exc_info.value.diagnostics["model_name"] == "builder-model"


@pytest.mark.asyncio
async def test_generate_prompt_result_persists_builder_diagnostics_when_fallback_used():
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(global_prompt="全局要求")),
        add=lambda _item: None,
        flush=AsyncMock(),
    )
    service = PromptService(session)  # type: ignore[arg-type]
    service._get_outline = AsyncMock(return_value=SimpleNamespace(outline_text="第一章大纲"))
    service.get_latest_prompt = AsyncMock(return_value=None)
    service._build_prompt_payload_result = AsyncMock(return_value=(
        {"system_prompt": "fallback 提示词"},
        "fallback",
        {"fallback_used": True, "failure_type": "timeout", "message": "超时"},
    ))

    result = await service.generate_prompt_result(
        project_id="project-id",
        chapter_number=1,
        precheck={},
        retry_info={},
    )

    assert result.source == "fallback"
    assert result.diagnostics is not None
    assert result.prompt.source_payload["builder_diagnostics"]["failure_type"] == "timeout"
    assert result.prompt.generated_system_prompt == "fallback 提示词"


@pytest.mark.asyncio
async def test_create_timeout_fallback_prompt_result_marks_stage_timeout():
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(global_prompt="全局要求")),
        add=lambda _item: None,
        flush=AsyncMock(),
    )
    service = PromptService(session)  # type: ignore[arg-type]
    service.settings.llm_stage_timeout_seconds = 180.0
    service._resolve_prompt_builder_config = AsyncMock(return_value=(
        SimpleNamespace(provider="openai_compatible", base_url="https://builder.example/v1", model_name="builder-model"),
        "prompt_builder",
    ))
    service._get_outline = AsyncMock(return_value=SimpleNamespace(outline_text="第一章大纲"))
    service.get_latest_prompt = AsyncMock(return_value=None)

    result = await service.create_timeout_fallback_prompt_result(
        project_id="project-id",
        chapter_number=1,
        precheck={},
        retry_info={},
        error_message="stage prompt_generation timed out after 180s",
    )



@pytest.mark.asyncio
async def test_generate_outlines_from_global_prompt_returns_rendered_outlines():
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(title="测试书名", genre="悬疑", style="冷峻", global_prompt="全局提示词", total_chapters=2))
    )
    service = PromptService(session)  # type: ignore[arg-type]
    service._build_outline_payload = AsyncMock(
        return_value={
            "chapter_outlines": [
                {
                    "chapter_number": 1,
                    "title": "雨夜来信",
                    "summary": "主角收到一封改变命运的来信。",
                    "must_cover": ["雨夜到来", "来信内容曝光"],
                    "tags": {"arc": "开篇"},
                },
                {
                    "chapter_number": 2,
                    "title": "追索线索",
                    "summary": "主角开始追查来信来源。",
                    "must_cover": ["锁定嫌疑人"],
                },
            ]
        }
    )

    result = await service.generate_outlines_from_global_prompt(project_id="project-id")

    assert len(result["outlines"]) == 2
    assert result["outlines"][0]["chapter_number"] == 1
    assert result["outlines"][0]["outline_text"].startswith("第1章：雨夜来信")
    assert "摘要：主角收到一封改变命运的来信。" in result["outlines"][0]["outline_text"]
    assert "- 雨夜到来" in result["outlines"][0]["outline_text"]
    assert result["outlines"][0]["tags"] == {"arc": "开篇"}


@pytest.mark.asyncio
async def test_build_outline_payload_accepts_top_level_list_payload():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    client = SimpleNamespace(
        generate_json=AsyncMock(
            return_value=[
                {"title": "雨夜来信", "summary": "主角收到一封改变命运的来信。", "must_cover": ["雨夜到来"]},
                {"title": "追索线索", "summary": "主角开始追查来信来源。", "must_cover": ["锁定嫌疑人"]},
            ]
        )
    )
    service.runtime.get_client = AsyncMock(return_value=client)

    payload = await service._build_outline_payload(
        "project-id",
        {
            "title": "测试书名",
            "genre": "悬疑",
            "style": "冷峻",
            "global_prompt": "全局提示词",
            "total_chapters": 2,
        },
    )

    assert isinstance(payload["chapter_outlines"], list)
    assert payload["chapter_outlines"][0]["title"] == "雨夜来信"


@pytest.mark.asyncio
async def test_build_outline_payload_returns_fallback_diagnostics_on_json_failure():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    config = SimpleNamespace(provider="openai_compatible", base_url="https://builder.example/v1", model_name="builder-model")
    service.runtime.get_client = AsyncMock(side_effect=ValueError("invalid json"))
    service._resolve_prompt_builder_config = AsyncMock(return_value=(config, "prompt_builder"))

    payload = await service._build_outline_payload(
        "project-id",
        {
            "title": "测试书名",
            "genre": "悬疑",
            "style": "冷峻",
            "global_prompt": "全局提示词",
            "total_chapters": 2,
        },
    )

    assert payload["reasoning_notes"] == ["fallback outline builder"]
    assert payload["builder_diagnostics"]["failure_type"] == "json_parse_error"
    assert payload["builder_diagnostics"]["fallback_used"] is True
    assert payload["builder_diagnostics"]["model_name"] == "builder-model"


@pytest.mark.asyncio
async def test_build_outline_payload_raises_on_missing_api_key_configuration():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]
    service.runtime.get_client = AsyncMock(side_effect=ValueError("prompt_builder 缺少 API Key，请配置环境变量 PROMPT_BUILDER_API_KEY"))
    service._resolve_prompt_builder_config = AsyncMock(return_value=(
        SimpleNamespace(provider="openai_compatible", base_url="https://builder.example/v1", model_name="builder-model"),
        "prompt_builder",
    ))

    with pytest.raises(PromptBuilderGenerationError) as exc_info:
        await service._build_outline_payload(
            "project-id",
            {
                "title": "测试书名",
                "genre": "悬疑",
                "style": "冷峻",
                "global_prompt": "全局提示词",
                "total_chapters": 2,
            },
        )

    assert exc_info.value.diagnostics["failure_type"] == "missing_api_key"
    assert exc_info.value.diagnostics["fallback_used"] is False


@pytest.mark.asyncio
async def test_generate_outlines_from_global_prompt_requires_global_prompt():
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(title="", genre="", style="", global_prompt="", total_chapters=3))
    )
    service = PromptService(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="请先填写全局系统提示词"):
        await service.generate_outlines_from_global_prompt(project_id="project-id")


def test_render_outline_text_matches_project_setup_parser_format():
    service = PromptService(SimpleNamespace())  # type: ignore[arg-type]

    outline_text = service._render_outline_text(
        {
            "title": "命运转折",
            "summary": "主角第一次意识到阴谋存在。",
            "must_cover": ["发现关键证据", "与反派首次交锋"],
        },
        1,
    )

    assert outline_text.startswith("第1章：命运转折")
    assert "摘要：主角第一次意识到阴谋存在。" in outline_text
    assert "关键点：" in outline_text
    assert "- 发现关键证据" in outline_text
