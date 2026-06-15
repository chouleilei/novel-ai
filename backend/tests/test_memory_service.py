import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from backend.llm.openai_compatible import LLMJSONDecodeError
from backend.services.memory_service import MemoryService


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return list(self._rows)


def test_high_risk_character_update_requires_manual_review():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    payload = {
        "name": "沈夜",
        "change_type": "deactivate",
        "confidence": 0.98,
        "notes": "角色死亡",
    }

    assert service._resolve_apply_mode(payload, revision_type="character") == "needs_review"


def test_normalize_storage_payload_truncates_summary_boundary_fields():
    service = MemoryService(session=None)  # type: ignore[arg-type]

    payload = service._normalize_storage_payload(
        "summary",
        {
            "summary_text": "摘要",
            "emotional_tone": "紧" * 80,
            "time_location": "夜" * 260,
        },
    )

    assert len(payload["emotional_tone"]) == 50
    assert len(payload["time_location"]) == 200


def test_normalize_storage_payload_truncates_character_and_world_boundary_fields():
    service = MemoryService(session=None)  # type: ignore[arg-type]

    character_payload = service._normalize_storage_payload(
        "character",
        {"name": "沈" * 140, "role": "主" * 80, "profile_json": {}},
    )
    world_payload = service._normalize_storage_payload(
        "world",
        {"name": "城" * 260, "category": "设" * 80, "setting_json": {}},
    )

    assert len(character_payload["name"]) == 100
    assert len(character_payload["role"]) == 50
    assert len(world_payload["name"]) == 200
    assert len(world_payload["category"]) == 50


def test_low_risk_world_update_can_be_auto_applied():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    payload = {
        "name": "黑潮港",
        "category": "location",
        "change_type": "create",
        "confidence": 0.92,
        "description": "新增沿海港口",
    }

    assert service._resolve_apply_mode(payload, revision_type="world") == "auto_safe"


def test_low_confidence_update_respects_custom_threshold():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    payload = {
        "name": "沈夜",
        "change_type": "update",
        "confidence": 0.79,
        "profile_json": {"latest_state": "决定继续追查"},
    }

    assert service._resolve_apply_mode(payload, revision_type="character", confidence_threshold=0.8) == "needs_review"


def test_high_risk_update_still_requires_manual_review_even_with_lower_threshold():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    payload = {
        "name": "沈夜",
        "change_type": "deactivate",
        "confidence": 0.2,
        "notes": "角色死亡",
    }

    assert service._resolve_apply_mode(payload, revision_type="character", confidence_threshold=0.1) == "needs_review"


@pytest.mark.asyncio
async def test_save_summary_and_revisions_passes_confidence_threshold_to_update_handlers():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    service._generate_summary = AsyncMock(return_value={"summary_text": "摘要"})
    service._upsert_summary = AsyncMock()
    service._generate_character_updates = AsyncMock(return_value=[{"name": "沈夜", "confidence": 0.7}])
    service._generate_world_updates = AsyncMock(return_value=[{"name": "钟楼", "category": "location", "confidence": 0.7}])
    service._derive_character_updates_from_summary = Mock(return_value=[])
    service._derive_world_updates_from_summary = Mock(return_value=[])
    service._apply_character_updates = AsyncMock(return_value={"total": 1, "applied": 0, "needs_review": 1})
    service._apply_world_updates = AsyncMock(return_value={"total": 1, "applied": 0, "needs_review": 1})
    service._update_distant_memory_cache = AsyncMock(return_value="cache")

    result = await service.save_summary_and_revisions(
        "project-id",
        3,
        "正文",
        confidence_threshold=0.9,
    )

    service._apply_character_updates.assert_awaited_once_with(
        "project-id",
        3,
        [{"name": "沈夜", "confidence": 0.7}],
        confidence_threshold=0.9,
    )
    service._apply_world_updates.assert_awaited_once_with(
        "project-id",
        3,
        [{"name": "钟楼", "category": "location", "confidence": 0.7}],
        confidence_threshold=0.9,
    )
    assert result["needs_review_count"] == 2


