import asyncio
import re
import uuid
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import ChapterOutline, ChapterPrompt, Project, ProjectModelConfig, PromptStatus
from backend.llm.factory import build_llm_client
from backend.prompts.prompt_builder import (
    CHAPTER_PROMPT_BUILDER_SYSTEM_PROMPT,
    PROJECT_GLOBAL_PROMPT_BUILDER_SYSTEM_PROMPT,
    PROJECT_OUTLINE_BUILDER_SYSTEM_PROMPT,
)
from backend.prompts.writer import BASE_WRITER_RULES
from backend.services.runtime_service import RuntimeService


@dataclass
class PromptGenerationResult:
    prompt: ChapterPrompt
    source: str
    diagnostics: dict[str, Any] | None = None


class PromptBuilderGenerationError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class PromptService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runtime = RuntimeService(session)
        self.settings = get_settings()

    async def get_latest_prompt(self, project_id: uuid.UUID, chapter_number: int) -> ChapterPrompt | None:
        stmt = (
            select(ChapterPrompt)
            .where(
                ChapterPrompt.project_id == project_id,
                ChapterPrompt.chapter_number == chapter_number,
            )
            .order_by(ChapterPrompt.version_no.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_approved_prompt(self, project_id: uuid.UUID, chapter_number: int) -> ChapterPrompt | None:
        stmt = (
            select(ChapterPrompt)
            .where(
                ChapterPrompt.project_id == project_id,
                ChapterPrompt.chapter_number == chapter_number,
                ChapterPrompt.status == PromptStatus.APPROVED.value,
            )
            .order_by(ChapterPrompt.version_no.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create_effective_prompt_result(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        precheck: dict[str, Any] | None,
        retry_info: dict[str, Any] | None,
        force_regenerate: bool = False,
    ) -> PromptGenerationResult:
        approved = await self.get_approved_prompt(project_id, chapter_number)
        if approved is not None and (not force_regenerate or self._should_preserve_manual_prompt(approved)):
            return PromptGenerationResult(prompt=approved, source="approved")
        return await self.generate_prompt_result(
            project_id=project_id,
            chapter_number=chapter_number,
            precheck=precheck,
            retry_info=retry_info,
        )

    async def get_or_create_effective_prompt(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        precheck: dict[str, Any] | None,
        retry_info: dict[str, Any] | None,
        force_regenerate: bool = False,
    ) -> ChapterPrompt:
        result = await self.get_or_create_effective_prompt_result(
            project_id=project_id,
            chapter_number=chapter_number,
            precheck=precheck,
            retry_info=retry_info,
            force_regenerate=force_regenerate,
        )
        return result.prompt

    async def generate_prompt_result(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        precheck: dict[str, Any] | None,
        retry_info: dict[str, Any] | None,
    ) -> PromptGenerationResult:
        source_payload, next_version = await self._prepare_prompt_generation(
            project_id=project_id,
            chapter_number=chapter_number,
            precheck=precheck,
            retry_info=retry_info,
        )
        generated_payload, prompt_source, diagnostics = await self._build_prompt_payload_result(project_id, source_payload)
        prompt = await self._create_prompt_record(
            project_id=project_id,
            chapter_number=chapter_number,
            version_no=next_version,
            source_payload=source_payload,
            generated_payload=generated_payload,
            diagnostics=diagnostics,
        )
        return PromptGenerationResult(prompt=prompt, source=prompt_source, diagnostics=diagnostics)

    async def generate_prompt(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        precheck: dict[str, Any] | None,
        retry_info: dict[str, Any] | None,
    ) -> ChapterPrompt:
        result = await self.generate_prompt_result(
            project_id=project_id,
            chapter_number=chapter_number,
            precheck=precheck,
            retry_info=retry_info,
        )
        return result.prompt

    async def create_timeout_fallback_prompt_result(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        precheck: dict[str, Any] | None,
        retry_info: dict[str, Any] | None,
        error_message: str,
    ) -> PromptGenerationResult:
        config, resolved_role = await self._resolve_prompt_builder_config(project_id)
        diagnostics = self._build_prompt_builder_diagnostics(
            config=config,
            resolved_role=resolved_role,
            failure_type="stage_timeout",
            message=error_message,
            timeout_seconds=float(self.settings.llm_stage_timeout_seconds),
        )
        source_payload, next_version = await self._prepare_prompt_generation(
            project_id=project_id,
            chapter_number=chapter_number,
            precheck=precheck,
            retry_info=retry_info,
        )
        prompt = await self._create_prompt_record(
            project_id=project_id,
            chapter_number=chapter_number,
            version_no=next_version,
            source_payload=source_payload,
            generated_payload=self._fallback_prompt_payload(source_payload),
            diagnostics=diagnostics,
        )
        return PromptGenerationResult(prompt=prompt, source="fallback", diagnostics=diagnostics)

    async def generate_global_prompt(
        self,
        *,
        project_id: uuid.UUID,
        outlines_text: str | None = None,
        title: str | None = None,
        genre: str | None = None,
        style: str | None = None,
    ) -> dict[str, Any]:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise ValueError("项目不存在")

        normalized_outlines = await self._resolve_global_prompt_outlines(project_id, outlines_text)
        source_payload = {
            "title": title.strip() if isinstance(title, str) else (project.title or "").strip(),
            "genre": genre.strip() if isinstance(genre, str) else (project.genre or "").strip(),
            "style": style.strip() if isinstance(style, str) else (project.style or "").strip(),
            "outlines_text": normalized_outlines,
            "chapter_count": self._count_outline_blocks(normalized_outlines),
        }
        payload = await self._build_global_prompt_payload(project_id, source_payload)
        resolved_project_info = self._resolve_project_info(payload, source_payload)
        global_prompt = self._extract_global_prompt_text(payload, source_payload | resolved_project_info)
        return {
            "title": resolved_project_info["title"],
            "genre": resolved_project_info["genre"],
            "style": resolved_project_info["style"],
            "global_prompt": global_prompt,
            "builder_payload": payload,
            "source_payload": source_payload | resolved_project_info,
        }

    async def generate_outlines_from_global_prompt(
        self,
        *,
        project_id: uuid.UUID,
        global_prompt: str | None = None,
        title: str | None = None,
        genre: str | None = None,
        style: str | None = None,
        total_chapters: int | None = None,
    ) -> dict[str, Any]:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise ValueError("项目不存在")

        normalized_global_prompt = (
            global_prompt.strip() if isinstance(global_prompt, str) else (project.global_prompt or "").strip()
        )
        if not normalized_global_prompt:
            raise ValueError("请先填写全局系统提示词，再生成章节大纲草案")

        normalized_total_chapters = total_chapters if isinstance(total_chapters, int) else project.total_chapters
        normalized_total_chapters = int(normalized_total_chapters or 0)
        if normalized_total_chapters < 1:
            raise ValueError("总章节数必须大于等于 1")

        source_payload = {
            "title": title.strip() if isinstance(title, str) else (project.title or "").strip(),
            "genre": genre.strip() if isinstance(genre, str) else (project.genre or "").strip(),
            "style": style.strip() if isinstance(style, str) else (project.style or "").strip(),
            "global_prompt": normalized_global_prompt,
            "total_chapters": normalized_total_chapters,
        }
        payload = await self._build_outline_payload(project_id, source_payload)
        outlines = self._extract_generated_outlines(payload, source_payload)
        return {
            "outlines": outlines,
            "builder_payload": payload,
            "source_payload": source_payload,
        }

    async def update_prompt(self, project_id: uuid.UUID, chapter_number: int, version_no: int, edited_prompt: str) -> ChapterPrompt | None:
        stmt = select(ChapterPrompt).where(
            ChapterPrompt.project_id == project_id,
            ChapterPrompt.chapter_number == chapter_number,
            ChapterPrompt.version_no == version_no,
        )
        result = await self.session.execute(stmt)
        prompt = result.scalar_one_or_none()
        if not prompt:
            return None
        prompt.user_edited_prompt = edited_prompt
        prompt.effective_system_prompt = f"{BASE_WRITER_RULES}\n\n{edited_prompt}"
        prompt.status = PromptStatus.EDITED.value
        await self.session.flush()
        return prompt

    async def approve_prompt(self, project_id: uuid.UUID, chapter_number: int, version_no: int) -> ChapterPrompt | None:
        stmt = select(ChapterPrompt).where(
            ChapterPrompt.project_id == project_id,
            ChapterPrompt.chapter_number == chapter_number,
            ChapterPrompt.version_no == version_no,
        )
        result = await self.session.execute(stmt)
        prompt = result.scalar_one_or_none()
        if not prompt:
            return None

        await self.session.execute(
            update(ChapterPrompt)
            .where(
                ChapterPrompt.project_id == project_id,
                ChapterPrompt.chapter_number == chapter_number,
                ChapterPrompt.version_no != version_no,
                ChapterPrompt.status == PromptStatus.APPROVED.value,
            )
            .values(status=PromptStatus.SUPERSEDED.value)
        )
        prompt.status = PromptStatus.APPROVED.value
        await self.session.flush()
        return prompt

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

    async def _prepare_prompt_generation(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        precheck: dict[str, Any] | None,
        retry_info: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], int]:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise ValueError("项目不存在")
        outline = await self._get_outline(project_id, chapter_number)
        latest = await self.get_latest_prompt(project_id, chapter_number)
        next_version = 1 if latest is None else latest.version_no + 1
        source_payload = {
            "outline": outline.outline_text,
            "global_prompt": project.global_prompt,
            "precheck": precheck or {},
            "retry_info": retry_info or {},
        }
        return source_payload, next_version

    async def _create_prompt_record(
        self,
        *,
        project_id: uuid.UUID,
        chapter_number: int,
        version_no: int,
        source_payload: dict[str, Any],
        generated_payload: dict[str, Any],
        diagnostics: dict[str, Any] | None,
    ) -> ChapterPrompt:
        generated = self._extract_chapter_prompt_text(generated_payload, source_payload)
        effective = f"{BASE_WRITER_RULES}\n\n{generated}"
        persisted_source_payload = source_payload | {"builder_output": generated_payload}
        if diagnostics is not None:
            persisted_source_payload["builder_diagnostics"] = diagnostics

        prompt = ChapterPrompt(
            project_id=project_id,
            chapter_number=chapter_number,
            version_no=version_no,
            generated_system_prompt=generated,
            user_edited_prompt=None,
            effective_system_prompt=effective,
            source_payload=persisted_source_payload,
            status=PromptStatus.GENERATED.value,
        )
        self.session.add(prompt)
        await self.session.flush()
        return prompt

    async def _resolve_prompt_builder_config(self, project_id: uuid.UUID) -> tuple[ProjectModelConfig, str]:
        configs = await self.runtime.get_model_configs(project_id)
        config = configs.get("prompt_builder")
        resolved_role = "prompt_builder"
        if config is None:
            config = configs.get("writer")
            resolved_role = "writer"
        if config is None:
            raise ValueError("缺少模型配置: prompt_builder")
        return config, resolved_role

    async def _build_prompt_builder_client(self, config: ProjectModelConfig):
        api_key, env_key = await self.runtime.resolve_api_key(config)
        return build_llm_client(config, api_key=api_key, api_key_name=env_key)

    async def _build_prompt_payload_result(
        self,
        project_id: uuid.UUID,
        source_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
        config, resolved_role = await self._resolve_prompt_builder_config(project_id)
        timeout_seconds = self._prompt_builder_timeout_seconds()
        try:
            client = self._build_prompt_builder_client(config)
            if isawaitable(client):
                client = await client
            payload = await asyncio.wait_for(
                client.generate_json(
                    system_prompt=CHAPTER_PROMPT_BUILDER_SYSTEM_PROMPT,
                    user_message=self._render_builder_user_message(source_payload),
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            diagnostics = self._build_prompt_builder_diagnostics(
                config=config,
                resolved_role=resolved_role,
                failure_type="timeout",
                message=f"prompt_builder 请求超时，{timeout_seconds:.0f}s 内未返回合法 JSON。",
                timeout_seconds=timeout_seconds,
            )
            return self._fallback_prompt_payload(source_payload), "fallback", diagnostics
        except Exception as exc:  # noqa: BLE001
            failure_type = self._classify_prompt_builder_exception(exc)
            should_fallback = self._should_fallback_for_prompt_builder_failure(failure_type)
            diagnostics = self._build_prompt_builder_diagnostics(
                config=config,
                resolved_role=resolved_role,
                failure_type=failure_type,
                message=str(exc),
                raw_preview=self._extract_raw_preview(exc),
                timeout_seconds=timeout_seconds,
                fallback_used=should_fallback,
            )
            if should_fallback:
                return self._fallback_prompt_payload(source_payload), "fallback", diagnostics
            raise PromptBuilderGenerationError(str(exc), diagnostics) from exc

        if isinstance(payload, dict) and "system_prompt" in payload:
            return payload, "builder", None

        diagnostics = self._build_prompt_builder_diagnostics(
            config=config,
            resolved_role=resolved_role,
            failure_type="invalid_payload",
            message="prompt_builder 返回缺少 system_prompt 字段，已回退到本地 fallback prompt。",
            raw_preview=self._serialize_payload_preview(payload),
            timeout_seconds=timeout_seconds,
        )
        return self._fallback_prompt_payload(source_payload), "fallback", diagnostics

    async def _build_prompt_payload(self, project_id: uuid.UUID, source_payload: dict[str, Any]) -> dict[str, Any]:
        payload, _, _ = await self._build_prompt_payload_result(project_id, source_payload)
        return payload

    def _fallback_prompt_payload(self, source_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "system_prompt": self._fallback_prompt_text(source_payload),
            "must_cover": ["覆盖本章大纲关键事件"],
            "must_avoid": ["不要脱离大纲", "不要输出解释"],
            "style_focus": ["保持风格一致"],
            "reasoning_notes": ["fallback prompt builder"],
        }

    def _prompt_builder_timeout_seconds(self) -> float:
        stage_timeout = max(0.5, float(self.settings.llm_stage_timeout_seconds))
        margin = min(5.0, max(0.5, stage_timeout * 0.1))
        return max(0.5, stage_timeout - margin)

    def _classify_prompt_builder_exception(self, exc: Exception) -> str:
        message = str(exc).lower()
        if self._extract_raw_preview(exc) or "json" in message:
            return "json_parse_error"
        if "缺少模型配置" in str(exc):
            return "missing_configuration"
        if "缺少 api key" in message:
            return "missing_api_key"
        if "暂不支持的 provider" in str(exc):
            return "unsupported_provider"
        return "request_failed"

    def _should_fallback_for_prompt_builder_failure(self, failure_type: str) -> bool:
        return failure_type in {"timeout", "invalid_payload", "json_parse_error"}

    def _extract_raw_preview(self, exc: Exception) -> str | None:
        raw_preview = getattr(exc, "raw_preview", None)
        if isinstance(raw_preview, str) and raw_preview.strip():
            return raw_preview.strip()
        return None

    def _serialize_payload_preview(self, payload: Any) -> str | None:
        try:
            serialized = payload if isinstance(payload, str) else str(payload if payload is None else payload)
            if not isinstance(payload, str):
                import json
                serialized = json.dumps(payload, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            serialized = str(payload)
        text = serialized.strip()
        if not text:
            return None
        if len(text) <= 400:
            return text
        return f"{text[:399]}…"

    def _build_prompt_builder_diagnostics(
        self,
        *,
        config: ProjectModelConfig,
        resolved_role: str,
        failure_type: str,
        message: str,
        timeout_seconds: float,
        raw_preview: str | None = None,
        fallback_used: bool = True,
    ) -> dict[str, Any]:
        diagnostics = {
            "fallback_used": fallback_used,
            "failure_type": failure_type,
            "message": message,
            "configured_role": "prompt_builder",
            "resolved_model_role": resolved_role,
            "provider": config.provider,
            "base_url": config.base_url,
            "model_name": config.model_name,
            "timeout_seconds": round(timeout_seconds, 3),
        }
        if raw_preview:
            diagnostics["raw_preview"] = raw_preview
        return diagnostics


    async def _build_global_prompt_payload(self, project_id: uuid.UUID, source_payload: dict[str, Any]) -> dict[str, Any]:
        try:
            client = await self.runtime.get_client(project_id, "prompt_builder", fallback_role="writer")
            payload = await client.generate_json(
                system_prompt=PROJECT_GLOBAL_PROMPT_BUILDER_SYSTEM_PROMPT,
                user_message=self._render_global_prompt_builder_message(source_payload),
            )
            if isinstance(payload, dict) and "global_prompt" in payload:
                return payload
            diagnostics = await self._build_global_outline_fallback_diagnostics(
                project_id=project_id,
                failure_type="invalid_payload",
                message="prompt_builder 返回缺少 global_prompt 字段，已回退到本地 fallback global prompt。",
                raw_preview=self._serialize_payload_preview(payload),
            )
            return self._build_fallback_global_prompt_payload(source_payload, diagnostics)
        except Exception as exc:  # noqa: BLE001
            failure_type = self._classify_prompt_builder_exception(exc)
            should_fallback = self._should_fallback_for_prompt_builder_failure(failure_type)
            diagnostics = await self._build_global_outline_fallback_diagnostics(
                project_id=project_id,
                failure_type=failure_type,
                message=str(exc),
                raw_preview=self._extract_raw_preview(exc),
                fallback_used=should_fallback,
            )
            if should_fallback:
                return self._build_fallback_global_prompt_payload(source_payload, diagnostics)
            raise PromptBuilderGenerationError(str(exc), diagnostics) from exc

    def _build_fallback_global_prompt_payload(
        self,
        source_payload: dict[str, Any],
        diagnostics: dict[str, Any],
    ) -> dict[str, Any]:
        resolved_project_info = self._resolve_project_info({}, source_payload)
        return {
            "title": resolved_project_info["title"],
            "genre": resolved_project_info["genre"],
            "style": resolved_project_info["style"],
            "global_prompt": self._fallback_global_prompt_text(source_payload | resolved_project_info),
            "core_requirements": ["严格遵循章节大纲推进主线"],
            "style_guidance": ["保持全书风格稳定，人物行为前后一致"],
            "risk_points": ["不要跳过关键情节，不要擅自改写大纲主线"],
            "reasoning_notes": ["fallback global prompt builder"],
            "builder_diagnostics": diagnostics,
        }

    def _render_builder_user_message(self, source_payload: dict[str, Any]) -> str:
        return "\n\n".join(
            [
                f"【全局系统提示词】\n{source_payload['global_prompt']}",
                f"【本章大纲】\n{source_payload['outline']}",
                f"【连续性预检查】\n{self._render_structured_retry_info(source_payload.get('precheck')) or '{}'}",
                f"【重试上下文】\n{self._render_structured_retry_info(source_payload.get('retry_info')) or '{}'}",
            ]
        )

    def _extract_chapter_prompt_text(self, payload: dict[str, Any], source_payload: dict[str, Any]) -> str:
        candidate = self._coerce_text(payload.get("system_prompt"))
        if candidate:
            return candidate
        structured = payload.get("system_prompt") if isinstance(payload.get("system_prompt"), dict) else payload
        rendered = self._render_structured_chapter_prompt(structured)
        return rendered or self._fallback_prompt_text(source_payload)

    def _render_structured_chapter_prompt(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        sections: list[str] = []
        role = self._coerce_text(payload.get("role"))
        if role:
            sections.append(f"角色定位：{role}")
        core_goal = self._coerce_text(payload.get("core_goal") or payload.get("goal"))
        if core_goal:
            sections.append(f"核心目标：{core_goal}")
        for title, key in (
            ("必须做到", "must_do"),
            ("必须覆盖", "must_cover"),
            ("禁止事项", "must_avoid"),
            ("风格重点", "style_focus"),
            ("场景目标", "scene_targets"),
            ("质量自检", "quality_check"),
        ):
            items = self._coerce_lines(payload.get(key))
            if not items:
                continue
            sections.append(f"{title}：\n" + "\n".join(f"{idx}. {item}" for idx, item in enumerate(items, start=1)))
        return "\n\n".join(section for section in sections if section.strip())

    def _fallback_prompt_text(self, source_payload: dict[str, Any]) -> str:
        precheck = source_payload.get("precheck") or {}
        retry_info = source_payload.get("retry_info") or {}
        continuity_notes = "；".join(self._coerce_lines(precheck.get("continuity_notes")))
        retry_brief = self._render_structured_retry_info(retry_info)
        return "\n".join(
            [
                "本章执行要求：",
                f"1. 必须覆盖以下大纲：{source_payload['outline']}",
                f"2. 必须遵循作品总要求：{source_payload['global_prompt']}",
                f"3. 连续性注意事项：{continuity_notes or '无'}",
                f"4. 重试修正点：{retry_brief or '无'}",
                "5. 正文必须直接输出，不要解释。",
            ]
        )

    def _extract_global_prompt_text(self, payload: dict[str, Any], source_payload: dict[str, Any]) -> str:
        candidate = self._coerce_text(payload.get("global_prompt"))
        if candidate:
            return candidate
        structured = payload.get("global_prompt") if isinstance(payload.get("global_prompt"), dict) else payload
        rendered = self._render_structured_global_prompt(structured)
        return rendered or self._fallback_global_prompt_text(source_payload)

    async def _build_outline_payload(self, project_id: uuid.UUID, source_payload: dict[str, Any]) -> dict[str, Any]:
        try:
            client = await self.runtime.get_client(project_id, "prompt_builder", fallback_role="writer")
            payload = await client.generate_json(
                system_prompt=PROJECT_OUTLINE_BUILDER_SYSTEM_PROMPT,
                user_message=self._render_outline_builder_message(source_payload),
            )
            if isinstance(payload, list):
                return {"chapter_outlines": payload}
            if isinstance(payload, dict) and isinstance(payload.get("chapter_outlines"), list):
                return payload
            diagnostics = await self._build_global_outline_fallback_diagnostics(
                project_id=project_id,
                failure_type="invalid_payload",
                message="prompt_builder 返回缺少 chapter_outlines 列表，已回退到本地 fallback outline。",
                raw_preview=self._serialize_payload_preview(payload),
            )
            return self._fallback_outline_payload(source_payload, diagnostics)
        except Exception as exc:  # noqa: BLE001
            failure_type = self._classify_prompt_builder_exception(exc)
            should_fallback = self._should_fallback_for_prompt_builder_failure(failure_type)
            diagnostics = await self._build_global_outline_fallback_diagnostics(
                project_id=project_id,
                failure_type=failure_type,
                message=str(exc),
                raw_preview=self._extract_raw_preview(exc),
                fallback_used=should_fallback,
            )
            if should_fallback:
                return self._fallback_outline_payload(source_payload, diagnostics)
            raise PromptBuilderGenerationError(str(exc), diagnostics) from exc

    def _extract_generated_outlines(self, payload: dict[str, Any], source_payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_items = payload.get("chapter_outlines") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list) or not raw_items:
            raw_items = self._fallback_outline_payload(source_payload)["chapter_outlines"]

        normalized_items: list[dict[str, Any]] = []
        total_chapters = int(source_payload["total_chapters"])
        for index in range(total_chapters):
            raw = raw_items[index] if index < len(raw_items) and isinstance(raw_items[index], dict) else {}
            chapter_number = index + 1
            title = self._coerce_text(raw.get("title")) or f"第{chapter_number}章"
            summary = self._coerce_text(raw.get("summary")) or "待补充本章剧情摘要。"
            must_cover = self._coerce_lines(raw.get("must_cover"))
            tags = raw.get("tags") if isinstance(raw.get("tags"), dict) else None
            normalized_items.append(
                {
                    "chapter_number": chapter_number,
                    "outline_text": self._render_outline_text(
                        {
                            "title": title,
                            "summary": summary,
                            "must_cover": must_cover,
                        },
                        chapter_number,
                    ),
                    "tags": tags,
                }
            )
        return normalized_items

    def _render_outline_text(self, item: dict[str, Any], chapter_number: int) -> str:
        title = self._coerce_text(item.get("title")) or f"第{chapter_number}章"
        summary = self._coerce_text(item.get("summary")) or "待补充本章剧情摘要。"
        must_cover = self._coerce_lines(item.get("must_cover"))
        sections = [f"第{chapter_number}章：{title}", f"摘要：{summary}"]
        if must_cover:
            sections.append("关键点：\n" + "\n".join(f"- {point}" for point in must_cover))
        return "\n".join(sections)

    def _render_outline_builder_message(self, source_payload: dict[str, Any]) -> str:
        return "\n\n".join(
            [
                f"【书名】\n{source_payload['title'] or '未提供'}",
                f"【题材】\n{source_payload['genre'] or '未提供'}",
                f"【风格】\n{source_payload['style'] or '未提供'}",
                f"【总章节数】\n{source_payload['total_chapters']}",
                f"【全局系统提示词】\n{source_payload['global_prompt']}",
            ]
        )

    def _fallback_outline_payload(
        self,
        source_payload: dict[str, Any],
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        chapter_outlines = []
        total_chapters = int(source_payload["total_chapters"])
        for chapter_number in range(1, total_chapters + 1):
            chapter_outlines.append(
                {
                    "chapter_number": chapter_number,
                    "title": f"草案第{chapter_number}章",
                    "summary": f"围绕《{source_payload['title'] or '本书'}》主线推进第 {chapter_number} 章情节，延续全局提示词要求并完成阶段性剧情推进。",
                    "must_cover": [
                        "承接上一章已建立的信息与关系",
                        "推进当前章节的核心冲突或目标",
                        "保持人物行为、世界规则与全局提示词一致",
                    ],
                    "tags": None,
                }
            )
        payload: dict[str, Any] = {
            "chapter_outlines": chapter_outlines,
            "reasoning_notes": ["fallback outline builder"],
        }
        if diagnostics is not None:
            payload["builder_diagnostics"] = diagnostics
        return payload

    async def _build_global_outline_fallback_diagnostics(
        self,
        *,
        project_id: uuid.UUID,
        failure_type: str,
        message: str,
        raw_preview: str | None,
        fallback_used: bool = True,
    ) -> dict[str, Any]:
        config, resolved_role = await self._resolve_prompt_builder_config(project_id)
        return self._build_prompt_builder_diagnostics(
            config=config,
            resolved_role=resolved_role,
            failure_type=failure_type,
            message=message,
            raw_preview=raw_preview,
            timeout_seconds=self._prompt_builder_timeout_seconds(),
            fallback_used=fallback_used,
        )

    def _render_structured_retry_info(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, dict):
            return self._coerce_text(value)

        sections: list[str] = []
        scalar_pairs = [
            ("最近失败尝试次数", value.get("recent_failed_attempts")),
            ("最近失败尝试轮次", value.get("last_failed_attempt_no")),
            ("最近失败评分", value.get("last_failed_score")),
            ("重试摘要", value.get("retry_brief")),
        ]
        for label, raw in scalar_pairs:
            text = self._coerce_text(raw)
            if text:
                sections.append(f"{label}：{text}")

        for title, key in (
            ("失败原因", "failed_reasons"),
            ("阻塞问题", "recent_blocking_issues"),
            ("未覆盖大纲点", "recent_uncovered_outline_points"),
            ("违反要求", "recent_violated_instructions"),
            ("优先修正建议", "recent_improvement_suggestions"),
            ("潜在问题", "issues"),
            ("连续性注意", "continuity_notes"),
            ("建议重点", "suggested_focus"),
        ):
            items = self._coerce_lines(value.get(key))
            if not items:
                continue
            sections.append(f"{title}：\n" + "\n".join(f"- {item}" for item in items))

        return "\n\n".join(section for section in sections if section.strip())

    def _should_preserve_manual_prompt(self, prompt: ChapterPrompt) -> bool:
        return bool((prompt.user_edited_prompt or "").strip())

    async def _resolve_global_prompt_outlines(self, project_id: uuid.UUID, outlines_text: str | None) -> str:
        normalized = (outlines_text or "").strip()
        if normalized:
            return normalized

        stmt = (
            select(ChapterOutline)
            .where(ChapterOutline.project_id == project_id)
            .order_by(ChapterOutline.chapter_number.asc())
        )
        result = await self.session.execute(stmt)
        outlines = list(result.scalars())
        if not outlines:
            raise ValueError("请先输入或导入章节大纲，再生成全局系统提示词")
        return "\n\n".join(item.outline_text for item in outlines if item.outline_text.strip())

    def _render_global_prompt_builder_message(self, source_payload: dict[str, Any]) -> str:
        return "\n\n".join(
            [
                f"【书名】\n{source_payload['title'] or '未提供'}",
                f"【题材】\n{source_payload['genre'] or '未提供'}",
                f"【风格】\n{source_payload['style'] or '未提供'}",
                f"【章节数】\n{source_payload['chapter_count']}",
                f"【章节大纲】\n{source_payload['outlines_text']}",
            ]
        )

    def _fallback_global_prompt_text(self, source_payload: dict[str, Any]) -> str:
        title = source_payload["title"] or "本书"
        genre = source_payload["genre"] or "未限定题材"
        style = source_payload["style"] or "以章节大纲自然呈现"
        return "\n".join(
            [
                f"你是一名长篇中文小说写作者，正在创作《{title}》。",
                f"作品题材：{genre}。",
                f"整体风格：{style}。",
                "",
                "全书执行规则：",
                "1. 必须严格按照章节大纲推进剧情，不得跳过关键情节或擅自改写主线。",
                "2. 保持人物动机、说话方式、情绪变化和前文连续一致。",
                "3. 保持世界观规则、势力关系、时间线和地点信息自洽。",
                "4. 每章都应围绕该章大纲完成明确推进，不要空转，不要用无关桥段稀释主线。",
                "5. 文风与节奏要全书统一，避免忽然切换叙事口吻或严重偏题。",
                "6. 只输出小说正文，不输出解释、分析、元评论或创作说明。",
                "",
                "创作重点来源：后续所有章节写作必须以当前导入的大纲为第一约束。",
            ]
        )

    def _resolve_project_info(self, payload: dict[str, Any], source_payload: dict[str, Any]) -> dict[str, str]:
        outlines_text = self._coerce_text(source_payload.get("outlines_text"))
        return {
            "title": self._coerce_text(payload.get("title"))
            or self._coerce_text(source_payload.get("title"))
            or self._infer_title_from_outlines(outlines_text),
            "genre": self._coerce_text(payload.get("genre"))
            or self._coerce_text(source_payload.get("genre"))
            or self._infer_genre_from_outlines(outlines_text),
            "style": self._coerce_text(payload.get("style"))
            or self._coerce_text(source_payload.get("style"))
            or self._infer_style_from_outlines(outlines_text),
        }

    def _count_outline_blocks(self, outlines_text: str) -> int:
        blocks = [block.strip() for block in outlines_text.split("\n\n") if block.strip()]
        return len(blocks)

    def _render_structured_global_prompt(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        sections: list[str] = []
        summary = self._coerce_text(payload.get("summary") or payload.get("core_goal"))
        if summary:
            sections.append(summary)
        for title, key in (
            ("核心要求", "core_requirements"),
            ("风格指导", "style_guidance"),
            ("风险提醒", "risk_points"),
        ):
            items = self._coerce_lines(payload.get(key))
            if not items:
                continue
            sections.append(f"{title}：\n" + "\n".join(f"{idx}. {item}" for idx, item in enumerate(items, start=1)))
        return "\n\n".join(section for section in sections if section.strip())

    def _coerce_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n".join(self._coerce_text(item) for item in value if self._coerce_text(item))
        if isinstance(value, dict):
            for key in ("description", "comment", "reason", "text", "issue", "title"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            return ""
        if value is None:
            return ""
        return str(value).strip()

    def _coerce_lines(self, value: Any) -> list[str]:
        if isinstance(value, str):
            normalized = value.strip()
            return [normalized] if normalized else []
        if isinstance(value, list):
            return [text for item in value if (text := self._coerce_text(item))]
        if isinstance(value, dict):
            text = self._coerce_text(value)
            return [text] if text else []
        return []

    def _infer_title_from_outlines(self, outlines_text: str) -> str:
        for raw_line in outlines_text.splitlines():
            line = raw_line.strip()
            if not line or len(line) > 80:
                continue
            if line.startswith("第") and "章" in line:
                continue
            if "《" in line and "》" in line:
                return line
            match = re.match(r"^(?:书名|作品名|项目名|卷名)[：:]\s*(.+)$", line)
            if match:
                return match.group(1).strip()
        return ""

    def _infer_genre_from_outlines(self, outlines_text: str) -> str:
        genre_labels: list[str] = []

        def append_if_matched(keywords: tuple[str, ...], label: str) -> None:
            if any(keyword in outlines_text for keyword in keywords) and label not in genre_labels:
                genre_labels.append(label)

        append_if_matched(("民国", "北平", "袁世凯", "督军", "国务院", "参议院", "新青年"), "民国乱世")
        append_if_matched(("军阀", "权力真空", "督军团", "外交部", "科长", "官场"), "权谋")
        append_if_matched(("报社", "报纸", "主编", "记者", "文稿", "写下"), "报业风云")
        append_if_matched(("戏台", "戏服", "唱腔", "凤冠", "点翠"), "时代传奇")
        append_if_matched(("命案", "追查", "线索", "凶手", "疑云"), "悬疑")
        append_if_matched(("宗门", "修仙", "飞升", "灵根", "法器"), "仙侠")
        append_if_matched(("机甲", "星舰", "太空", "联邦", "AI"), "科幻")

        return " / ".join(genre_labels[:3])

    def _infer_style_from_outlines(self, outlines_text: str) -> str:
        style_labels: list[str] = []

        def append_if_matched(keywords: tuple[str, ...], label: str) -> None:
            if any(keyword in outlines_text for keyword in keywords) and label not in style_labels:
                style_labels.append(label)

        append_if_matched(("压抑", "冷硬", "冷峻", "死寂", "冰冷", "撕裂", "血痕", "低泣", "困兽", "待宰", "断了"), "冷峻压抑")
        append_if_matched(("乱世", "军阀", "权力真空", "权贵", "施舍", "活人去填坑", "黑暗"), "残酷现实")
        append_if_matched(("社会全景", "社会层", "百姓", "茶馆", "黄包车夫", "市井", "时代"), "乱世群像")
        append_if_matched(("戏曲", "戏台", "点翠", "凤冠", "唱腔"), "细腻感官")
        append_if_matched(("报社", "文章", "反抗", "新文化"), "克制锋利")

        return "、".join(style_labels[:3])
