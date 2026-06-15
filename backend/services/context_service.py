import math
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import (
    Chapter,
    ChapterAttempt,
    ChapterOutline,
    ChapterReview,
    ChapterStatus,
    ChapterSummary,
    Character,
    Project,
    WorldSetting,
)
from backend.prompts.memory import (
    NEXT_CHAPTER_ANCHOR_PREFIX,
    ROMANCE_THREAD_PREFIX,
    SCENE_HOOK_THREAD_PREFIX,
    SUMMARY_SCENE_HOOK_LABEL,
)


class ContextService:
    CONTINUATION_PREFIX_MAX_CHARS = 6000
    WRITER_TARGET_INPUT_TOKENS = 64000
    WRITER_HARD_LIMIT_TOKENS = 96000
    CRITIC_TARGET_INPUT_TOKENS = 32000
    CRITIC_HARD_LIMIT_TOKENS = 48000
    TARGET_INPUT_TOKENS = WRITER_TARGET_INPUT_TOKENS
    HARD_LIMIT_TOKENS = WRITER_HARD_LIMIT_TOKENS
    LAYER_LIMITS = {
        "effective_system_prompt": 3500,
        "current_outline": 2200,
        "retry_feedback": 1800,
        "precheck_notes": 1600,
        "cross_chapter_review_insights": 1200,
        "relevant_characters": 7000,
        "relevant_world_settings": 2500,
        "previous_chapter_tail": 10000,
        "recent_chapter_cards": 14000,
        "relationship_state_memory": 12000,
        "review_history": 1400,
        "recent_full_text": 10000,
        "recent_summaries": 3000,
        "distant_memory": 12000,
        "critic_distant_memory": 1800,
        "global_prompt_digest": 900,
        "rush_previous_chapter_contents": 70000,
    }
    REDUCTION_ORDER = [
        "critic_distant_memory",
        "relevant_world_settings",
        "global_prompt_digest",
        "cross_chapter_review_insights",
        "review_history",
        "distant_memory",
        "relevant_characters",
        "recent_summaries",
        "recent_full_text",
        "recent_chapter_cards",
        "relationship_state_memory",
        "previous_chapter_tail",
    ]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build_writer_context(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        effective_system_prompt: str,
        retry_feedback: str = "",
        precheck: dict | None = None,
        retry_guidance: dict | None = None,
        continuation_prefix: str = "",
        budget_overrides: dict[str, int] | None = None,
    ) -> dict:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise ValueError("项目不存在")
        outline = await self._get_outline(project_id, chapter_number)
        retry_context = retry_guidance or await self.build_retry_guidance(
            project_id=project_id,
            chapter_number=chapter_number,
            fallback_feedback=retry_feedback,
        )
        characters = await self._get_relevant_characters(project_id, chapter_number, outline.outline_text)
        world_settings = await self._get_relevant_world_settings(project_id, outline.outline_text)
        recent_context = await self._get_recent_context(project_id, chapter_number)
        distant_memory = await self._get_distant_memory(project_id, chapter_number, outline.outline_text)
        cross_chapter_review_insights = await self._build_cross_chapter_review_insights(project_id, chapter_number)

        layers = [
            ("effective_system_prompt", effective_system_prompt, True),
            ("current_outline", outline.outline_text, True),
            ("retry_feedback", retry_context.get("writer_feedback", ""), True),
            ("precheck_notes", self._render_precheck(precheck), True),
            ("cross_chapter_review_insights", cross_chapter_review_insights, False),
            ("relevant_characters", characters, False),
            ("relevant_world_settings", world_settings, False),
            ("previous_chapter_tail", recent_context["previous_chapter_tail"], False),
            ("recent_chapter_cards", recent_context["recent_chapter_cards"], False),
            ("relationship_state_memory", recent_context["relationship_state_memory"], False),
            ("distant_memory", distant_memory, False),
            ("global_prompt_digest", self._build_global_prompt_digest(project.global_prompt), False),
        ]
        writer_budget = budget_overrides or {}
        fitted_layers = self._fit_to_budget(
            layers,
            target_tokens=writer_budget.get("target_tokens", self.WRITER_TARGET_INPUT_TOKENS),
            hard_limit_tokens=writer_budget.get("hard_limit_tokens", self.WRITER_HARD_LIMIT_TOKENS),
        )
        user_message = self._render_writer_message(
            chapter_number,
            fitted_layers,
            continuation_prefix=continuation_prefix,
        )
        return {
            "user_message": user_message,
            "token_usage": self.estimate_tokens(user_message),
            "layers": {name: text for name, text in fitted_layers},
        }

    async def build_rush_writer_context(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        previous_chapter_count: int,
        continuation_prefix: str = "",
        budget_overrides: dict[str, int] | None = None,
    ) -> dict:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise ValueError("项目不存在")
        outline = await self._get_outline(project_id, chapter_number)
        previous_content = await self._get_previous_chapter_contents(
            project_id,
            chapter_number,
            max_count=previous_chapter_count,
        )
        layers = [
            ("book_title", project.title, True),
            ("genre", project.genre or "未设置", True),
            ("global_prompt", project.global_prompt, True),
            ("rush_previous_chapter_contents", previous_content, False),
            ("current_outline", outline.outline_text, True),
            (
                "rush_instruction",
                f"你现在要创作第 {chapter_number} 章，本章的主要情节为：{outline.outline_text}",
                True,
            ),
        ]
        writer_budget = budget_overrides or {}
        fitted_layers = self._fit_to_budget(
            layers,
            target_tokens=writer_budget.get("target_tokens", writer_budget.get("hard_limit_tokens", self.WRITER_HARD_LIMIT_TOKENS)),
            hard_limit_tokens=writer_budget.get("hard_limit_tokens", self.WRITER_HARD_LIMIT_TOKENS),
        )
        user_message = self._render_rush_writer_message(
            chapter_number,
            fitted_layers,
            continuation_prefix=continuation_prefix,
        )
        return {
            "user_message": user_message,
            "token_usage": self.estimate_tokens(user_message),
            "layers": {name: text for name, text in fitted_layers},
        }

    async def build_critic_context(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        effective_system_prompt: str,
        content: str,
        budget_overrides: dict[str, int] | None = None,
    ) -> str:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise ValueError("项目不存在")
        outline = await self._get_outline(project_id, chapter_number)
        characters = await self._get_relevant_characters(project_id, chapter_number, outline.outline_text)
        world_settings = await self._get_relevant_world_settings(project_id, outline.outline_text)
        recent_summaries = await self._render_recent_summaries(project_id, chapter_number)
        previous_chapter_tail = await self._get_previous_chapter_tail(project_id, chapter_number)
        retry_context = await self.build_retry_guidance(project_id=project_id, chapter_number=chapter_number)
        cross_chapter_review_insights = await self._build_cross_chapter_review_insights(project_id, chapter_number)
        review_history = self._render_review_history(
            retry_context.get("critic_review_history", ""),
            cross_chapter_review_insights,
        )
        distant_memory = await self._get_distant_memory(project_id, chapter_number, outline.outline_text)

        layers = [
            ("critic_global_prompt", project.global_prompt, True),
            ("effective_system_prompt", effective_system_prompt, True),
            ("current_outline", outline.outline_text, True),
            ("relevant_characters", characters, False),
            ("relevant_world_settings", world_settings, False),
            ("recent_summaries", recent_summaries, False),
            ("previous_chapter_tail", previous_chapter_tail, False),
            ("review_history", review_history, False),
            ("critic_distant_memory", distant_memory if chapter_number > 4 else "", False),
            ("critic_content", content, True),
        ]
        critic_budget = budget_overrides or {}
        fitted_layers = self._fit_to_budget(
            layers,
            target_tokens=critic_budget.get("target_tokens", self.CRITIC_TARGET_INPUT_TOKENS),
            hard_limit_tokens=critic_budget.get("hard_limit_tokens", self.CRITIC_HARD_LIMIT_TOKENS),
        )
        return self._render_critic_message(fitted_layers)

    def estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return math.ceil(len(text) / 1.8)

    async def build_retry_guidance(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        fallback_feedback: str = "",
    ) -> dict:
        failed_reviews = await self._get_failed_reviews(project_id, chapter_number)
        aggregated = self._aggregate_retry_reviews(failed_reviews, fallback_feedback)
        rendered_feedback = self._render_retry_feedback(aggregated)
        return {
            "writer_feedback": rendered_feedback,
            "critic_review_history": rendered_feedback,
            "prompt_retry_info": {
                "recent_failed_attempts": aggregated["recent_failed_attempts"],
                "last_failed_attempt_no": aggregated["last_failed_attempt_no"],
                "last_failed_score": aggregated["last_failed_score"],
                "recent_blocking_issues": aggregated["recent_blocking_issues"],
                "recent_uncovered_outline_points": aggregated["recent_uncovered_outline_points"],
                "recent_violated_instructions": aggregated["recent_violated_instructions"],
                "recent_improvement_suggestions": aggregated["recent_improvement_suggestions"],
                "failed_reasons": aggregated["failed_reasons"],
                "retry_brief": rendered_feedback,
            },
        }

    async def _get_outline(self, project_id: uuid.UUID, chapter_number: int) -> ChapterOutline:
        stmt = select(ChapterOutline).where(
            ChapterOutline.project_id == project_id,
            ChapterOutline.chapter_number == chapter_number,
        )
        result = await self.session.execute(stmt)
        outline = result.scalar_one_or_none()
        if outline is None:
            raise ValueError("章节大纲不存在")
        return outline

    async def _get_relevant_characters(self, project_id: uuid.UUID, chapter_number: int, outline_text: str) -> str:
        stmt = (
            select(Character)
            .where(Character.project_id == project_id, Character.is_active.is_(True))
            .order_by(Character.updated_at.desc())
        )
        result = await self.session.execute(stmt)
        items = list(result.scalars())
        matched = [item for item in items if item.name in outline_text]
        selected = matched or items[:6]
        return "\n".join(f"{item.name}: {item.profile_json}" for item in selected)

    async def _get_relevant_world_settings(self, project_id: uuid.UUID, outline_text: str) -> str:
        stmt = (
            select(WorldSetting)
            .where(WorldSetting.project_id == project_id)
            .order_by(WorldSetting.updated_at.desc())
        )
        result = await self.session.execute(stmt)
        items = list(result.scalars())
        matched = [item for item in items if item.name in outline_text or item.category in outline_text]
        selected = matched or items[:5]
        return "\n".join(f"{item.category}/{item.name}: {item.setting_json}" for item in selected)

    async def _get_recent_context(self, project_id: uuid.UUID, chapter_number: int) -> dict[str, str]:
        previous_tail = await self._get_previous_chapter_tail(project_id, chapter_number, max_chars=12000)
        recent_summaries = await self._get_recent_summaries_for_context(project_id, chapter_number)
        return {
            "previous_chapter_tail": previous_tail,
            "recent_chapter_cards": self._render_recent_chapter_cards(recent_summaries),
            "relationship_state_memory": self._build_relationship_state_memory(recent_summaries),
        }

    async def _get_previous_chapter_tail(self, project_id: uuid.UUID, chapter_number: int, max_chars: int = 1800) -> str:
        prev = chapter_number - 1
        if prev < 1:
            return ""
        chapter = await self.session.scalar(
            select(Chapter).where(Chapter.project_id == project_id, Chapter.chapter_number == prev)
        )
        if chapter is None or not chapter.final_content:
            return ""
        return f"第{prev}章末尾\n{chapter.final_content[-max_chars:]}"

    async def _get_previous_chapter_contents(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        *,
        max_count: int,
    ) -> str:
        if max_count <= 0 or chapter_number <= 1:
            return ""
        stmt = (
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number < chapter_number,
                Chapter.status == ChapterStatus.PASSED.value,
                Chapter.final_content.is_not(None),
            )
            .order_by(Chapter.chapter_number.desc())
            .limit(max_count)
        )
        result = await self.session.execute(stmt)
        chapters = list(reversed(list(result.scalars())))
        return "\n\n".join(
            f"第{chapter.chapter_number}章正文\n{chapter.final_content.strip()}"
            for chapter in chapters
            if chapter.final_content and chapter.final_content.strip()
        )

    async def _get_recent_summaries_for_context(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        *,
        limit: int = 5,
    ) -> list[ChapterSummary]:
        stmt = (
            select(ChapterSummary)
            .where(
                ChapterSummary.project_id == project_id,
                ChapterSummary.chapter_number < chapter_number,
            )
            .order_by(ChapterSummary.chapter_number.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(reversed(list(result.scalars())))

    def _render_recent_chapter_cards(self, summaries: list[ChapterSummary]) -> str:
        cards: list[str] = []
        for summary in summaries:
            summary_text = (summary.summary_text or "").strip()
            lines: list[str] = [f"第{summary.chapter_number}章"]
            if summary_text:
                lines.append(f"摘要：{summary_text}")
            if summary.emotional_tone:
                lines.append(f"情绪：{summary.emotional_tone}")
            if summary.time_location:
                lines.append(f"时空/场景：{summary.time_location}")
            scene_hook = self._extract_scene_hook(summary)
            if scene_hook:
                lines.append(f"场景钩子：{scene_hook}")
            next_anchor = self._extract_next_chapter_anchor(summary)
            if next_anchor:
                lines.append(f"下一章锚点：{next_anchor}")
            threads = self._extract_labeled_summary_items(summary.unresolved_threads, prefix=ROMANCE_THREAD_PREFIX, limit=2)
            if threads:
                lines.append(ROMANCE_THREAD_PREFIX + "；".join(threads))
            general_threads = self._extract_general_unresolved_threads(summary.unresolved_threads, limit=2)
            if general_threads:
                lines.append("未解线索：" + "；".join(general_threads))
            cards.append("\n".join(lines))
        return "\n\n".join(cards)

    def _build_relationship_state_memory(self, summaries: list[ChapterSummary]) -> str:
        relationship_lines: list[str] = []
        thread_lines: list[str] = []
        for summary in summaries:
            rendered_relationships = self._render_relationship_entries(summary)
            if rendered_relationships:
                joined_relationships = "；".join(rendered_relationships)
                relationship_lines.append(f"第{summary.chapter_number}章：{joined_relationships}")
            romance_threads = self._extract_labeled_summary_items(summary.unresolved_threads, prefix=ROMANCE_THREAD_PREFIX, limit=3)
            if romance_threads:
                joined_threads = "；".join(romance_threads)
                thread_lines.append(f"第{summary.chapter_number}章：{joined_threads}")

        sections: list[str] = []
        if relationship_lines:
            sections.append("近期关系推进：\n" + "\n".join(relationship_lines))
        if thread_lines:
            sections.append("未解感情线索：\n" + "\n".join(thread_lines))
        return "\n\n".join(sections)

    def _render_relationship_entries(self, summary: ChapterSummary) -> list[str]:
        entries: list[str] = []
        for character_name, payload in self._iterate_character_change_entries(summary.character_changes):
            if isinstance(payload, str):
                continue
            relationships = payload.get("relationships") if isinstance(payload.get("relationships"), list) else []
            emotional_shift = self._coerce_text(payload.get("emotional_shift"))
            emotional_beats = self._normalize_text_items(payload.get("emotional_beats"))
            external_pressures = self._normalize_text_items(payload.get("external_pressures"))

            for relation in relationships:
                if not isinstance(relation, dict):
                    continue
                counterparts = self._normalize_text_items(relation.get("with"))
                labels: list[str] = []
                if counterparts:
                    labels.append(f"{character_name} ↔ {'、'.join(counterparts)}")
                else:
                    labels.append(character_name)
                relation_summary = self._compact_relation_details(relation)
                if relation_summary:
                    labels.append(relation_summary)
                if len(labels) > 1:
                    entries.append(f"{labels[0]}：{'；'.join(labels[1:])}")
                else:
                    entries.append(labels[0])

            if emotional_shift or emotional_beats or external_pressures:
                details: list[str] = []
                if emotional_shift:
                    details.append(f"情绪转折：{emotional_shift}")
                if emotional_beats:
                    details.append("情绪节拍：" + "；".join(emotional_beats[:2]))
                if external_pressures:
                    details.append("外部压力：" + "；".join(external_pressures[:2]))
                entries.append(f"{character_name}：{'；'.join(details)}")
        return self._dedupe_keep_order(entries, limit=8)

    def _iterate_character_change_entries(self, value: Any) -> list[tuple[str, Any]]:
        if isinstance(value, dict):
            return [(str(name).strip(), payload) for name, payload in value.items() if str(name).strip()]
        return []

    def _compact_relation_details(self, relation: dict[str, Any]) -> str:
        parts: list[str] = []
        for key, label in (
            ("change", "关系推进"),
            ("status", "当前状态"),
            ("tension", "张力/误会"),
            ("emotional_shift", "情绪变化"),
            ("notes", "备注"),
        ):
            text = self._coerce_text(relation.get(key))
            if text:
                parts.append(f"{label}：{text}")
        external_pressure = self._normalize_text_items(relation.get("external_pressure"))
        if external_pressure:
            parts.append("外部压力：" + "；".join(external_pressure[:2]))
        return "；".join(parts)

    def _extract_scene_hook(self, summary: ChapterSummary) -> str:
        hook = self._extract_labeled_field(summary.summary_text, SUMMARY_SCENE_HOOK_LABEL)
        if hook:
            return hook
        matches = self._extract_labeled_summary_items(summary.unresolved_threads, prefix=SCENE_HOOK_THREAD_PREFIX, limit=1)
        return matches[0] if matches else ""

    def _extract_next_chapter_anchor(self, summary: ChapterSummary) -> str:
        anchor = self._extract_labeled_field(summary.summary_text, NEXT_CHAPTER_ANCHOR_PREFIX)
        if anchor:
            return anchor
        matches = self._extract_labeled_summary_items(summary.unresolved_threads, prefix=NEXT_CHAPTER_ANCHOR_PREFIX, limit=1)
        return matches[0] if matches else ""

    def _extract_labeled_field(self, text: str, label: str) -> str:
        if not text or label not in text:
            return ""
        trailing = text.split(label, 1)[1].strip()
        for separator in ("；", "\n"):
            if separator in trailing:
                trailing = trailing.split(separator, 1)[0].strip()
        return trailing

    def _extract_labeled_summary_items(self, items: Any, *, prefix: str, limit: int = 3) -> list[str]:
        matches: list[str] = []
        for item in self._normalize_text_items(items):
            if item.startswith(prefix):
                text = item[len(prefix) :].strip()
                if text:
                    matches.append(text)
        return self._dedupe_keep_order(matches, limit=limit)

    def _extract_general_unresolved_threads(self, items: Any, *, limit: int = 3) -> list[str]:
        excluded_prefixes = (ROMANCE_THREAD_PREFIX, SCENE_HOOK_THREAD_PREFIX, NEXT_CHAPTER_ANCHOR_PREFIX)
        normalized = [item for item in self._normalize_text_items(items) if not item.startswith(excluded_prefixes)]
        return self._dedupe_keep_order(normalized, limit=limit)

    async def _get_distant_memory(self, project_id: uuid.UUID, chapter_number: int, outline_text: str) -> str:
        project = await self.session.get(Project, project_id)
        if project is None or chapter_number <= 3:
            return ""

        stmt = (
            select(ChapterSummary)
            .where(
                ChapterSummary.project_id == project_id,
                ChapterSummary.chapter_number < chapter_number - 2,
            )
            .order_by(ChapterSummary.chapter_number.asc())
        )
        result = await self.session.execute(stmt)
        summaries = list(result.scalars())

        sections: list[str] = []
        cache_text = (project.distant_memory_cache or "").strip()
        if cache_text:
            sections.append(cache_text)

        focused_memory = self._get_relevant_distant_summaries(summaries, outline_text)
        if focused_memory:
            sections.append(f"相关远期记忆提要：\n{focused_memory}")

        if sections:
            return "\n\n".join(section for section in sections if section.strip())

        fallback_lines = [
            rendered
            for rendered in (self._render_compact_distant_summary(item) for item in summaries[-4:])
            if rendered
        ]
        return "\n".join(fallback_lines)

    def _get_relevant_distant_summaries(self, summaries: list[ChapterSummary], outline_text: str) -> str:
        if not summaries:
            return ""

        selected = [item for item in summaries if self._is_outline_relevant_to_summary(outline_text, item)]
        candidates = selected or summaries[-4:]
        rendered = [self._render_compact_distant_summary(item) for item in candidates]
        return "\n".join(line for line in rendered if line)

    def _is_outline_relevant_to_summary(self, outline_text: str, summary: ChapterSummary) -> bool:
        if not outline_text.strip():
            return False
        candidate_texts = [
            *self._normalize_text_items(summary.key_events),
            *self._normalize_text_items(summary.unresolved_threads),
            *self._extract_change_names(summary.character_changes),
            *self._extract_change_names(summary.world_changes),
        ]
        outline = outline_text.strip()
        for item in candidate_texts:
            if not item or len(item) > 24:
                continue
            if item in outline:
                return True
        return False

    def _render_compact_distant_summary(self, summary: ChapterSummary) -> str:
        parts: list[str] = []
        key_events = self._normalize_text_items(summary.key_events)[:2]
        unresolved_threads = self._normalize_text_items(summary.unresolved_threads)[:2]
        character_names = self._extract_change_names(summary.character_changes)[:3]
        world_names = self._extract_change_names(summary.world_changes)[:3]

        if key_events:
            parts.append("关键事件：" + "；".join(key_events))
        if unresolved_threads:
            parts.append("未解线索：" + "；".join(unresolved_threads))
        if character_names:
            parts.append("人物变化：" + "、".join(character_names))
        if world_names:
            parts.append("设定变化：" + "、".join(world_names))
        if not parts and summary.summary_text.strip():
            parts.append(summary.summary_text[:160])
        return f"第{summary.chapter_number}章：{'；'.join(parts)}" if parts else ""

    async def _render_recent_summaries(self, project_id: uuid.UUID, chapter_number: int) -> str:
        stmt = (
            select(ChapterSummary)
            .where(
                ChapterSummary.project_id == project_id,
                ChapterSummary.chapter_number < chapter_number,
            )
            .order_by(ChapterSummary.chapter_number.desc())
            .limit(3)
        )
        result = await self.session.execute(stmt)
        items = list(result.scalars())
        return "\n\n".join(f"第{item.chapter_number}章\n{item.summary_text}" for item in reversed(items))

    async def _get_failed_reviews(self, project_id: uuid.UUID, chapter_number: int, limit: int = 3) -> list[tuple[int, ChapterReview]]:
        stmt = (
            select(ChapterAttempt.attempt_no, ChapterReview)
            .join(ChapterReview, ChapterReview.attempt_id == ChapterAttempt.id)
            .join(Chapter, Chapter.id == ChapterAttempt.chapter_id)
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == chapter_number,
                ChapterReview.passed.is_(False),
            )
            .order_by(ChapterAttempt.attempt_no.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(attempt_no, review) for attempt_no, review in result.all()]

    async def _get_recent_accepted_reviews(self, project_id: uuid.UUID, chapter_number: int, limit: int = 5) -> list[tuple[int, ChapterReview]]:
        stmt = (
            select(Chapter.chapter_number, ChapterReview)
            .join(ChapterAttempt, Chapter.accepted_attempt_id == ChapterAttempt.id)
            .join(ChapterReview, ChapterReview.attempt_id == ChapterAttempt.id)
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number < chapter_number,
                Chapter.status == ChapterStatus.PASSED.value,
                Chapter.accepted_attempt_id.is_not(None),
            )
            .order_by(Chapter.chapter_number.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(chapter_no, review) for chapter_no, review in result.all()]

    def _aggregate_retry_reviews(self, failed_reviews: list[tuple[int, ChapterReview]], fallback_feedback: str) -> dict[str, Any]:
        blocking_issues: list[str] = []
        uncovered_outline_points: list[str] = []
        violated_instructions: list[str] = []
        improvement_suggestions: list[str] = []
        last_failed_attempt_no: int | None = None
        last_failed_score: float | None = None

        for attempt_no, review in failed_reviews:
            if last_failed_attempt_no is None:
                last_failed_attempt_no = attempt_no
                last_failed_score = float(review.overall_score)
            feedback = self._extract_review_feedback(review)
            blocking_issues.extend(feedback["blocking_issues"])
            uncovered_outline_points.extend(feedback["uncovered_outline_points"])
            violated_instructions.extend(feedback["violated_instructions"])
            improvement_suggestions.extend(feedback["improvement_suggestions"])

        if fallback_feedback.strip():
            improvement_suggestions.append(fallback_feedback.strip())

        recent_blocking_issues = self._dedupe_keep_order(blocking_issues, limit=5)
        recent_uncovered_outline_points = self._dedupe_keep_order(uncovered_outline_points, limit=5)
        recent_violated_instructions = self._dedupe_keep_order(violated_instructions, limit=5)
        recent_improvement_suggestions = self._dedupe_keep_order(improvement_suggestions, limit=6)

        return {
            "recent_failed_attempts": len(failed_reviews),
            "last_failed_attempt_no": last_failed_attempt_no,
            "last_failed_score": last_failed_score,
            "recent_blocking_issues": recent_blocking_issues,
            "recent_uncovered_outline_points": recent_uncovered_outline_points,
            "recent_violated_instructions": recent_violated_instructions,
            "recent_improvement_suggestions": recent_improvement_suggestions,
            "failed_reasons": self._combine_retry_reasons(
                recent_blocking_issues,
                recent_uncovered_outline_points,
                recent_violated_instructions,
                recent_improvement_suggestions,
            ),
        }

    def _extract_review_feedback(self, review: ChapterReview) -> dict[str, list[str]]:
        blocking_issues = self._normalize_text_items(review.blocking_issues)
        uncovered_outline_points = self._normalize_text_items(review.uncovered_outline_points)
        violated_instructions = self._normalize_text_items(review.violated_instructions)
        improvement_suggestions = self._normalize_text_items(review.improvement_suggestions)

        raw_json = review.raw_json if isinstance(review.raw_json, dict) else {}
        raw_blocking_issues = self._normalize_text_items(raw_json.get("blocking_issues"))
        raw_uncovered_outline_points = self._normalize_text_items(raw_json.get("uncovered_outline_points"))
        raw_violated_instructions = self._normalize_text_items(raw_json.get("violated_instructions"))
        raw_improvement_suggestions = self._normalize_text_items(raw_json.get("improvement_suggestions"))
        raw_major_issues = self._normalize_text_items(raw_json.get("major_issues"))
        raw_minor_issues = self._normalize_text_items(raw_json.get("minor_issues"))
        raw_failed_reasons = self._normalize_text_items(raw_json.get("failed_reasons"))
        raw_summary = self._coerce_text(raw_json.get("summary"))
        outline_comment = self._extract_review_dimension_comment(raw_json, "outline_adherence")
        instruction_comment = self._extract_review_dimension_comment(raw_json, "instruction_adherence")

        if not blocking_issues:
            blocking_issues = raw_blocking_issues
        if not blocking_issues and float(review.outline_score) < 8:
            blocking_issues = self._dedupe_keep_order(raw_major_issues + [outline_comment] + raw_failed_reasons, limit=2)

        if not uncovered_outline_points:
            uncovered_outline_points = raw_uncovered_outline_points

        if not violated_instructions:
            violated_instructions = raw_violated_instructions
        if not violated_instructions and float(review.instruction_score) < 8:
            violated_instructions = self._dedupe_keep_order([instruction_comment] + raw_minor_issues + raw_failed_reasons, limit=2)

        if not improvement_suggestions:
            improvement_suggestions = self._dedupe_keep_order(
                raw_improvement_suggestions
                + raw_major_issues
                + raw_minor_issues
                + raw_failed_reasons
                + [raw_summary],
                limit=4,
            )

        return {
            "blocking_issues": blocking_issues,
            "uncovered_outline_points": uncovered_outline_points,
            "violated_instructions": violated_instructions,
            "improvement_suggestions": improvement_suggestions,
        }

    def _extract_review_dimension_comment(self, payload: dict[str, Any], key: str) -> str:
        dimensions = payload.get("dimensions") if isinstance(payload.get("dimensions"), dict) else {}
        scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
        raw_dimension = dimensions.get(key) or scores.get(key) or payload.get(key)
        if isinstance(raw_dimension, dict):
            return self._coerce_text(raw_dimension.get("comment") or raw_dimension.get("reason"))
        return self._coerce_text(raw_dimension)

    def _combine_retry_reasons(
        self,
        blocking_issues: list[str],
        uncovered_outline_points: list[str],
        violated_instructions: list[str],
        improvement_suggestions: list[str],
    ) -> list[str]:
        combined = [
            *[f"优先修复：{item}" for item in blocking_issues],
            *[f"补齐大纲点：{item}" for item in uncovered_outline_points],
            *[f"避免违规：{item}" for item in violated_instructions],
            *improvement_suggestions,
        ]
        return self._dedupe_keep_order(combined, limit=8)

    def _render_retry_feedback(self, summary: dict[str, Any]) -> str:
        parts: list[str] = []
        failed_attempts = int(summary.get("recent_failed_attempts") or 0)
        last_failed_score = summary.get("last_failed_score")
        if failed_attempts > 0:
            score_text = f"，最近一次评分 {last_failed_score:.2f}" if isinstance(last_failed_score, float) else ""
            parts.append(f"最近 {failed_attempts} 次尝试未通过{score_text}。")

        for title, key in (
            ("阻塞问题", "recent_blocking_issues"),
            ("未覆盖的大纲点", "recent_uncovered_outline_points"),
            ("违反的明确要求", "recent_violated_instructions"),
            ("优先修正建议", "recent_improvement_suggestions"),
        ):
            items = summary.get(key) or []
            if not items:
                continue
            parts.append(f"{title}：\n" + "\n".join(f"- {item}" for item in items))
        return "\n\n".join(parts)

    async def _build_cross_chapter_review_insights(self, project_id: uuid.UUID, chapter_number: int, limit: int = 5) -> str:
        accepted_reviews = await self._get_recent_accepted_reviews(project_id, chapter_number)
        if not accepted_reviews:
            return ""

        frequency: dict[str, int] = {}
        chapter_refs: dict[str, list[int]] = {}
        for accepted_chapter_number, review in accepted_reviews:
            seen_in_review: set[str] = set()
            for item in self._normalize_text_items(review.improvement_suggestions):
                if item in seen_in_review:
                    continue
                seen_in_review.add(item)
                frequency[item] = frequency.get(item, 0) + 1
                chapter_refs.setdefault(item, []).append(accepted_chapter_number)

        if not frequency:
            return ""

        ranked_items = sorted(
            frequency.keys(),
            key=lambda item: (-frequency[item], -max(chapter_refs[item]), item),
        )
        lines: list[str] = []
        for item in ranked_items[:limit]:
            chapters = sorted(chapter_refs[item], reverse=True)
            if frequency[item] > 1:
                suffix = f"（近{frequency[item]}章反复提到，最近见于第{chapters[0]}章）"
            else:
                suffix = f"（来自第{chapters[0]}章已通过评审的提醒）"
            lines.append(f"- {item}{suffix}")
        return "\n".join(lines)

    def _render_review_history(self, retry_history: str, cross_chapter_review_insights: str) -> str:
        parts: list[str] = []
        if retry_history:
            parts.append(f"同章重试复盘：\n{retry_history}")
        if cross_chapter_review_insights:
            parts.append(f"跨章节改进提醒：\n{cross_chapter_review_insights}")
        return "\n\n".join(parts)

    def _render_precheck(self, precheck: dict | None) -> str:
        if not precheck:
            return ""
        parts = []
        if precheck.get("issues"):
            parts.append("潜在问题：" + "；".join(self._normalize_precheck_items(precheck["issues"])))
        if precheck.get("continuity_notes"):
            parts.append("连续性注意：" + "；".join(self._normalize_precheck_items(precheck["continuity_notes"])))
        if precheck.get("suggested_focus"):
            parts.append("建议重点：" + "；".join(self._normalize_precheck_items(precheck["suggested_focus"])))
        return "\n".join(parts)

    def _normalize_precheck_items(self, items: Any) -> list[str]:
        if not isinstance(items, list):
            return []
        normalized: list[str] = []
        for item in items:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append(text)
                continue
            if isinstance(item, dict):
                description = str(item.get("description") or item.get("comment") or "").strip()
                issue_type = str(item.get("type") or item.get("category") or "").strip()
                if description and issue_type:
                    normalized.append(f"{issue_type}: {description}")
                elif description:
                    normalized.append(description)
                elif issue_type:
                    normalized.append(issue_type)
        return normalized

    def _normalize_text_items(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            values = value
        else:
            values = [value]

        normalized: list[str] = []
        for item in values:
            text = self._coerce_text(item)
            if text:
                normalized.append(text)
        return normalized

    def _extract_change_names(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, dict):
            return [str(name).strip() for name in value.keys() if str(name).strip()]
        if not isinstance(value, list):
            return []

        names: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    names.append(text)
                continue
            if not isinstance(item, dict):
                continue
            for key in ("name", "character_name", "world_name", "key", "title"):
                raw_value = item.get(key)
                if isinstance(raw_value, str) and raw_value.strip():
                    names.append(raw_value.strip())
                    break
        return self._dedupe_keep_order(names)

    def _coerce_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            primary_text = ""
            for key in ("description", "comment", "reason", "text", "issue", "title", "summary"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    primary_text = candidate.strip()
                    break
            if primary_text:
                details = value.get("details")
                if isinstance(details, str) and details.strip():
                    return f"{primary_text}：{details.strip()}"
                return primary_text
            parts: list[str] = []
            for key in ("details", "impact", "evidence"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    parts.append(candidate.strip())
            return "；".join(parts)
        return str(value).strip()

    def _dedupe_keep_order(self, items: list[str], limit: int | None = None) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
            if limit is not None and len(deduped) >= limit:
                break
        return deduped

    def _fit_to_budget(
        self,
        layers: list[tuple[str, str, bool]],
        *,
        target_tokens: int | None = None,
        hard_limit_tokens: int | None = None,
    ) -> list[tuple[str, str]]:
        effective_target = target_tokens or self.TARGET_INPUT_TOKENS
        effective_hard_limit = hard_limit_tokens or self.HARD_LIMIT_TOKENS
        kept: list[dict] = []
        for name, text, required in layers:
            if not text:
                continue
            trimmed = self._trim_to_token_limit(text, self.LAYER_LIMITS.get(name))
            kept.append({"name": name, "text": trimmed, "required": required})

        reduction_order = self.REDUCTION_ORDER + [
            item["name"] for item in kept if not item["required"] and item["name"] not in self.REDUCTION_ORDER
        ]
        total = sum(self.estimate_tokens(item["text"]) for item in kept)
        if total <= effective_target:
            return [(item["name"], item["text"]) for item in kept]

        for layer_name in reduction_order:
            if total <= effective_target:
                break
            for item in kept:
                if item["name"] != layer_name or item["required"] or not item["text"]:
                    continue
                original = item["text"]
                trimmed = self._shrink_optional_layer(layer_name, original)
                if trimmed == original:
                    continue
                item["text"] = trimmed
                total -= self.estimate_tokens(original) - self.estimate_tokens(trimmed)
                if total <= effective_target:
                    break

        if total > effective_hard_limit:
            for item in kept:
                if item["required"]:
                    continue
                original = item["text"]
                item["text"] = self._trim_to_token_limit(original, max(300, self.estimate_tokens(original) // 2))
            total = sum(self.estimate_tokens(item["text"]) for item in kept)

            while total > effective_hard_limit:
                progress = False
                overflow = total - effective_hard_limit
                for layer_name in reduction_order:
                    if total <= effective_hard_limit:
                        break
                    for item in kept:
                        if item["name"] != layer_name or item["required"] or not item["text"]:
                            continue
                        current_tokens = self.estimate_tokens(item["text"])
                        if current_tokens <= 300:
                            continue
                        target_limit = max(300, current_tokens - max(overflow, 200))
                        trimmed = self._trim_to_token_limit(item["text"], target_limit)
                        if trimmed == item["text"]:
                            continue
                        item["text"] = trimmed
                        total -= current_tokens - self.estimate_tokens(trimmed)
                        progress = True
                        if total <= effective_hard_limit:
                            break
                if not progress:
                    break

        return [(item["name"], item["text"]) for item in kept if item["text"]]

    def _trim_to_token_limit(self, text: str, token_limit: int | None) -> str:
        if not text or token_limit is None:
            return text
        if self.estimate_tokens(text) <= token_limit:
            return text
        max_chars = max(int(token_limit * 1.8), 300)
        return text[:max_chars]

    def _shrink_optional_layer(self, layer_name: str, text: str) -> str:
        target_limit = self.LAYER_LIMITS.get(layer_name)
        if target_limit is None:
            return self._trim_to_token_limit(text, max(300, self.estimate_tokens(text) // 2))
        if layer_name in {"distant_memory", "critic_distant_memory"}:
            return self._trim_to_token_limit(text, max(500, int(target_limit * 0.55)))
        if layer_name in {
            "relevant_world_settings",
            "relevant_characters",
            "recent_summaries",
            "global_prompt_digest",
            "cross_chapter_review_insights",
            "review_history",
        }:
            return self._trim_to_token_limit(text, max(300, int(target_limit * 0.55)))
        if layer_name == "recent_full_text":
            return self._trim_to_token_limit(text, max(2200, int(target_limit * 0.65)))
        if layer_name == "recent_chapter_cards":
            return self._trim_to_token_limit(text, max(3200, int(target_limit * 0.68)))
        if layer_name == "relationship_state_memory":
            return self._trim_to_token_limit(text, max(2600, int(target_limit * 0.72)))
        if layer_name == "previous_chapter_tail":
            return self._trim_to_token_limit(text, max(2800, int(target_limit * 0.75)))
        return self._trim_to_token_limit(text, max(300, target_limit // 2))

    def _build_global_prompt_digest(self, global_prompt: str) -> str:
        normalized = global_prompt.strip()
        if self.estimate_tokens(normalized) <= self.LAYER_LIMITS["global_prompt_digest"]:
            return normalized
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return self._trim_to_token_limit(normalized, self.LAYER_LIMITS["global_prompt_digest"])
        digest = "\n".join(f"- {line}" for line in lines[:8])
        return self._trim_to_token_limit(digest, self.LAYER_LIMITS["global_prompt_digest"])

    def _render_writer_message(
        self,
        chapter_number: int,
        layers: list[tuple[str, str]],
        *,
        continuation_prefix: str = "",
    ) -> str:
        rendered = [f"【第{chapter_number}章创作任务】"]
        for name, text in layers:
            rendered.append(f"【{name}】\n{text}")
        if continuation_prefix.strip():
            rendered.append(
                "【continuation_prefix】\n"
                + self._trim_continuation_prefix(continuation_prefix.strip())
            )
            rendered.append(
                f"请在以上已生成正文基础上从最后一句自然续写第{chapter_number}章，只输出新增正文，不要重复、改写或总结前文。"
            )
        else:
            rendered.append(f"请严格按照以上要求完成第{chapter_number}章，直接输出正文。")
        return "\n\n".join(rendered)

    def _render_rush_writer_message(
        self,
        chapter_number: int,
        layers: list[tuple[str, str]],
        *,
        continuation_prefix: str = "",
    ) -> str:
        titles = {
            "book_title": "书名",
            "genre": "题材",
            "global_prompt": "全局提示词",
            "rush_previous_chapter_contents": "前文正文",
            "current_outline": "本章大纲",
            "rush_instruction": "创作指令",
        }
        rendered = [f"【Rush 极速创作任务：第{chapter_number}章】"]
        for name, text in layers:
            rendered.append(f"【{titles.get(name, name)}】\n{text}")
        if continuation_prefix.strip():
            rendered.append(
                "【已生成正文】\n"
                + self._trim_continuation_prefix(continuation_prefix.strip())
            )
            rendered.append(f"请基于已生成正文自然续写第{chapter_number}章，只输出新增正文，不要重复前文。")
        else:
            rendered.append(f"请直接输出第{chapter_number}章正文，不要输出评审、摘要或设定修订。")
        return "\n\n".join(rendered)

    def _trim_continuation_prefix(self, continuation_prefix: str) -> str:
        if len(continuation_prefix) <= self.CONTINUATION_PREFIX_MAX_CHARS:
            return continuation_prefix
        return continuation_prefix[-self.CONTINUATION_PREFIX_MAX_CHARS :]

    def _render_critic_message(self, layers: list[tuple[str, str]]) -> str:
        titles = {
            "critic_global_prompt": "global_prompt",
            "effective_system_prompt": "effective_system_prompt",
            "current_outline": "本章大纲",
            "relevant_characters": "相关人物",
            "relevant_world_settings": "相关世界设定",
            "recent_summaries": "最近章节摘要",
            "previous_chapter_tail": "上一章末尾",
            "review_history": "历史评审要点",
            "critic_distant_memory": "远期记忆摘要",
            "critic_content": "待评审正文",
        }
        rendered: list[str] = []
        for name, text in layers:
            rendered.append(f"【{titles.get(name, name)}】\n{text}")
        return "\n\n".join(rendered)