@pytest.mark.asyncio
async def test_save_summary_and_revisions_runs_each_substage_through_stage_runner():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    service._generate_summary = AsyncMock(return_value={"summary_text": "摘要"})
    service._upsert_summary = AsyncMock()
    service._generate_character_updates = AsyncMock(return_value=[])
    service._generate_world_updates = AsyncMock(return_value=[])
    service._derive_character_updates_from_summary = Mock(return_value=[])
    service._derive_world_updates_from_summary = Mock(return_value=[])
    service._apply_character_updates = AsyncMock(return_value={"total": 0, "applied": 0, "needs_review": 0})
    service._apply_world_updates = AsyncMock(return_value={"total": 0, "applied": 0, "needs_review": 0})
    service._update_distant_memory_cache = AsyncMock(return_value="cache")

    stage_calls: list[str] = []

    async def stage_runner(stage_name: str, operation):
        stage_calls.append(stage_name)
        return await operation

    result = await service.save_summary_and_revisions(
        "project-id",
        4,
        "正文",
        stage_runner=stage_runner,
    )

    assert result["distant_memory_cache"] == "cache"

    # 验证所有必需的 stage 都被调用
    assert set(stage_calls) == {
        "summary_generation",
        "summary_persist",
        "character_updates_generation",
        "world_updates_generation",
        "character_updates_apply",
        "world_updates_apply",
        "distant_memory_refresh",
    }

    # 验证第一阶段（并行 generation）在第二阶段之前
    generation_stages = {"summary_generation", "character_updates_generation", "world_updates_generation"}
    last_generation_idx = max(stage_calls.index(s) for s in generation_stages)
    assert stage_calls.index("summary_persist") > last_generation_idx

    # 验证 apply 在 summary_persist 之后
    assert stage_calls.index("character_updates_apply") > stage_calls.index("summary_persist")
    assert stage_calls.index("world_updates_apply") > stage_calls.index("summary_persist")

    # 验证 distant_memory_refresh 在最后
    assert stage_calls[-1] == "distant_memory_refresh"


@pytest.mark.asyncio
async def test_save_summary_and_revisions_falls_back_when_summary_generation_times_out():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    service._generate_summary = AsyncMock(side_effect=TimeoutError("stage memory_update:summary_generation timed out after 360s"))
    service._upsert_summary = AsyncMock()
    service._generate_character_updates = AsyncMock(return_value=[])
    service._generate_world_updates = AsyncMock(return_value=[])
    service._derive_character_updates_from_summary = Mock(return_value=[])
    service._derive_world_updates_from_summary = Mock(return_value=[])
    service._apply_character_updates = AsyncMock(return_value={"total": 0, "applied": 0, "needs_review": 0})
    service._apply_world_updates = AsyncMock(return_value={"total": 0, "applied": 0, "needs_review": 0})
    service._update_distant_memory_cache = AsyncMock(return_value="cache")

    result = await service.save_summary_and_revisions("project-id", 9, "正文内容用于超时降级")

    service._upsert_summary.assert_awaited_once()
    summary_payload = service._upsert_summary.await_args.args[2]
    assert summary_payload == {
        "summary_text": "正文内容用于超时降级",
        "key_events": [],
        "character_changes": {},
        "world_changes": {},
        "unresolved_threads": [],
        "emotional_tone": "",
        "time_location": "",
    }
    assert result["summary"] == summary_payload


@pytest.mark.asyncio
async def test_save_summary_and_revisions_falls_back_when_summary_generation_returns_invalid_json():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    service._generate_summary = AsyncMock(side_effect=LLMJSONDecodeError("invalid json", raw_preview="bad"))
    service._upsert_summary = AsyncMock()
    service._generate_character_updates = AsyncMock(return_value=[])
    service._generate_world_updates = AsyncMock(return_value=[])
    service._derive_character_updates_from_summary = Mock(return_value=[])
    service._derive_world_updates_from_summary = Mock(return_value=[])
    service._apply_character_updates = AsyncMock(return_value={"total": 0, "applied": 0, "needs_review": 0})
    service._apply_world_updates = AsyncMock(return_value={"total": 0, "applied": 0, "needs_review": 0})
    service._update_distant_memory_cache = AsyncMock(return_value="cache")

    result = await service.save_summary_and_revisions("project-id", 10, "正文内容用于解析降级")

    summary_payload = service._upsert_summary.await_args.args[2]
    assert summary_payload["summary_text"] == "正文内容用于解析降级"
    assert result["summary"]["summary_text"] == "正文内容用于解析降级"


