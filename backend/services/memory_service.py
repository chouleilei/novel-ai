import asyncio
import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import (
    Character,
    CharacterRevision,
    Chapter,
    ChapterStatus,
    ChapterSummary,
    Project,
    ProjectStatus,
    WorldSetting,
    WorldSettingRevision,
)
from backend.llm.openai_compatible import LLMJSONDecodeError
from backend.prompts.memory import (
    CHARACTER_UPDATE_PROMPT,
    CONTINUITY_CHECK_PROMPT,
    DISTANT_MEMORY_COMPRESSION_PROMPT,
    NEXT_CHAPTER_ANCHOR_PREFIX,
    ROMANCE_THREAD_PREFIX,
    SCENE_HOOK_THREAD_PREFIX,
    SUMMARY_SYSTEM_PROMPT,
    WORLD_SETTING_UPDATE_PROMPT,
)
from backend.services.runtime_service import RuntimeService


class MemoryService:
    MEMORY_REVIEW_WARNING = "检测到高风险设定变更，请前往资源面板“审核提案”处理，生成不中断。"
    SUMMARY_EMOTIONAL_TONE_MAX_LENGTH = 50
    SUMMARY_TIME_LOCATION_MAX_LENGTH = 200
    CHARACTER_NAME_MAX_LENGTH = 100
    CHARACTER_ROLE_MAX_LENGTH = 50
    WORLD_CATEGORY_MAX_LENGTH = 50
    WORLD_NAME_MAX_LENGTH = 200
    MEMORY_KNOWN_CHARACTER_LIMIT = 40
    MEMORY_KNOWN_WORLD_LIMIT = 40
    MEMORY_PROFILE_TEXT_LIMIT = 400
    DISTANT_MEMORY_FALLBACK_MAX_LINES = 10
    SUMMARY_RECOVERABLE_ERRORS = (
        TimeoutError,
        httpx.TimeoutException,
        json.JSONDecodeError,
        LLMJSONDecodeError,
    )

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runtime = RuntimeService(session)

    async def continuity_precheck(self, project_id: uuid.UUID, chapter_number: int, outline_text: str) -> dict[str, list[Any]]:
        client = await self.runtime.get_client(project_id, "memory")
        payload = self._normalize_mapping_payload(
            await client.generate_json(
                system_prompt=CONTINUITY_CHECK_PROMPT,
                user_message=json.dumps(
                    {
                        "chapter_number": chapter_number,
                        "outline_text": outline_text,
                        "recent_summaries": await self._get_recent_summaries(project_id, chapter_number),
                        "characters": await self._get_character_payload(project_id),
                        "world_settings": await self._get_world_payload(project_id),
                    },
                    ensure_ascii=False,
                ),
            )
        )
        return {
            "issues": payload.get("issues", []),
            "continuity_notes": payload.get("continuity_notes", []),
            "suggested_focus": payload.get("suggested_focus", []),
        }

    async def save_summary_and_revisions(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        content: str,
        *,
        confidence_threshold: float = 0.75,
        stage_runner: Callable[[str, Awaitable[Any]], Awaitable[Any]] | None = None,
    ) -> dict[str, object]:
        async def run_stage(stage_name: str, operation: Awaitable[Any]) -> Any:
            if stage_runner is None:
                return await operation
            return await stage_runner(stage_name, operation)

        # 第一阶段：并行生成摘要、角色更新、世界观更新
        summary_task = run_stage(
            "summary_generation",
            self._generate_summary(project_id, chapter_number, content),
        )
        character_task = run_stage(
            "character_updates_generation",
            self._generate_character_updates(project_id, chapter_number, content),
        )
        world_task = run_stage(
            "world_updates_generation",
            self._generate_world_updates(project_id, chapter_number, content),
        )

        results = await asyncio.gather(
            summary_task,
            character_task,
            world_task,
            return_exceptions=True,
        )

        summary_result, character_result_raw, world_result_raw = results

        # 处理 summary 异常：recoverable 则 fallback，否则传播
        if isinstance(summary_result, self.SUMMARY_RECOVERABLE_ERRORS):
            summary_payload = self._build_summary_fallback_payload(content)
        elif isinstance(summary_result, BaseException):
            raise summary_result
        else:
            summary_payload = summary_result

        # 处理 character/world 异常：任何异常都传播
        if isinstance(character_result_raw, BaseException):
            raise character_result_raw
        if isinstance(world_result_raw, BaseException):
            raise world_result_raw

        character_updates = character_result_raw
        world_updates = world_result_raw

        # 处理 fallback（LLM 返回空列表时从 summary 派生）
        if not character_updates:
            character_updates = self._derive_character_updates_from_summary(summary_payload)
        if not world_updates:
            world_updates = self._derive_world_updates_from_summary(summary_payload)

        # 第二阶段：持久化摘要
        await run_stage(
            "summary_persist",
            self._upsert_summary(project_id, chapter_number, summary_payload),
        )

        # 第三阶段：顺序应用角色和世界观更新（共享同一 session，不能并行）
        character_result = await run_stage(
            "character_updates_apply",
            self._apply_character_updates(
                project_id,
                chapter_number,
                character_updates,
                confidence_threshold=confidence_threshold,
            ),
        )

        world_result = await run_stage(
            "world_updates_apply",
            self._apply_world_updates(
                project_id,
                chapter_number,
                world_updates,
                confidence_threshold=confidence_threshold,
            ),
        )

        # 第四阶段：更新远期记忆
        compressed_memory = await run_stage(
            "distant_memory_refresh",
            self._update_distant_memory_cache(project_id, chapter_number),
        )
        return {
            "summary": summary_payload,
            "character_revision_count": character_result["total"],
            "world_revision_count": world_result["total"],
            "applied_revision_count": character_result["applied"] + world_result["applied"],
            "needs_review_count": character_result["needs_review"] + world_result["needs_review"],
            "distant_memory_cache": compressed_memory,
        }

    def _build_summary_fallback_payload(self, content: str) -> dict[str, object]:
        fallback_summary = self._normalize_summary_text(content[:300]) or content[:300]
        return {
            "summary_text": fallback_summary,
            "key_events": [],
            "character_changes": {},
            "world_changes": {},
            "unresolved_threads": [],
            "emotional_tone": "",
            "time_location": "",
        }

    async def list_summaries(self, project_id: uuid.UUID) -> list[ChapterSummary]:
        stmt = (
            select(ChapterSummary)
            .where(ChapterSummary.project_id == project_id)
            .order_by(ChapterSummary.chapter_number.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_characters(self, project_id: uuid.UUID) -> list[Character]:
        stmt = select(Character).where(Character.project_id == project_id).order_by(Character.updated_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def build_character_resources_from_summaries(self, project_id: uuid.UUID) -> list[dict[str, object]]:
        stmt = (
            select(ChapterSummary)
            .where(ChapterSummary.project_id == project_id)
            .order_by(ChapterSummary.chapter_number.asc())
        )
        result = await self.session.execute(stmt)
        merged: dict[str, dict[str, object]] = {}
        for summary in result.scalars():
            for item in self._derive_character_updates_from_summary({"character_changes": summary.character_changes or {}}):
                merged[item["name"]] = {
                    "name": item["name"],
                    "role": item.get("role"),
                    "profile_json": item.get("profile_json", {}),
                    "is_active": item.get("is_active", True),
                }
        return list(merged.values())

    async def list_character_revisions(self, project_id: uuid.UUID) -> list[CharacterRevision]:
        stmt = (
            select(CharacterRevision)
            .where(CharacterRevision.project_id == project_id)
            .order_by(CharacterRevision.created_at.desc(), CharacterRevision.character_name.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_world_settings(self, project_id: uuid.UUID) -> list[WorldSetting]:
        stmt = select(WorldSetting).where(WorldSetting.project_id == project_id).order_by(WorldSetting.updated_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def build_world_setting_resources_from_summaries(self, project_id: uuid.UUID) -> list[dict[str, object]]:
        stmt = (
            select(ChapterSummary)
            .where(ChapterSummary.project_id == project_id)
            .order_by(ChapterSummary.chapter_number.asc())
        )
        result = await self.session.execute(stmt)
        merged: dict[tuple[str, str], dict[str, object]] = {}
        for summary in result.scalars():
            for item in self._derive_world_updates_from_summary({"world_changes": summary.world_changes or {}}):
                key = (item.get("category", "general"), item["name"])
                merged[key] = {
                    "category": item.get("category", "general"),
                    "name": item["name"],
                    "setting_json": item.get("setting_json", {}),
                }
        return list(merged.values())

    async def list_world_setting_revisions(self, project_id: uuid.UUID) -> list[WorldSettingRevision]:
        stmt = (
            select(WorldSettingRevision)
            .where(WorldSettingRevision.project_id == project_id)
            .order_by(WorldSettingRevision.created_at.desc(), WorldSettingRevision.category.asc(), WorldSettingRevision.name.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def apply_character_revision(self, project_id: uuid.UUID, revision_id: uuid.UUID) -> CharacterRevision | None:
        revision = await self.session.get(CharacterRevision, revision_id)
        if revision is None or revision.project_id != project_id:
            return None
        await self._upsert_character(project_id, revision.patch_json)
        revision.apply_mode = "applied"
        await self._sync_project_state_after_revision_decision(project_id)
        await self.session.flush()
        return revision

    async def reject_character_revision(self, project_id: uuid.UUID, revision_id: uuid.UUID) -> CharacterRevision | None:
        revision = await self.session.get(CharacterRevision, revision_id)
        if revision is None or revision.project_id != project_id:
            return None
        revision.apply_mode = "rejected"
        await self._sync_project_state_after_revision_decision(project_id)
        await self.session.flush()
        return revision

    async def apply_world_setting_revision(self, project_id: uuid.UUID, revision_id: uuid.UUID) -> WorldSettingRevision | None:
        revision = await self.session.get(WorldSettingRevision, revision_id)
        if revision is None or revision.project_id != project_id:
            return None
        await self._upsert_world_setting(project_id, revision.patch_json)
        revision.apply_mode = "applied"
        await self._sync_project_state_after_revision_decision(project_id)
        await self.session.flush()
        return revision

    async def reject_world_setting_revision(self, project_id: uuid.UUID, revision_id: uuid.UUID) -> WorldSettingRevision | None:
        revision = await self.session.get(WorldSettingRevision, revision_id)
        if revision is None or revision.project_id != project_id:
            return None
        revision.apply_mode = "rejected"
        await self._sync_project_state_after_revision_decision(project_id)
        await self.session.flush()
        return revision

    async def sync_project_review_warning(self, project_id: uuid.UUID) -> int:
        pending_count = await self._count_pending_revisions(project_id)
        await self._sync_project_review_warning(project_id, pending_count)
        return pending_count

    async def reset_resources_from_chapter(self, project_id: uuid.UUID, chapter_number: int) -> None:
        await self.session.execute(
            delete(ChapterSummary).where(
                ChapterSummary.project_id == project_id,
                ChapterSummary.chapter_number >= chapter_number,
            )
        )
        await self.session.execute(
            delete(CharacterRevision).where(
                CharacterRevision.project_id == project_id,
                CharacterRevision.chapter_number >= chapter_number,
            )
        )
        await self.session.execute(
            delete(WorldSettingRevision).where(
                WorldSettingRevision.project_id == project_id,
                WorldSettingRevision.chapter_number >= chapter_number,
            )
        )
        await self.session.execute(delete(Character).where(Character.project_id == project_id))
        await self.session.execute(delete(WorldSetting).where(WorldSetting.project_id == project_id))

        for item in await self.build_character_resources_from_summaries(project_id):
            await self._upsert_character(project_id, item)

        for item in await self.build_world_setting_resources_from_summaries(project_id):
            await self._upsert_world_setting(project_id, item)

        await self.sync_project_review_warning(project_id)
        await self.session.flush()

    async def _count_pending_revisions(self, project_id: uuid.UUID) -> int:
        pending_character = await self.list_character_revisions(project_id)
        pending_world = await self.list_world_setting_revisions(project_id)
        return sum(1 for item in pending_character if item.apply_mode == "needs_review") + sum(
            1 for item in pending_world if item.apply_mode == "needs_review"
        )

    async def _sync_project_review_warning(self, project_id: uuid.UUID, pending_count: int) -> None:
        project = await self.session.get(Project, project_id)
        if project is None:
            return

        if pending_count > 0:
            if project.last_error in {None, self.MEMORY_REVIEW_WARNING}:
                project.last_error = self.MEMORY_REVIEW_WARNING
            return

        if project.last_error == self.MEMORY_REVIEW_WARNING:
            project.last_error = None

    async def _generate_summary(self, project_id: uuid.UUID, chapter_number: int, content: str) -> dict[str, object]:
        client = await self.runtime.get_client(project_id, "memory")
        payload = self._normalize_mapping_payload(
            await client.generate_json(
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                user_message=f"请为第{chapter_number}章生成结构化摘要：\n{content}",
            )
        )
        summary_text = self._normalize_summary_text(payload.get("summary_text")) or content[:300]
        relationship_changes = self._normalize_relationship_changes(payload.get("relationship_changes"))
        emotional_beats = self._normalize_summary_items(payload.get("emotional_beats"))
        romance_threads = self._normalize_summary_items(payload.get("romance_threads"))
        scene_hook = self._normalize_summary_text(payload.get("scene_hook"))
        next_chapter_anchor = self._normalize_summary_text(payload.get("next_chapter_anchor"))
        character_changes = self._merge_character_and_relationship_signals(
            self._normalize_character_changes(payload.get("character_changes")),
            relationship_changes,
            emotional_beats,
        )
        unresolved_threads = self._merge_unresolved_threads(
            self._normalize_summary_items(payload.get("unresolved_threads")),
            romance_threads,
            scene_hook,
            next_chapter_anchor,
        )
        return {
            "summary_text": summary_text,
            "key_events": self._normalize_summary_items(payload.get("key_events")),
            "character_changes": character_changes,
            "world_changes": self._normalize_world_changes(payload.get("world_changes")),
            "unresolved_threads": unresolved_threads,
            "emotional_tone": self._normalize_summary_text(payload.get("emotional_tone")),
            "time_location": self._normalize_summary_text(payload.get("time_location")),
        }

    async def _upsert_summary(self, project_id: uuid.UUID, chapter_number: int, payload: dict[str, object]) -> None:
        payload = self._normalize_storage_payload("summary", payload)
        stmt = select(ChapterSummary).where(
            ChapterSummary.project_id == project_id,
            ChapterSummary.chapter_number == chapter_number,
        )
        result = await self.session.execute(stmt)
        summary = result.scalar_one_or_none()
        if summary is None:
            summary = ChapterSummary(project_id=project_id, chapter_number=chapter_number, **payload)
            self.session.add(summary)
        else:
            summary.summary_text = payload["summary_text"]
            summary.key_events = payload["key_events"]
            summary.character_changes = payload["character_changes"]
            summary.world_changes = payload["world_changes"]
            summary.unresolved_threads = payload["unresolved_threads"]
            summary.emotional_tone = payload["emotional_tone"]
            summary.time_location = payload["time_location"]
        await self.session.flush()

    async def _generate_character_updates(self, project_id: uuid.UUID, chapter_number: int, content: str) -> list[dict[str, Any]]:
        client = await self.runtime.get_client(project_id, "memory")
        payload = await client.generate_json(
            system_prompt=CHARACTER_UPDATE_PROMPT,
            user_message=json.dumps(
                {
                    "chapter_number": chapter_number,
                    "known_characters": await self._get_character_payload(project_id),
                    "content": content,
                },
                ensure_ascii=False,
            ),
        )
        return self._normalize_update_list_payload(payload)

    async def _generate_world_updates(self, project_id: uuid.UUID, chapter_number: int, content: str) -> list[dict[str, Any]]:
        client = await self.runtime.get_client(project_id, "memory")
        payload = await client.generate_json(
            system_prompt=WORLD_SETTING_UPDATE_PROMPT,
            user_message=json.dumps(
                {
                    "chapter_number": chapter_number,
                    "known_world_settings": await self._get_world_payload(project_id),
                    "content": content,
                },
                ensure_ascii=False,
            ),
        )
        return self._normalize_update_list_payload(payload)

    async def _apply_character_updates(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        updates: list[dict[str, Any]],
        *,
        confidence_threshold: float = 0.75,
    ) -> dict[str, int]:
        count = 0
        applied = 0
        needs_review = 0
        for item in updates:
            normalized_item = self._normalize_storage_payload("character", item)
            apply_mode = self._resolve_apply_mode(
                normalized_item,
                revision_type="character",
                confidence_threshold=confidence_threshold,
            )
            revision = CharacterRevision(
                project_id=project_id,
                chapter_number=chapter_number,
                character_name=normalized_item["name"],
                change_type=normalized_item.get("change_type", "update"),
                patch_json=normalized_item,
                confidence=self._normalize_confidence(normalized_item.get("confidence")),
                apply_mode=apply_mode,
            )
            self.session.add(revision)
            await self._upsert_character(project_id, normalized_item)
            if apply_mode != "auto_safe":
                revision.patch_json = {**normalized_item, "review_required": True, "original_apply_mode": apply_mode}
            revision.apply_mode = "applied"
            applied += 1
            count += 1
        await self.session.flush()
        return {"total": count, "applied": applied, "needs_review": needs_review}

    async def _apply_world_updates(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        updates: list[dict[str, Any]],
        *,
        confidence_threshold: float = 0.75,
    ) -> dict[str, int]:
        count = 0
        applied = 0
        needs_review = 0
        for item in updates:
            normalized_item = self._normalize_storage_payload("world", item)
            apply_mode = self._resolve_apply_mode(
                normalized_item,
                revision_type="world",
                confidence_threshold=confidence_threshold,
            )
            revision = WorldSettingRevision(
                project_id=project_id,
                chapter_number=chapter_number,
                category=normalized_item.get("category", "general"),
                name=normalized_item["name"],
                change_type=normalized_item.get("change_type", "update"),
                patch_json=normalized_item,
                confidence=self._normalize_confidence(normalized_item.get("confidence")),
                apply_mode=apply_mode,
            )
            self.session.add(revision)
            await self._upsert_world_setting(project_id, normalized_item)
            if apply_mode != "auto_safe":
                revision.patch_json = {**normalized_item, "review_required": True, "original_apply_mode": apply_mode}
            revision.apply_mode = "applied"
            applied += 1
            count += 1
        await self.session.flush()
        return {"total": count, "applied": applied, "needs_review": needs_review}

    async def _update_distant_memory_cache(self, project_id: uuid.UUID, chapter_number: int) -> str:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise ValueError("项目不存在")
        coverage_end = chapter_number - 2
        if coverage_end < 1:
            project.distant_memory_cache = None
            project.distant_memory_updated_chapter = chapter_number
            await self.session.flush()
            return ""

        existing_memory = (project.distant_memory_cache or "").strip()
        previous_coverage_end = max(0, int(project.distant_memory_updated_chapter or 0) - 2)

        if not existing_memory or previous_coverage_end <= 0 or previous_coverage_end > coverage_end:
            source_summaries = await self._get_distant_memory_source_summaries(project_id, chapter_number + 1)
            if not source_summaries:
                project.distant_memory_cache = None
                project.distant_memory_updated_chapter = chapter_number
                await self.session.flush()
                return ""
            compressed = await self._compress_distant_memory(project_id, source_summaries)
        else:
            new_summaries = await self._get_distant_memory_source_summaries_in_range(
                project_id,
                start_chapter=previous_coverage_end + 1,
                end_chapter=coverage_end,
            )
            if not new_summaries:
                project.distant_memory_updated_chapter = chapter_number
                await self.session.flush()
                return existing_memory
            compressed = await self._compress_distant_memory(
                project_id,
                new_summaries,
                existing_memory=existing_memory,
            )

        project.distant_memory_cache = compressed
        project.distant_memory_updated_chapter = chapter_number
        await self.session.flush()
        return compressed

    async def _compress_distant_memory(
        self,
        project_id: uuid.UUID,
        summaries: list[dict[str, Any]],
        *,
        existing_memory: str = "",
    ) -> str:
        client = await self.runtime.get_client(project_id, "memory")
        try:
            payload = self._normalize_mapping_payload(
                await client.generate_json(
                    system_prompt=DISTANT_MEMORY_COMPRESSION_PROMPT,
                    user_message=json.dumps(
                        {
                            "existing_memory": existing_memory,
                            "new_summaries": summaries,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            compressed = str(payload.get("compressed_memory", "")).strip()
            if compressed:
                return compressed
        except Exception:  # noqa: BLE001
            pass
        fallback_lines = [
            f"第{item['chapter_number']}章：{item['summary_text'][:120]}"
            for item in summaries[-self.DISTANT_MEMORY_FALLBACK_MAX_LINES :]
        ]
        sections = [section for section in (existing_memory.strip(), "\n".join(fallback_lines).strip()) if section]
        return "\n".join(sections)

    def _resolve_apply_mode(
        self,
        payload: dict[str, Any],
        *,
        revision_type: str,
        confidence_threshold: float = 0.75,
    ) -> str:
        explicit = str(payload.get("apply_mode", "") or "").strip().lower()
        if explicit in {"applied", "rejected", "needs_review"}:
            return explicit
        confidence = self._normalize_confidence(payload.get("confidence"))
        high_risk = (
            self._is_high_risk_character_update(payload)
            if revision_type == "character"
            else self._is_high_risk_world_update(payload)
        )
        if high_risk or (confidence is not None and confidence < confidence_threshold):
            return "needs_review"
        return "auto_safe"

    def _is_high_risk_character_update(self, payload: dict[str, Any]) -> bool:
        change_type = str(payload.get("change_type", "update")).lower()
        if change_type not in {"create", "update"}:
            return True
        if payload.get("is_active") is False:
            return True
        keywords = ("死亡", "失忆", "身份反转", "身份暴露", "背叛", "黑化")
        serialized = json.dumps(payload, ensure_ascii=False)
        return any(keyword in serialized for keyword in keywords)

    def _is_high_risk_world_update(self, payload: dict[str, Any]) -> bool:
        change_type = str(payload.get("change_type", "update")).lower()
        if change_type not in {"create", "update"}:
            return True
        if str(payload.get("risk_level", "")).lower() == "high":
            return True
        keywords = ("推翻", "重写", "改写规则", "世界规则变化", "设定冲突")
        serialized = json.dumps(payload, ensure_ascii=False)
        return any(keyword in serialized for keyword in keywords)

    def _normalize_confidence(self, value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_mapping_payload(self, payload) -> dict[str, Any]:
        if isinstance(payload, Mapping):
            return dict(payload)
        return {}

    def _normalize_update_list_payload(self, payload) -> list[dict[str, Any]]:
        raw_updates = payload
        if isinstance(payload, Mapping):
            raw_updates = payload.get("updates", [])
        if not isinstance(raw_updates, list):
            return []
        return [item for item in raw_updates if isinstance(item, dict)]

    def _normalize_summary_items(self, value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if not isinstance(value, list):
            return []

        normalized: list[str] = []
        for item in value:
            text = ""
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                primary = item.get("event") or item.get("description") or item.get("thread") or item.get("title")
                if isinstance(primary, str):
                    text = primary.strip()
                if not text:
                    significance = item.get("significance") or item.get("comment")
                    if isinstance(significance, str):
                        text = significance.strip()
            elif item is not None:
                text = str(item).strip()

            if text:
                normalized.append(text)
        return normalized

    def _normalize_summary_text(self, value, *, max_length: int | None = None) -> str:
        text = ""
        if value is None:
            text = ""
        elif isinstance(value, str):
            text = value.strip()
        elif isinstance(value, list):
            text = "；".join(self._normalize_summary_items(value))
        elif isinstance(value, dict):
            text = self._normalize_summary_text(
                value.get("summary")
                or value.get("description")
                or value.get("text")
                or value.get("comment")
                or value.get("title")
                or value.get("latest_state")
                or value.get("status")
                or value.get("change")
            )
        else:
            text = str(value).strip()
        if max_length is not None and len(text) > max_length:
            return text[:max_length]
        return text

    def _normalize_storage_payload(self, resource_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if resource_type == "summary":
            return {
                **payload,
                "emotional_tone": self._normalize_summary_text(
                    payload.get("emotional_tone"),
                    max_length=self.SUMMARY_EMOTIONAL_TONE_MAX_LENGTH,
                ),
                "time_location": self._normalize_summary_text(
                    payload.get("time_location"),
                    max_length=self.SUMMARY_TIME_LOCATION_MAX_LENGTH,
                ),
            }

        if resource_type == "character":
            normalized = dict(payload)
            normalized["name"] = self._normalize_summary_text(
                payload.get("name"),
                max_length=self.CHARACTER_NAME_MAX_LENGTH,
            )
            if "role" in payload:
                normalized["role"] = self._normalize_summary_text(
                    payload.get("role"),
                    max_length=self.CHARACTER_ROLE_MAX_LENGTH,
                ) or None
            return normalized

        if resource_type == "world":
            normalized = dict(payload)
            normalized["category"] = self._normalize_summary_text(
                payload.get("category") or "general",
                max_length=self.WORLD_CATEGORY_MAX_LENGTH,
            ) or "general"
            normalized["name"] = self._normalize_summary_text(
                payload.get("name"),
                max_length=self.WORLD_NAME_MAX_LENGTH,
            )
            return normalized

        return dict(payload)

    def _normalize_character_changes(self, value) -> dict[str, str | dict[str, Any]]:
        normalized: dict[str, str | dict[str, Any]] = {}
        if isinstance(value, dict):
            for raw_name, raw_entry in value.items():
                name = self._normalize_summary_text(raw_name, max_length=100)
                if not name:
                    continue
                entry = self._normalize_character_change_value(raw_entry)
                if entry is None:
                    continue
                normalized[name] = entry
            return normalized

        if not isinstance(value, list):
            return normalized

        for item in value:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    normalized[name] = "在最近章节中有明确戏份变化"
                continue
            if not isinstance(item, dict):
                continue
            name = self._normalize_summary_text(
                item.get("name") or item.get("character") or item.get("character_name"),
                max_length=100,
            )
            if not name:
                continue
            entry = self._normalize_character_change_value(item)
            if entry is None:
                continue
            normalized[name] = self._merge_character_change_entries(normalized.get(name), entry)
        return normalized

    def _normalize_character_change_value(self, value) -> str | dict[str, Any] | None:
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, list):
            beats = self._normalize_summary_items(value)
            return {"emotional_beats": beats} if beats else None
        if not isinstance(value, dict):
            text = self._normalize_summary_text(value)
            return text or None

        latest_state = self._normalize_summary_text(
            value.get("latest_state") or value.get("after") or value.get("state_change") or value.get("description")
        )
        emotional_shift = self._normalize_summary_text(value.get("emotional_shift") or value.get("emotion"))
        role = self._normalize_summary_text(value.get("role"), max_length=50)
        notes = self._normalize_summary_text(value.get("notes") or value.get("comment"))
        emotional_beats = self._normalize_summary_items(value.get("emotional_beats"))
        external_pressures = self._normalize_summary_items(
            value.get("external_pressures") or value.get("external_pressure")
        )
        relationships = self._normalize_character_relationships(value.get("relationships"))

        normalized: dict[str, object] = {}
        if latest_state:
            normalized["latest_state"] = latest_state
        if emotional_shift:
            normalized["emotional_shift"] = emotional_shift
        if role:
            normalized["role"] = role
        if notes:
            normalized["notes"] = notes
        if emotional_beats:
            normalized["emotional_beats"] = emotional_beats
        if external_pressures:
            normalized["external_pressures"] = external_pressures
        if relationships:
            normalized["relationships"] = relationships

        if not normalized:
            return None
        if set(normalized.keys()) == {"latest_state"}:
            return str(normalized["latest_state"])
        return normalized

    def _normalize_character_relationships(self, value) -> list[dict[str, Any]]:
        if value is None:
            return []
        raw_items = value if isinstance(value, list) else [value]
        normalized: list[dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append({"status": text})
                continue
            if not isinstance(item, dict):
                continue
            relation: dict[str, object] = {}
            counterparts = self._normalize_relation_participants(item.get("with") or item.get("characters") or item.get("targets"))
            if counterparts:
                relation["with"] = counterparts
            for key in ("status", "change", "tension", "emotional_shift", "notes"):
                text = self._normalize_summary_text(item.get(key))
                if text:
                    relation[key] = text
            external_pressures = self._normalize_summary_items(
                item.get("external_pressures") or item.get("external_pressure")
            )
            if external_pressures:
                relation["external_pressures"] = external_pressures
            if relation:
                normalized.append(relation)
        return normalized

    def _normalize_relation_participants(self, value) -> list[str]:
        names: list[str] = []
        if value is None:
            return names
        if isinstance(value, str):
            normalized = (
                value.replace("，", "、")
                .replace(",", "、")
                .replace("/", "、")
                .replace("&", "、")
                .replace("与", "、")
            )
            names.extend(part.strip() for part in normalized.split("、") if part.strip())
        elif isinstance(value, list):
            for item in value:
                text = self._normalize_summary_text(item, max_length=100)
                if text:
                    names.append(text)
        elif isinstance(value, dict):
            for key in ("name", "character", "character_name"):
                text = self._normalize_summary_text(value.get(key), max_length=100)
                if text:
                    names.append(text)
                    break
        return self._dedupe_texts(names)

    def _normalize_relationship_changes(self, value) -> list[dict[str, Any]]:
        if value is None:
            return []
        raw_items = value if isinstance(value, list) else [value]
        normalized: list[dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append({"change": text})
                continue
            if not isinstance(item, dict):
                continue

            characters = self._normalize_relation_participants(item.get("characters"))
            if not characters:
                for key in ("character_a", "character_b", "source", "target"):
                    text = self._normalize_summary_text(item.get(key), max_length=100)
                    if text:
                        characters.append(text)
                characters = self._dedupe_texts(characters)

            relation: dict[str, object] = {}
            if characters:
                relation["characters"] = characters
            for key in ("change", "status", "tension", "emotional_shift", "notes"):
                text = self._normalize_summary_text(item.get(key))
                if text:
                    relation[key] = text
            external_pressure = self._normalize_summary_items(
                item.get("external_pressure") or item.get("external_pressures")
            )
            if external_pressure:
                relation["external_pressure"] = external_pressure
            if not relation:
                fallback = self._normalize_summary_text(item)
                if fallback:
                    relation["change"] = fallback
            if relation:
                normalized.append(relation)
        return normalized

    def _normalize_world_changes(self, value) -> dict[str, str | dict[str, Any]]:
        normalized: dict[str, str | dict[str, Any]] = {}
        if isinstance(value, dict):
            for raw_name, raw_entry in value.items():
                name = self._normalize_summary_text(raw_name, max_length=200)
                if not name:
                    continue
                entry = self._normalize_world_change_value(raw_entry)
                if entry is None:
                    continue
                normalized[name] = entry
            return normalized

        if not isinstance(value, list):
            return normalized

        for item in value:
            if not isinstance(item, dict):
                continue
            name = self._normalize_summary_text(item.get("name") or item.get("title"), max_length=200)
            if not name:
                continue
            entry = self._normalize_world_change_value(item)
            if entry is None:
                continue
            normalized[name] = entry
        return normalized

    def _normalize_world_change_value(self, value) -> str | dict[str, Any] | None:
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if not isinstance(value, dict):
            text = self._normalize_summary_text(value)
            return text or None
        description = self._normalize_summary_text(
            value.get("description") or value.get("latest_change") or value.get("summary") or value.get("status")
        )
        category = self._normalize_summary_text(value.get("category"), max_length=50)
        notes = self._normalize_summary_text(value.get("notes") or value.get("comment"))
        normalized: dict[str, object] = {}
        if description:
            normalized["description"] = description
        if category:
            normalized["category"] = category
        if notes:
            normalized["notes"] = notes
        if not normalized:
            return None
        if set(normalized.keys()) == {"description"}:
            return str(normalized["description"])
        return normalized

    def _merge_character_and_relationship_signals(
        self,
        character_changes: dict[str, str | dict[str, Any]],
        relationship_changes: list[dict[str, Any]],
        emotional_beats: list[str],
    ) -> dict[str, str | dict[str, Any]]:
        merged = dict(character_changes)
        relationship_names: list[str] = []
        for relation in relationship_changes:
            characters = [name for name in relation.get("characters", []) if isinstance(name, str) and name.strip()]
            if not characters:
                continue
            relationship_names.extend(characters)
            shared_payload = {key: value for key, value in relation.items() if key != "characters"}
            for name in characters:
                entry = self._coerce_character_change_entry(merged.get(name))
                counterparts = [item for item in characters if item != name]
                relation_payload = dict(shared_payload)
                if counterparts:
                    relation_payload["with"] = counterparts
                if relation_payload:
                    existing_relationships = entry.get("relationships")
                    relationships = list(existing_relationships) if isinstance(existing_relationships, list) else []
                    self._append_unique_list_item(relationships, relation_payload)
                    entry["relationships"] = relationships
                merged[name] = self._finalize_character_change_entry(entry)

        beat_targets = self._dedupe_texts(relationship_names or list(merged.keys())[:3])
        if emotional_beats and beat_targets:
            for name in beat_targets:
                entry = self._coerce_character_change_entry(merged.get(name))
                existing_beats = entry.get("emotional_beats")
                beats = list(existing_beats) if isinstance(existing_beats, list) else []
                for item in emotional_beats:
                    self._append_unique_list_item(beats, item)
                entry["emotional_beats"] = beats
                merged[name] = self._finalize_character_change_entry(entry)
        return merged

    def _merge_unresolved_threads(
        self,
        unresolved_threads: list[str],
        romance_threads: list[str],
        scene_hook: str,
        next_chapter_anchor: str,
    ) -> list[str]:
        merged = list(unresolved_threads)
        for item in romance_threads:
            text = item.strip()
            if not text:
                continue
            prefixed = text if text.startswith(ROMANCE_THREAD_PREFIX) else f"{ROMANCE_THREAD_PREFIX}{text}"
            self._append_unique_list_item(merged, prefixed)
        if scene_hook:
            self._append_unique_list_item(merged, f"{SCENE_HOOK_THREAD_PREFIX}{scene_hook}")
        if next_chapter_anchor:
            self._append_unique_list_item(merged, f"{NEXT_CHAPTER_ANCHOR_PREFIX}{next_chapter_anchor}")
        return merged[:12]

    def _coerce_character_change_entry(self, value) -> dict[str, object]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and value.strip():
            return {"latest_state": value.strip()}
        return {}

    def _merge_character_change_entries(self, current, incoming) -> str | dict[str, Any]:
        entry = self._coerce_character_change_entry(current)
        incoming_entry = self._coerce_character_change_entry(incoming)
        if not entry:
            return incoming if isinstance(incoming, (str, dict)) else self._finalize_character_change_entry(incoming_entry)
        for key in ("latest_state", "emotional_shift", "role", "notes"):
            if incoming_entry.get(key) and not entry.get(key):
                entry[key] = incoming_entry[key]
        for key in ("relationships", "emotional_beats", "external_pressures"):
            existing_raw = entry.get(key)
            existing_items = list(existing_raw) if isinstance(existing_raw, list) else []
            incoming_raw = incoming_entry.get(key)
            incoming_items = list(incoming_raw) if isinstance(incoming_raw, list) else []
            for item in incoming_items:
                self._append_unique_list_item(existing_items, item)
            if existing_items:
                entry[key] = existing_items
        return self._finalize_character_change_entry(entry)

    def _finalize_character_change_entry(self, entry: dict[str, object]) -> str | dict[str, Any]:
        normalized = {key: value for key, value in entry.items() if value not in (None, "", [], {})}
        if set(normalized.keys()) == {"latest_state"}:
            return str(normalized["latest_state"])
        return normalized

    def _append_unique_list_item(self, items: list[Any], candidate: Any) -> None:
        if candidate in (None, "", [], {}):
            return
        marker = json.dumps(candidate, ensure_ascii=False, sort_keys=True) if isinstance(candidate, dict) else str(candidate)
        if any(
            (json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)) == marker
            for item in items
        ):
            return
        items.append(candidate)

    def _dedupe_texts(self, items: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = item.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped

    def _derive_character_updates_from_summary(self, summary_payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_changes = summary_payload.get("character_changes")
        candidates: list[dict[str, Any]] = []

        if isinstance(raw_changes, dict):
            for name, description in raw_changes.items():
                if not isinstance(name, str) or not name.strip():
                    continue
                entry = self._coerce_character_change_entry(description)
                profile_json = {
                    key: value
                    for key, value in {
                        "latest_state": self._normalize_summary_text(entry.get("latest_state")),
                        "emotional_shift": self._normalize_summary_text(entry.get("emotional_shift")),
                        "relationships": entry.get("relationships") if isinstance(entry.get("relationships"), list) else None,
                        "emotional_beats": entry.get("emotional_beats") if isinstance(entry.get("emotional_beats"), list) else None,
                        "external_pressures": entry.get("external_pressures") if isinstance(entry.get("external_pressures"), list) else None,
                    }.items()
                    if value not in (None, "", [], {})
                }
                latest_state = self._normalize_summary_text(entry.get("latest_state"))
                notes = self._normalize_summary_text(entry.get("notes") or latest_state)
                candidates.append(
                    {
                        "name": name.strip(),
                        "role": self._normalize_summary_text(entry.get("role"), max_length=50) or None,
                        "change_type": "update",
                        "profile_json": profile_json or {"latest_state": latest_state or "在最近章节中有明确戏份变化"},
                        "notes": notes,
                        "confidence": 0.82,
                    }
                )
        elif isinstance(raw_changes, list):
            for item in raw_changes:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("character")
                if not isinstance(name, str) or not name.strip():
                    continue
                latest_state = item.get("after") or item.get("state_change") or item.get("description")
                profile_json = {
                    key: value
                    for key, value in {
                        "latest_state": latest_state,
                        "before": item.get("before"),
                        "state_change": item.get("state_change"),
                    }.items()
                    if isinstance(value, str) and value.strip()
                }
                candidates.append(
                    {
                        "name": name.strip(),
                        "role": item.get("role"),
                        "change_type": "update",
                        "profile_json": profile_json or {"latest_state": "在最近章节中有明确戏份变化"},
                        "notes": item.get("description") or item.get("state_change") or "",
                        "confidence": 0.82,
                    }
                )

        deduplicated: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for item in candidates:
            name = item["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            deduplicated.append(item)
        return deduplicated

    def _derive_world_updates_from_summary(self, summary_payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_changes = summary_payload.get("world_changes")
        if isinstance(raw_changes, dict):
            updates: list[dict[str, Any]] = []
            for name, description in raw_changes.items():
                if not isinstance(name, str) or not name.strip():
                    continue
                entry = self._normalize_world_change_value(description)
                if entry is None:
                    continue
                setting_json = entry if isinstance(entry, dict) else {"description": entry}
                text = self._normalize_summary_text(
                    setting_json.get("description") if isinstance(setting_json, dict) else entry
                )
                updates.append(
                    {
                        "category": self._normalize_summary_text(setting_json.get("category"), max_length=50) or "general",
                        "name": name.strip(),
                        "change_type": "update",
                        "setting_json": setting_json or {"description": text or "在最近章节中出现了明确设定变化"},
                        "description": text,
                        "confidence": 0.8,
                    }
                )
            return updates
        return []

    async def _upsert_character(self, project_id: uuid.UUID, payload: dict[str, Any]) -> None:
        payload = self._normalize_storage_payload("character", payload)
        stmt = select(Character).where(Character.project_id == project_id, Character.name == payload["name"])
        result = await self.session.execute(stmt)
        character = result.scalar_one_or_none()
        profile = payload.get("profile_json") or {
            "name": payload["name"],
            "role": payload.get("role"),
            "notes": payload.get("notes", ""),
        }
        if character is None:
            character = Character(
                project_id=project_id,
                name=payload["name"],
                role=payload.get("role"),
                profile_json=profile,
                is_active=True,
            )
            self.session.add(character)
        else:
            character.role = payload.get("role", character.role)
            character.profile_json = {**character.profile_json, **profile}
            character.is_active = payload.get("is_active", character.is_active)

    async def _upsert_world_setting(self, project_id: uuid.UUID, payload: dict[str, Any]) -> None:
        payload = self._normalize_storage_payload("world", payload)
        stmt = select(WorldSetting).where(
            WorldSetting.project_id == project_id,
            WorldSetting.category == payload.get("category", "general"),
            WorldSetting.name == payload["name"],
        )
        result = await self.session.execute(stmt)
        setting = result.scalar_one_or_none()
        setting_json = payload.get("setting_json") or {"description": payload.get("description", "")}
        if setting is None:
            setting = WorldSetting(
                project_id=project_id,
                category=payload.get("category", "general"),
                name=payload["name"],
                setting_json=setting_json,
            )
            self.session.add(setting)
        else:
            setting.setting_json = {**setting.setting_json, **setting_json}

    async def _get_recent_summaries(self, project_id: uuid.UUID, chapter_number: int) -> list[dict[str, Any]]:
        stmt = (
            select(ChapterSummary)
            .where(
                ChapterSummary.project_id == project_id,
                ChapterSummary.chapter_number < chapter_number,
            )
            .order_by(ChapterSummary.chapter_number.desc())
            .limit(5)
        )
        result = await self.session.execute(stmt)
        return [
            {
                "chapter_number": item.chapter_number,
                "summary_text": item.summary_text,
            }
            for item in result.scalars()
        ]

    async def _get_distant_memory_source_summaries(self, project_id: uuid.UUID, chapter_number: int) -> list[dict[str, Any]]:
        if chapter_number <= 3:
            return []
        stmt = (
            select(ChapterSummary)
            .where(
                ChapterSummary.project_id == project_id,
                ChapterSummary.chapter_number < chapter_number - 2,
            )
            .order_by(ChapterSummary.chapter_number.asc())
        )
        result = await self.session.execute(stmt)
        return [
            {
                "chapter_number": item.chapter_number,
                "summary_text": item.summary_text,
                "key_events": item.key_events or [],
                "unresolved_threads": item.unresolved_threads or [],
                "character_changes": item.character_changes or {},
                "world_changes": item.world_changes or {},
            }
            for item in result.scalars()
        ]

    async def _get_distant_memory_source_summaries_in_range(
        self,
        project_id: uuid.UUID,
        *,
        start_chapter: int,
        end_chapter: int,
    ) -> list[dict[str, Any]]:
        if start_chapter > end_chapter:
            return []
        stmt = (
            select(ChapterSummary)
            .where(
                ChapterSummary.project_id == project_id,
                ChapterSummary.chapter_number >= start_chapter,
                ChapterSummary.chapter_number <= end_chapter,
            )
            .order_by(ChapterSummary.chapter_number.asc())
        )
        result = await self.session.execute(stmt)
        return [
            {
                "chapter_number": item.chapter_number,
                "summary_text": item.summary_text,
                "key_events": item.key_events or [],
                "unresolved_threads": item.unresolved_threads or [],
                "character_changes": item.character_changes or {},
                "world_changes": item.world_changes or {},
            }
            for item in result.scalars()
        ]

    async def _sync_project_state_after_revision_decision(self, project_id: uuid.UUID) -> None:
        pending_count = await self.sync_project_review_warning(project_id)
        if pending_count > 0:
            return

        project = await self.session.get(Project, project_id)
        if project is None:
            return

        stmt = select(Chapter).where(Chapter.project_id == project_id)
        result = await self.session.execute(stmt)
        chapters = list(result.scalars())
        if chapters and all(item.status == ChapterStatus.PASSED.value for item in chapters):
            project.status = ProjectStatus.COMPLETED.value

    async def _get_character_payload(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        stmt = (
            select(Character)
            .where(Character.project_id == project_id)
            .order_by(Character.updated_at.desc())
            .limit(self.MEMORY_KNOWN_CHARACTER_LIMIT)
        )
        result = await self.session.execute(stmt)
        payload = [
            {
                "name": item.name,
                "role": item.role,
                "profile_json": self._compact_payload_value(item.profile_json),
                "is_active": item.is_active,
            }
            for item in result.scalars()
        ][: self.MEMORY_KNOWN_CHARACTER_LIMIT]
        if payload:
            return payload
        fallback = await self.build_character_resources_from_summaries(project_id)
        return fallback[: self.MEMORY_KNOWN_CHARACTER_LIMIT]

    async def _get_world_payload(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        stmt = (
            select(WorldSetting)
            .where(WorldSetting.project_id == project_id)
            .order_by(WorldSetting.updated_at.desc())
            .limit(self.MEMORY_KNOWN_WORLD_LIMIT)
        )
        result = await self.session.execute(stmt)
        payload = [
            {
                "category": item.category,
                "name": item.name,
                "setting_json": self._compact_payload_value(item.setting_json),
            }
            for item in result.scalars()
        ][: self.MEMORY_KNOWN_WORLD_LIMIT]
        if payload:
            return payload
        fallback = await self.build_world_setting_resources_from_summaries(project_id)
        return fallback[: self.MEMORY_KNOWN_WORLD_LIMIT]

    def _compact_payload_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return value[: self.MEMORY_PROFILE_TEXT_LIMIT]
        if isinstance(value, list):
            return [self._compact_payload_value(item) for item in value[:8]]
        if isinstance(value, dict):
            compacted: dict[str, Any] = {}
            for key, item in list(value.items())[:12]:
                compacted[str(key)] = self._compact_payload_value(item)
            return compacted
        return value