@pytest.mark.asyncio
async def test_save_summary_and_revisions_still_raises_non_recoverable_summary_error():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    service._generate_summary = AsyncMock(side_effect=RuntimeError("db broken"))

    with pytest.raises(RuntimeError, match="db broken"):
        await service.save_summary_and_revisions("project-id", 11, "正文")


@pytest.mark.asyncio
async def test_apply_character_updates_marks_low_confidence_payload_for_review_and_still_updates_canonical_resource():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = MemoryService(session)  # type: ignore[arg-type]
    service._upsert_character = AsyncMock()

    result = await service._apply_character_updates(
        "project-id",
        5,
        [{"name": "沈夜", "change_type": "update", "confidence": 0.6}],
        confidence_threshold=0.8,
    )

    assert result == {"total": 1, "applied": 1, "needs_review": 0}
    service._upsert_character.assert_awaited_once_with(
        "project-id",
        {"name": "沈夜", "change_type": "update", "confidence": 0.6},
    )
    revision = session.add.call_args.args[0]
    assert revision.apply_mode == "applied"
    assert revision.patch_json["review_required"] is True
    assert revision.patch_json["original_apply_mode"] == "needs_review"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_character_updates_truncates_revision_and_upsert_storage_fields():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = MemoryService(session)  # type: ignore[arg-type]
    service._upsert_character = AsyncMock()

    await service._apply_character_updates(
        "project-id",
        5,
        [{"name": "沈" * 140, "role": "主" * 80, "change_type": "update", "confidence": 0.9}],
    )

    assert service._upsert_character.await_args is not None
    normalized_payload = service._upsert_character.await_args.args[1]
    assert len(normalized_payload["name"]) == 100
    assert len(normalized_payload["role"]) == 50
    assert session.add.call_args is not None
    assert session.add.call_args is not None
    revision = session.add.call_args.args[0]
    assert len(revision.character_name) == 100


@pytest.mark.asyncio
async def test_apply_world_updates_auto_applies_when_confidence_meets_custom_threshold():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = MemoryService(session)  # type: ignore[arg-type]
    service._upsert_world_setting = AsyncMock()

    result = await service._apply_world_updates(
        "project-id",
        5,
        [{"name": "钟楼", "category": "location", "change_type": "update", "confidence": 0.85}],
        confidence_threshold=0.8,
    )

    assert result == {"total": 1, "applied": 1, "needs_review": 0}
    service._upsert_world_setting.assert_awaited_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_world_updates_marks_needs_review_and_still_updates_canonical_resource():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = MemoryService(session)  # type: ignore[arg-type]
    service._upsert_world_setting = AsyncMock()

    result = await service._apply_world_updates(
        "project-id",
        5,
        [{"name": "钟楼", "category": "location", "change_type": "update", "confidence": 0.2, "risk_level": "high"}],
        confidence_threshold=0.8,
    )

    assert result == {"total": 1, "applied": 1, "needs_review": 0}
    service._upsert_world_setting.assert_awaited_once_with(
        "project-id",
        {"name": "钟楼", "category": "location", "change_type": "update", "confidence": 0.2, "risk_level": "high"},
    )
    revision = session.add.call_args.args[0]
    assert revision.apply_mode == "applied"
    assert revision.patch_json["review_required"] is True
    assert revision.patch_json["original_apply_mode"] == "needs_review"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_world_updates_truncates_revision_and_upsert_storage_fields():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = MemoryService(session)  # type: ignore[arg-type]
    service._upsert_world_setting = AsyncMock()

    await service._apply_world_updates(
        "project-id",
        5,
        [{"name": "城" * 260, "category": "设" * 80, "change_type": "update", "confidence": 0.9}],
    )

    assert service._upsert_world_setting.await_args is not None
    normalized_payload = service._upsert_world_setting.await_args.args[1]
    assert len(normalized_payload["name"]) == 200
    assert len(normalized_payload["category"]) == 50
    assert session.add.call_args is not None
    revision = session.add.call_args.args[0]
    assert len(revision.name) == 200
    assert len(revision.category) == 50


@pytest.mark.asyncio
async def test_apply_character_updates_handles_string_confidence_gracefully():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = MemoryService(session)  # type: ignore[arg-type]
    service._upsert_character = AsyncMock()

    result = await service._apply_character_updates(
        "project-id",
        5,
        [{"name": "沈夜", "change_type": "update", "confidence": "high"}],
        confidence_threshold=0.8,
    )

    assert result == {"total": 1, "applied": 1, "needs_review": 0}
    revision = session.add.call_args.args[0]
    assert revision.confidence is None
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_world_updates_handles_string_confidence_gracefully():
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    service = MemoryService(session)  # type: ignore[arg-type]
    service._upsert_world_setting = AsyncMock()

    result = await service._apply_world_updates(
        "project-id",
        5,
        [{"name": "钟楼", "category": "location", "change_type": "update", "confidence": "high"}],
        confidence_threshold=0.8,
    )

    assert result == {"total": 1, "applied": 1, "needs_review": 0}
    revision = session.add.call_args.args[0]
    assert revision.confidence is None
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_distant_memory_source_summaries_preserves_structured_fields():
    summaries = [
        SimpleNamespace(
            chapter_number=1,
            summary_text="第一章摘要",
            key_events=["沈夜拿到密钥"],
            unresolved_threads=["档案失踪"],
            character_changes={"沈夜": "开始怀疑上司"},
            world_changes={"钟楼": "夜间封锁"},
        )
    ]
    session = SimpleNamespace(execute=AsyncMock(return_value=FakeResult(summaries)))
    service = MemoryService(session)  # type: ignore[arg-type]

    result = await service._get_distant_memory_source_summaries("project-id", 5)

    assert result == [
        {
            "chapter_number": 1,
            "summary_text": "第一章摘要",
            "key_events": ["沈夜拿到密钥"],
            "unresolved_threads": ["档案失踪"],
            "character_changes": {"沈夜": "开始怀疑上司"},
            "world_changes": {"钟楼": "夜间封锁"},
        }
    ]


@pytest.mark.asyncio
async def test_generate_summary_normalizes_romance_fields_into_existing_schema_slots():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(
        generate_json=AsyncMock(
            return_value={
                "summary_text": "两人在雨夜对峙后暂时结盟。",
                "key_events": ["雨夜对峙", "决定联手查案"],
                "character_changes": {
                    "沈夜": {"latest_state": "决定先合作", "emotional_shift": "戒备里生出迟疑"}
                },
                "world_changes": {"钟楼": {"description": "夜间封锁更严", "category": "location"}},
                "unresolved_threads": ["匿名短信来源未明"],
                "emotional_tone": "压抑暧昧",
                "time_location": "雨夜/钟楼外",
                "relationship_changes": [
                    {
                        "characters": ["沈夜", "林疏"],
                        "change": "从互相试探转为暂时联手",
                        "status": "暧昧未明",
                        "tension": "都不愿先暴露真实立场",
                        "external_pressure": ["专案组盯梢"],
                        "emotional_shift": "靠近又克制",
                    }
                ],
                "emotional_beats": ["想挽留却没有开口", "转身后仍在回望"],
                "romance_threads": ["沈夜误会林疏要离开"],
                "scene_hook": "林疏收到匿名短信",
                "next_chapter_anchor": "第二天的庆功宴对质",
            }
        )
    )
    service.runtime.get_client = AsyncMock(return_value=client)

    payload = await service._generate_summary("project-id", 7, "正文")

    assert payload["summary_text"] == "两人在雨夜对峙后暂时结盟。"
    assert payload["unresolved_threads"] == [
        "匿名短信来源未明",
        "感情线索：沈夜误会林疏要离开",
        "场景钩子：林疏收到匿名短信",
        "下一章锚点：第二天的庆功宴对质",
    ]
    assert payload["world_changes"] == {"钟楼": {"description": "夜间封锁更严", "category": "location"}}
    assert isinstance(payload["character_changes"]["沈夜"], dict)
    relationships = payload["character_changes"]["沈夜"]["relationships"]
    assert relationships[0]["with"] == ["林疏"]
    assert relationships[0]["status"] == "暧昧未明"
    assert payload["character_changes"]["沈夜"]["emotional_beats"] == ["想挽留却没有开口", "转身后仍在回望"]


@pytest.mark.asyncio
async def test_generate_summary_keeps_backward_compatibility_when_new_fields_missing():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(
        generate_json=AsyncMock(
            return_value={
                "summary_text": "主角完成本章调查。",
                "key_events": ["完成调查"],
                "character_changes": {"主角": "明确了下一步方向"},
                "world_changes": {},
                "unresolved_threads": ["幕后人未明"],
                "emotional_tone": "紧张",
                "time_location": "夜晚/码头",
            }
        )
    )
    service.runtime.get_client = AsyncMock(return_value=client)

    payload = await service._generate_summary("project-id", 3, "正文")

    assert payload == {
        "summary_text": "主角完成本章调查。",
        "key_events": ["完成调查"],
        "character_changes": {"主角": "明确了下一步方向"},
        "world_changes": {},
        "unresolved_threads": ["幕后人未明"],
        "emotional_tone": "紧张",
        "time_location": "夜晚/码头",
    }


@pytest.mark.asyncio
async def test_continuity_precheck_handles_top_level_list_payload_without_crashing():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(generate_json=AsyncMock(return_value=[{"issue": "unexpected"}]))
    service.runtime.get_client = AsyncMock(return_value=client)
    service._get_recent_summaries = AsyncMock(return_value=[])
    service._get_character_payload = AsyncMock(return_value=[])
    service._get_world_payload = AsyncMock(return_value=[])

    payload = await service.continuity_precheck("project-id", 4, "大纲")

    assert payload == {
        "issues": [],
        "continuity_notes": [],
        "suggested_focus": [],
    }


@pytest.mark.asyncio
async def test_generate_summary_falls_back_when_top_level_payload_is_list():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(generate_json=AsyncMock(return_value=["unexpected"]))
    service.runtime.get_client = AsyncMock(return_value=client)

    payload = await service._generate_summary("project-id", 6, "正文内容用于摘要回退")

    assert payload["summary_text"] == "正文内容用于摘要回退"
    assert payload["key_events"] == []
    assert payload["character_changes"] == {}
    assert payload["world_changes"] == {}
    assert payload["unresolved_threads"] == []


@pytest.mark.asyncio
async def test_generate_character_updates_accepts_top_level_list_payload():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(
        generate_json=AsyncMock(
            return_value=[
                {"name": "沈夜", "change_type": "update", "profile_json": {"latest_state": "决定继续追查"}},
                "ignored",
            ]
        )
    )
    service.runtime.get_client = AsyncMock(return_value=client)
    service._get_character_payload = AsyncMock(return_value=[])

    payload = await service._generate_character_updates("project-id", 2, "正文")

    assert payload == [{"name": "沈夜", "change_type": "update", "profile_json": {"latest_state": "决定继续追查"}}]


@pytest.mark.asyncio
async def test_generate_world_updates_accepts_top_level_list_payload():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(
        generate_json=AsyncMock(
            return_value=[
                {"name": "钟楼", "category": "location", "change_type": "update", "setting_json": {"status": "夜间封锁"}},
                123,
            ]
        )
    )
    service.runtime.get_client = AsyncMock(return_value=client)
    service._get_world_payload = AsyncMock(return_value=[])

    payload = await service._generate_world_updates("project-id", 2, "正文")

    assert payload == [{"name": "钟楼", "category": "location", "change_type": "update", "setting_json": {"status": "夜间封锁"}}]


@pytest.mark.asyncio
async def test_compress_distant_memory_falls_back_when_top_level_payload_is_list():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(generate_json=AsyncMock(return_value=["unexpected"]))
    service.runtime.get_client = AsyncMock(return_value=client)
    summaries = [
        {
            "chapter_number": 1,
            "summary_text": "沈夜第一次接触钟楼密钥，并意识到档案室有第二套封锁机制。",
            "key_events": ["拿到密钥"],
            "unresolved_threads": ["第二套封锁机制来源不明"],
            "character_changes": {"沈夜": "开始怀疑上司"},
            "world_changes": {"钟楼": "存在夜间封锁"},
        }
    ]

    compressed = await service._compress_distant_memory("project-id", summaries)

    assert compressed.startswith("第1章：")
    assert "钟楼密钥" in compressed


@pytest.mark.asyncio
async def test_compress_distant_memory_sends_existing_memory_and_new_summaries_to_model():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(generate_json=AsyncMock(return_value={"compressed_memory": "合并后的远期记忆"}))
    service.runtime.get_client = AsyncMock(return_value=client)
    summaries = [
        {
            "chapter_number": 21,
            "summary_text": "旧案线索重新浮出。",
            "key_events": ["重新发现档案"],
            "unresolved_threads": ["幕后人仍未现身"],
            "character_changes": {"沈夜": "决定继续深挖"},
            "world_changes": {},
        }
    ]

    compressed = await service._compress_distant_memory(
        "project-id",
        summaries,
        existing_memory="前20章远期记忆",
    )

    assert compressed == "合并后的远期记忆"
    call = client.generate_json.await_args
    assert call is not None
    payload = json.loads(call.kwargs["user_message"])
    assert payload["existing_memory"] == "前20章远期记忆"
    assert payload["new_summaries"] == summaries


@pytest.mark.asyncio
async def test_compress_distant_memory_fallback_keeps_recent_long_term_signals():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(generate_json=AsyncMock(side_effect=RuntimeError("mock failure")))
    service.runtime.get_client = AsyncMock(return_value=client)
    summaries = [
        {
            "chapter_number": 1,
            "summary_text": "沈夜第一次接触钟楼密钥，并意识到档案室有第二套封锁机制。",
            "key_events": ["拿到密钥"],
            "unresolved_threads": ["第二套封锁机制来源不明"],
            "character_changes": {"沈夜": "开始怀疑上司"},
            "world_changes": {"钟楼": "存在夜间封锁"},
        },
        {
            "chapter_number": 2,
            "summary_text": "林疏现身并暗示档案失踪与钟楼管理员有关。",
            "key_events": ["林疏现身"],
            "unresolved_threads": ["管理员身份未明"],
            "character_changes": {"林疏": "态度暧昧"},
            "world_changes": {},
        },
    ]

    compressed = await service._compress_distant_memory("project-id", summaries)

    assert "第1章：" in compressed
    assert "钟楼密钥" in compressed
    assert "第2章：" in compressed
    assert "管理员有关" in compressed


@pytest.mark.asyncio
async def test_compress_distant_memory_fallback_preserves_existing_memory_prefix():
    service = MemoryService(session=None)  # type: ignore[arg-type]
    client = SimpleNamespace(generate_json=AsyncMock(side_effect=RuntimeError("mock failure")))
    service.runtime.get_client = AsyncMock(return_value=client)
    summaries = [
        {
            "chapter_number": 22,
            "summary_text": "沈夜再次潜入钟楼。",
            "key_events": ["再次潜入"],
            "unresolved_threads": ["钟楼地底仍未探明"],
            "character_changes": {},
            "world_changes": {},
        }
    ]

    compressed = await service._compress_distant_memory(
        "project-id",
        summaries,
        existing_memory="前21章总记忆",
    )

    assert compressed.startswith("前21章总记忆")
    assert "第22章：" in compressed


@pytest.mark.asyncio
async def test_update_distant_memory_cache_uses_incremental_summary_range_when_cache_exists():
    project = SimpleNamespace(distant_memory_cache="旧缓存", distant_memory_updated_chapter=20)
    session = SimpleNamespace(get=AsyncMock(return_value=project), flush=AsyncMock())
    service = MemoryService(session)  # type: ignore[arg-type]
    service._get_distant_memory_source_summaries = AsyncMock()
    service._get_distant_memory_source_summaries_in_range = AsyncMock(
        return_value=[{"chapter_number": 19, "summary_text": "第19章摘要", "key_events": [], "unresolved_threads": [], "character_changes": {}, "world_changes": {}}]
    )
    service._compress_distant_memory = AsyncMock(return_value="新缓存")

    compressed = await service._update_distant_memory_cache("project-id", 22)

    assert compressed == "新缓存"
    service._get_distant_memory_source_summaries.assert_not_awaited()
    service._get_distant_memory_source_summaries_in_range.assert_awaited_once_with(
        "project-id",
        start_chapter=19,
        end_chapter=20,
    )
    service._compress_distant_memory.assert_awaited_once_with(
        "project-id",
        [{"chapter_number": 19, "summary_text": "第19章摘要", "key_events": [], "unresolved_threads": [], "character_changes": {}, "world_changes": {}}],
        existing_memory="旧缓存",
    )
    assert project.distant_memory_cache == "新缓存"
    assert project.distant_memory_updated_chapter == 22
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_character_payload_limits_and_compacts_known_resources():
    characters = [
        SimpleNamespace(name=f"角色{i}", role="配角", profile_json={"bio": "设定" * 300}, is_active=True)
        for i in range(45)
    ]
    session = SimpleNamespace(execute=AsyncMock(return_value=FakeResult(characters)))
    service = MemoryService(session)  # type: ignore[arg-type]

    payload = await service._get_character_payload("project-id")

    assert len(payload) == 40
    assert payload[0]["name"] == "角色0"
    assert len(payload[0]["profile_json"]["bio"]) == service.MEMORY_PROFILE_TEXT_LIMIT


@pytest.mark.asyncio
async def test_get_world_payload_limits_and_compacts_known_resources():
    worlds = [
        SimpleNamespace(category="location", name=f"地点{i}", setting_json={"detail": "规则" * 300})
        for i in range(44)
    ]
    session = SimpleNamespace(execute=AsyncMock(return_value=FakeResult(worlds)))
    service = MemoryService(session)  # type: ignore[arg-type]

    payload = await service._get_world_payload("project-id")

    assert len(payload) == 40
    assert payload[0]["name"] == "地点0"
    assert len(payload[0]["setting_json"]["detail"]) == service.MEMORY_PROFILE_TEXT_LIMIT


@pytest.mark.asyncio
async def test_sync_project_review_warning_sets_soft_warning_when_pending_revisions_exist():
    project = SimpleNamespace(last_error=None)
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    service = MemoryService(session)  # type: ignore[arg-type]
    service.list_character_revisions = AsyncMock(return_value=[SimpleNamespace(apply_mode="needs_review")])
    service.list_world_setting_revisions = AsyncMock(return_value=[SimpleNamespace(apply_mode="applied")])

    pending_count = await service.sync_project_review_warning("project-id")

    assert pending_count == 1
    assert project.last_error == service.MEMORY_REVIEW_WARNING


@pytest.mark.asyncio
async def test_sync_project_review_warning_preserves_real_blocking_error():
    project = SimpleNamespace(last_error="章节达到最大重试次数，项目已暂停。")
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    service = MemoryService(session)  # type: ignore[arg-type]
    service.list_character_revisions = AsyncMock(return_value=[SimpleNamespace(apply_mode="needs_review")])
    service.list_world_setting_revisions = AsyncMock(return_value=[])

    pending_count = await service.sync_project_review_warning("project-id")

    assert pending_count == 1
    assert project.last_error == "章节达到最大重试次数，项目已暂停。"


@pytest.mark.asyncio
async def test_sync_project_review_warning_clears_soft_warning_when_no_pending_revisions():
    project = SimpleNamespace(last_error=MemoryService.MEMORY_REVIEW_WARNING)
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    service = MemoryService(session)  # type: ignore[arg-type]
    service.list_character_revisions = AsyncMock(return_value=[SimpleNamespace(apply_mode="applied")])
    service.list_world_setting_revisions = AsyncMock(return_value=[SimpleNamespace(apply_mode="rejected")])

    pending_count = await service.sync_project_review_warning("project-id")

    assert pending_count == 0
    assert project.last_error is None


@pytest.mark.asyncio
async def test_sync_project_state_after_revision_decision_keeps_project_completed_after_last_pending_resolved():
    project = SimpleNamespace(last_error=MemoryService.MEMORY_REVIEW_WARNING, status="running")
    session = SimpleNamespace(
        get=AsyncMock(return_value=project),
        execute=AsyncMock(return_value=FakeResult([SimpleNamespace(status="passed"), SimpleNamespace(status="passed")])),
    )
    service = MemoryService(session)  # type: ignore[arg-type]
    service.list_character_revisions = AsyncMock(return_value=[SimpleNamespace(apply_mode="applied")])
    service.list_world_setting_revisions = AsyncMock(return_value=[])

    await service._sync_project_state_after_revision_decision("project-id")

    assert project.last_error is None
    assert project.status == "completed"
