import asyncio
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from collections.abc import AsyncIterator, Awaitable, Coroutine
from typing import Any, cast
import uuid

import httpx
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.datetime_utils import utcnow
from backend.db.models import (
    AttemptStatus,
    Chapter,
    ChapterAttempt,
    ChapterOutline,
    ChapterReview,
    ChapterStatus,
    GenerationJob,
    GenerationMode,
    JobStatus,
    Project,
    ProjectStatus,
)
from backend.prompts.critic import build_critic_system_prompt
from backend.services.context_service import ContextService
from backend.services.event_service import EventService
from backend.services.generation_state import (
    accept_chapter,
    advance_after_chapter_success,
    apply_review_failure,
    build_generate_chapter_job,
    pause_queued_chapter,
    pause_current_generation,
)
from backend.services.memory_service import MemoryService
from backend.services.project_events import (
    ProjectEventType,
    chapter_passed_payload,
    chapter_rewriting_payload,
    chapter_truncated_payload,
    job_failed_payload,
    memory_updated_payload,
    pipeline_complete_payload,
    project_paused_payload,
    prompt_generated_payload,
)
from backend.services.prompt_service import PromptService
from backend.services.retention_cleanup_service import RetentionCleanupService
from backend.services.review_rules import build_review, is_review_passed, normalize_review_payload
from backend.services.runtime_service import RuntimeService
from backend.services.system_runtime_settings_service import SystemRuntimeSettingsService
from backend.llm.openai_compatible import LLMEmptyResponseError, LLMJSONDecodeError
from backend.llm.json_schemas import CRITIC_REVIEW_SCHEMA


class GenerationInterrupted(Exception):
    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


DEFERRED_RETRY_JOB_KEY = "__deferred_retry_job__"
MAX_CONTINUATION_ROUNDS = 3


@dataclass
class WriterGenerationResult:
    content: str
    truncation_reason: str | None = None


@dataclass
class ContinuationCandidate:
    should_continue: bool
    reason: str | None = None

class Pipeline:
    RECOVERABLE_MEMORY_STAGE_ERRORS = (
        TimeoutError,
        httpx.TimeoutException,
        json.JSONDecodeError,
        LLMJSONDecodeError,
    )
    CRITIC_REVIEW_TIMEOUT_MULTIPLIER = 2.0
    CRITIC_REVIEW_MIN_TIMEOUT_SECONDS = 420.0

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()
        self.events = EventService(session)
        self.prompt_service = PromptService(session)
        self.memory_service = MemoryService(session)
        self.context_service = ContextService(session)
        self.runtime = RuntimeService(session)
        self.runtime_settings = SystemRuntimeSettingsService(session)
        self.retention_cleanup = RetentionCleanupService(session)
        self._last_status_check_at: float = 0.0

    def _is_provider_auth_error(self, exc: Exception) -> bool:
        if not isinstance(exc, httpx.HTTPStatusError):
            return False
        response = getattr(exc, "response", None)
        return response is not None and response.status_code in {401, 403}

    async def _finalize_accepted_chapter(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        content: str,
        score: float | None,
        improvement_notes: str,
        runtime_settings: dict[str, Any],
        auto_accepted: bool,
        chapter_continue: bool = False,
        chapter_retry: bool = False,
        update_memory: bool = True,
        passed_event_extra: dict[str, Any] | None = None,
        current_job_id: uuid.UUID | None = None,
    ) -> None:
        accept_chapter(
            project=project,
            chapter=chapter,
            attempt=attempt,
            content=content,
            score=score,
            auto_accepted=auto_accepted,
            improvement_notes=improvement_notes,
        )

        async def run_memory_stage(memory_stage: str, operation: Awaitable[Any]) -> Any:
            return await self._run_cancelable(
                cast(Coroutine[Any, Any, Any], operation),
                project=project,
                stage=f"memory_update:{memory_stage}",
                runtime_settings=runtime_settings,
            )

        memory_result: dict[str, Any] | None = None
        if update_memory:
            try:
                memory_result = await self.memory_service.save_summary_and_revisions(
                    project.id,
                    chapter.chapter_number,
                    content,
                    confidence_threshold=runtime_settings["memory_auto_apply_confidence_threshold"],
                    stage_runner=run_memory_stage,
                )
            except self.RECOVERABLE_MEMORY_STAGE_ERRORS:
                memory_result = None
        if memory_result is not None:
            await self.events.append(
                project.id,
                ProjectEventType.MEMORY_UPDATED,
                memory_updated_payload(
                    chapter_number=chapter.chapter_number,
                    character_revision_count=memory_result["character_revision_count"],
                    world_revision_count=memory_result["world_revision_count"],
                    applied_revision_count=memory_result["applied_revision_count"],
                    needs_review_count=memory_result["needs_review_count"],
                    confidence_threshold=runtime_settings["memory_auto_apply_confidence_threshold"],
                ),
                chapter.chapter_number,
            )

        await self.events.append(
            project.id,
            ProjectEventType.CHAPTER_PASSED,
            chapter_passed_payload(score=score, auto_accepted=auto_accepted) | (passed_event_extra or {}),
            chapter.chapter_number,
        )
        await self.retention_cleanup.cleanup_chapter_content_chunks(project.id, chapter.chapter_number)

        next_chapter = await self._get_next_runnable_chapter(project.id, chapter.chapter_number)

        if chapter_continue or chapter_retry:
            if next_chapter is None:
                project.status = ProjectStatus.COMPLETED.value
                project.last_error = None
                await self.events.append(project.id, ProjectEventType.PIPELINE_COMPLETE, pipeline_complete_payload(project.id))
                await self.retention_cleanup.cleanup_completed_project(project.id, exclude_job_id=current_job_id)
            else:
                project.status = ProjectStatus.PAUSED.value
                project.last_error = (
                    '当前章节断点续写已完成，后续章节保持原样。'
                    if chapter_continue
                    else '当前章节重试已完成，后续章节保持原样。'
                )
                await self.events.append(
                    project.id,
                    ProjectEventType.PROJECT_PAUSED,
                    project_paused_payload(reason='chapter_continue_completed' if chapter_continue else 'chapter_retry_completed'),
                    chapter.chapter_number,
                )
            if memory_result is not None:
                await self.memory_service.sync_project_review_warning(project.id)
            return

        advance_result = advance_after_chapter_success(project=project, next_chapter=next_chapter)
        if advance_result.next_chapter is None:
            await self.events.append(project.id, ProjectEventType.PIPELINE_COMPLETE, pipeline_complete_payload(project.id))
            await self.retention_cleanup.cleanup_completed_project(project.id, exclude_job_id=current_job_id)
        elif advance_result.queued_job is None:
            await self.events.append(
                project.id,
                ProjectEventType.PROJECT_PAUSED,
                project_paused_payload(
                    reason="waiting_manual_resume",
                    next_chapter=advance_result.next_chapter.chapter_number,
                ),
                chapter.chapter_number,
            )
        else:
            try:
                await self._enqueue_generate_job(
                    advance_result.queued_job.project_id,
                    advance_result.queued_job.chapter_number,
                    payload=advance_result.queued_job.payload,
                )
            except ValueError:
                pass

        if memory_result is not None:
            await self.memory_service.sync_project_review_warning(project.id)

    async def process_generate_chapter(self, job: GenerationJob) -> None:
        if job.chapter_number is None:
            raise ValueError("generate_chapter 任务缺少 chapter_number")

        project = await self.session.get(Project, job.project_id)
        if project is None:
            raise ValueError("项目不存在")
        chapter = await self._get_chapter(job.project_id, job.chapter_number)
        if chapter is None:
            raise ValueError("章节不存在")

        if chapter.status == ChapterStatus.PASSED.value:
            return
        outline = await self._get_outline(job.project_id, job.chapter_number)
        await self.runtime.get_model_configs(job.project_id)
        runtime_settings = await self.runtime_settings.get_runtime_snapshot()
        hard_review_gates_enabled = bool(getattr(project, "hard_review_gates_enabled", True))
        is_rush_mode = getattr(project, "generation_mode", GenerationMode.STANDARD.value) == GenerationMode.RUSH.value

        attempt: ChapterAttempt | None = None
        context_payload: dict[str, Any] | None = None
        content_parts: list[str] = []
        continuation_prefix = ""
        continuation_round = 0

        try:
            await self._ensure_job_active(job, stage="generation")

            job_payload = job.payload if isinstance(job.payload, dict) else {}
            continuation_attempt = await self._resolve_continuation_attempt(chapter.id, job_payload)
            existing_attempt = None if job_payload.get("chapter_continue") else await self._resolve_resume_attempt(chapter.id, job_payload)
            if continuation_attempt is not None:
                attempt = await self._create_attempt(chapter.id)
                continuation_prefix = self._normalize_writer_output(continuation_attempt.content)
                content_parts = [continuation_prefix] if continuation_prefix else []
            elif existing_attempt is not None:
                attempt = existing_attempt
                continuation_prefix = self._normalize_writer_output(existing_attempt.content)
                content_parts = [continuation_prefix] if continuation_prefix else []
                attempt.status = AttemptStatus.RUNNING.value
                attempt.finished_at = None
            else:
                attempt = await self._create_attempt(chapter.id)
            assert attempt is not None
            chapter.status = ChapterStatus.WRITING.value
            chapter.last_error = None
            await self.events.append(
                project.id,
                ProjectEventType.CHAPTER_WRITING,
                {
                    "attempt": attempt.attempt_no,
                    "continuation": bool(continuation_prefix),
                    "continuation_source": (
                        "chapter_breakpoint_button"
                        if continuation_attempt is not None
                        else ("resume_saved_draft" if continuation_prefix else None)
                    ),
                    "saved_chars": len(continuation_prefix),
                },
                chapter.chapter_number,
            )
            await self.session.commit()

            if is_rush_mode:
                await self._process_rush_chapter(
                    job=job,
                    project=project,
                    chapter=chapter,
                    attempt=attempt,
                    job_payload=job_payload,
                    continuation_prefix=continuation_prefix,
                    content_parts=content_parts,
                    runtime_settings=runtime_settings,
                )
                return

            precheck = await self._run_cancelable(
                self.memory_service.continuity_precheck(
                    project.id,
                    chapter.chapter_number,
                    outline.outline_text,
                ),
                project=project,
                stage="precheck",
                runtime_settings=runtime_settings,
            )
            await self.events.append(project.id, ProjectEventType.PRECHECK_DONE, precheck, chapter.chapter_number)
            await self.session.commit()

            retry_guidance = await self.context_service.build_retry_guidance(
                project_id=project.id,
                chapter_number=chapter.chapter_number,
                fallback_feedback=chapter.improvement_notes or "",
            )
            should_force_regenerate_prompt = bool(job_payload.get("retry")) or chapter.retry_count > 0

            prompt_result = await self._resolve_prompt_for_chapter(
                project=project,
                chapter=chapter,
                precheck=precheck,
                retry_guidance=retry_guidance,
                force_regenerate=should_force_regenerate_prompt,
                runtime_settings=runtime_settings,
            )
            prompt = prompt_result.prompt
            assert attempt is not None

            if prompt_result.diagnostics is not None:
                await self.events.append(
                    project.id,
                    ProjectEventType.PROMPT_GENERATION_FALLBACK,
                    prompt_result.diagnostics,
                    chapter.chapter_number,
                )

            await self.events.append(
                project.id,
                ProjectEventType.PROMPT_GENERATED,
                prompt_generated_payload(
                    version_no=prompt.version_no,
                    source=prompt_result.source,
                    fallback=prompt_result.diagnostics,
                ),
                chapter.chapter_number,
            )

            attempt.prompt_version_id = prompt.id
            writer_client = await self.runtime.get_client(project.id, "writer")
            critic_client = await self.runtime.get_client(project.id, "critic")
            await self.session.commit()

            writer_streaming_enabled = bool(getattr(project, "writer_streaming_enabled", False))
            content = ""
            while True:
                context_payload = await self._run_cancelable(
                    self.context_service.build_writer_context(
                        project_id=project.id,
                        chapter_number=chapter.chapter_number,
                        effective_system_prompt=prompt.effective_system_prompt,
                        retry_feedback=chapter.improvement_notes or "",
                        precheck=precheck,
                        retry_guidance=retry_guidance,
                        continuation_prefix=continuation_prefix,
                        budget_overrides={
                            "target_tokens": runtime_settings["writer_target_input_tokens"],
                            "hard_limit_tokens": runtime_settings["writer_hard_limit_tokens"],
                        },
                    ),
                    project=project,
                    stage="build_writer_context",
                    runtime_settings=runtime_settings,
                )
                assert context_payload is not None

                attempt.input_snapshot = context_payload["layers"]
                attempt.input_tokens = context_payload["token_usage"]
                await self.session.commit()

                round_content_parts: list[str] = []
                content_parts = round_content_parts
                try:
                    writer_result = await self._generate_writer_content(
                        project=project,
                        chapter=chapter,
                        attempt=attempt,
                        writer_client=writer_client,
                        system_prompt=prompt.effective_system_prompt,
                        user_message=context_payload["user_message"],
                        existing_content_prefix=continuation_prefix,
                        content_parts=round_content_parts,
                        writer_streaming_enabled=writer_streaming_enabled,
                        runtime_settings=runtime_settings,
                    )
                except TimeoutError as exc:
                    await self._handle_writer_generation_failure(
                        project=project,
                        chapter=chapter,
                        attempt=attempt,
                        current_job=job,
                        feedback_message="正文生成超时，未拿到可用正文。请优先压缩场景复杂度并重试本章。",
                        event_reason="writer_generation_timeout",
                        error_message=str(exc),
                    )
                    return
                except (httpx.HTTPError, json.JSONDecodeError, LLMJSONDecodeError, ValueError) as exc:
                    if self._is_provider_auth_error(exc):
                        await self._handle_writer_provider_unavailable(
                            project=project,
                            chapter=chapter,
                            attempt=attempt,
                            feedback_message="写作模型鉴权失败或无可用权限，当前 writer 配置不可用。请检查 API Key、模型权限或网关配置后再重试。",
                            error_message=str(exc),
                        )
                        return
                    await self._handle_writer_generation_failure(
                        project=project,
                        chapter=chapter,
                        attempt=attempt,
                        current_job=job,
                        feedback_message="正文生成结果解析失败，未拿到可用正文。请优先收敛提示词并重试本章。",
                        event_reason="writer_generation_invalid_response",
                        error_message=str(exc),
                    )
                    return

                content = writer_result.content
                continuation = self._detect_continuation_candidate(
                    content=content,
                    truncation_reason=writer_result.truncation_reason,
                    continuation_round=continuation_round,
                )
                if not continuation.should_continue:
                    break

                continuation_round += 1
                continuation_prefix = content
                content_parts = [content]
                await self.events.append(
                    project.id,
                    ProjectEventType.CHAPTER_TRUNCATED,
                    chapter_truncated_payload(
                        attempt=attempt.attempt_no,
                        accumulated_chars=len(content),
                        reason=continuation.reason or "writer_output_truncated",
                    ),
                    chapter.chapter_number,
                )
                await self.events.append(
                    project.id,
                    ProjectEventType.CHAPTER_WRITING,
                    {
                        "attempt": attempt.attempt_no,
                        "continuation": True,
                        "continuation_source": "writer_truncated",
                        "saved_chars": len(content),
                    },
                    chapter.chapter_number,
                )
                await self.session.commit()

            attempt.content = content
            attempt.status = AttemptStatus.REVIEWED.value
            attempt.finished_at = utcnow()
            attempt.input_snapshot = context_payload["layers"]
            attempt.input_tokens = context_payload["token_usage"]
            attempt.output_tokens = self.context_service.estimate_tokens(content)

            await self._ensure_job_active(job, stage="before_review")
            chapter.status = ChapterStatus.REVIEWING.value
            await self.events.append(project.id, ProjectEventType.CHAPTER_REVIEWING, {}, chapter.chapter_number)
            await self.session.commit()

            critic_context = await self._run_cancelable(
                self.context_service.build_critic_context(
                    project_id=project.id,
                    chapter_number=chapter.chapter_number,
                    effective_system_prompt=prompt.effective_system_prompt,
                    content=content,
                    budget_overrides={
                        "target_tokens": runtime_settings["critic_target_input_tokens"],
                        "hard_limit_tokens": runtime_settings["critic_hard_limit_tokens"],
                    },
                ),
                project=project,
                stage="build_critic_context",
                runtime_settings=runtime_settings,
            )
            try:
                critic_timeout_seconds = max(
                    float(runtime_settings.get("llm_stage_timeout_seconds", self.settings.llm_stage_timeout_seconds))
                    * self.CRITIC_REVIEW_TIMEOUT_MULTIPLIER,
                    self.CRITIC_REVIEW_MIN_TIMEOUT_SECONDS,
                )
                review_payload = await self._run_cancelable(
                    critic_client.generate_json(
                        system_prompt=build_critic_system_prompt(
                            hard_gates_enabled=hard_review_gates_enabled,
                        ),
                        user_message=critic_context,
                        response_schema=CRITIC_REVIEW_SCHEMA,
                        schema_name="critic_review",
                    ),
                    project=project,
                    stage="critic_review",
                    timeout_seconds=critic_timeout_seconds,
                    runtime_settings=runtime_settings,
                )
            except TimeoutError as exc:
                await self._handle_critic_review_failure(
                    project=project,
                    chapter=chapter,
                    attempt=attempt,
                    current_job=job,
                    feedback_message="评审超时，未拿到有效评审结果。请优先自检大纲覆盖、指令遵循与收尾完整性后重写。",
                    event_reason="critic_review_timeout",
                    error_message=str(exc),
                )
                return
            except (json.JSONDecodeError, LLMJSONDecodeError) as exc:
                await self._handle_critic_review_failure(
                    project=project,
                    chapter=chapter,
                    attempt=attempt,
                    current_job=job,
                    feedback_message="评审结果解析失败，未拿到有效评审 JSON。请优先自检大纲覆盖、指令遵循与收尾完整性后重写。",
                    event_reason="critic_review_invalid_json",
                    error_message=str(exc),
                )
                return
            except LLMEmptyResponseError as exc:
                await self._handle_critic_provider_unavailable(
                    project=project,
                    chapter=chapter,
                    attempt=attempt,
                    feedback_message="评审模型返回空响应，当前 critic 配置不可用。请更换监督模型或网关后再重试。",
                    error_message=str(exc),
                )
                return
            normalized_review = self._normalize_review_payload(
                review_payload,
                runtime_settings=runtime_settings,
                hard_gates_enabled=hard_review_gates_enabled,
            )
            normalized_review["passed"] = self._is_passed(
                normalized_review,
                runtime_settings=runtime_settings,
                hard_gates_enabled=hard_review_gates_enabled,
            )
            await self._save_review(attempt.id, review_payload, normalized_review)

            await self.events.append(
                project.id,
                ProjectEventType.CHAPTER_SCORED,
                {
                    "score": normalized_review["overall_score"],
                    "passed": normalized_review["passed"],
                    "review_thresholds": {
                        "hard_gates_enabled": hard_review_gates_enabled,
                        "overall_score_threshold": runtime_settings["review_overall_score_threshold"],
                        "outline_score_threshold": runtime_settings["review_outline_score_threshold"],
                        "instruction_score_threshold": runtime_settings["review_instruction_score_threshold"],
                    },
                },
                chapter.chapter_number,
            )
            await self.session.commit()

            improvement_notes = "\n".join(normalized_review.get("improvement_suggestions", []))
            auto_accept_critic_failed = bool(getattr(project, "auto_accept_critic_failed", False))
            auto_accept_on_max_retries = bool(getattr(project, "auto_accept_on_max_retries", False))
            is_final_retry_attempt = chapter.retry_count >= max(0, project.max_retries - 1)
            auto_accept_due_to_retry_limit = bool(
                auto_accept_on_max_retries and is_final_retry_attempt and not normalized_review["passed"]
            )
            is_auto_accepted = bool(
                not normalized_review["passed"] and (auto_accept_critic_failed or auto_accept_due_to_retry_limit)
            )
            if normalized_review["passed"] or is_auto_accepted:
                await self._finalize_accepted_chapter(
                    project=project,
                    chapter=chapter,
                    attempt=attempt,
                    content=content,
                    score=normalized_review["overall_score"],
                    improvement_notes=improvement_notes,
                    runtime_settings=runtime_settings,
                    auto_accepted=is_auto_accepted,
                    chapter_continue=bool(job_payload.get("chapter_continue")),
                    chapter_retry=bool(job_payload.get("chapter_retry")),
                    current_job_id=getattr(job, "id", None),
                )
            else:
                chapter.improvement_notes = improvement_notes
                attempt.status = AttemptStatus.REJECTED.value
                retry_job = apply_review_failure(project=project, chapter=chapter)
                if retry_job is None:
                    await self.events.append(
                        project.id,
                        ProjectEventType.PROJECT_PAUSED,
                        project_paused_payload(reason="max_retries_exceeded"),
                        chapter.chapter_number,
                    )
                else:
                    self._defer_retry_job(job, retry_job)
                    await self.events.append(
                        project.id,
                        ProjectEventType.CHAPTER_REWRITING,
                        chapter_rewriting_payload(
                            retry_count=chapter.retry_count,
                            reason="review_failed",
                            failure_summary=self._build_review_failure_summary(normalized_review),
                        ),
                        chapter.chapter_number,
                    )
            await self.session.commit()
        except GenerationInterrupted as exc:
            await self._pause_current_generation(
                project=project,
                chapter=chapter,
                attempt=attempt,
                content=self._normalize_writer_output(getattr(attempt, "content", None)) or "".join(content_parts),
                context_payload=context_payload,
                stage=exc.stage,
            )
            return

    async def _process_rush_chapter(
        self,
        *,
        job: GenerationJob,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        job_payload: dict[str, Any],
        continuation_prefix: str,
        content_parts: list[str],
        runtime_settings: dict[str, Any],
    ) -> None:
        writer_client = await self.runtime.get_client(project.id, "writer")
        writer_streaming_enabled = bool(getattr(project, "writer_streaming_enabled", False))
        content = ""
        continuation_round = 0
        context_payload: dict[str, Any] | None = None
        system_prompt = "你是 Writer AI，负责根据给定资料直接创作小说章节正文。只输出正文。"

        while True:
            context_payload = await self._run_cancelable(
                self.context_service.build_rush_writer_context(
                    project_id=project.id,
                    chapter_number=chapter.chapter_number,
                    previous_chapter_count=int(getattr(project, "rush_previous_chapter_count", 10) or 0),
                    continuation_prefix=continuation_prefix,
                    budget_overrides={
                        "target_tokens": runtime_settings["writer_target_input_tokens"],
                        "hard_limit_tokens": runtime_settings["writer_hard_limit_tokens"],
                    },
                ),
                project=project,
                stage="build_rush_writer_context",
                runtime_settings=runtime_settings,
            )
            assert context_payload is not None

            attempt.input_snapshot = context_payload["layers"]
            attempt.input_tokens = context_payload["token_usage"]
            await self.session.commit()

            round_content_parts: list[str] = []
            content_parts[:] = round_content_parts
            try:
                writer_result = await self._generate_writer_content(
                    project=project,
                    chapter=chapter,
                    attempt=attempt,
                    writer_client=writer_client,
                    system_prompt=system_prompt,
                    user_message=context_payload["user_message"],
                    existing_content_prefix=continuation_prefix,
                    content_parts=round_content_parts,
                    writer_streaming_enabled=writer_streaming_enabled,
                    runtime_settings=runtime_settings,
                )
            except TimeoutError as exc:
                await self._handle_writer_generation_failure(
                    project=project,
                    chapter=chapter,
                    attempt=attempt,
                    current_job=job,
                    feedback_message="Rush 正文生成超时，未拿到可用正文。请降低前文引用章数或重试本章。",
                    event_reason="rush_writer_generation_timeout",
                    error_message=str(exc),
                )
                return
            except (httpx.HTTPError, json.JSONDecodeError, LLMJSONDecodeError, ValueError) as exc:
                if self._is_provider_auth_error(exc):
                    await self._handle_writer_provider_unavailable(
                        project=project,
                        chapter=chapter,
                        attempt=attempt,
                        feedback_message="写作模型鉴权失败或无可用权限，当前 writer 配置不可用。请检查 API Key、模型权限或网关配置后再重试。",
                        error_message=str(exc),
                    )
                    return
                await self._handle_writer_generation_failure(
                    project=project,
                    chapter=chapter,
                    attempt=attempt,
                    current_job=job,
                    feedback_message="Rush 正文生成结果解析失败，未拿到可用正文。请降低前文引用章数或重试本章。",
                    event_reason="rush_writer_generation_invalid_response",
                    error_message=str(exc),
                )
                return

            content = writer_result.content
            continuation = self._detect_continuation_candidate(
                content=content,
                truncation_reason=writer_result.truncation_reason,
                continuation_round=continuation_round,
            )
            if not continuation.should_continue:
                break

            continuation_round += 1
            continuation_prefix = content
            content_parts[:] = [content]
            await self.events.append(
                project.id,
                ProjectEventType.CHAPTER_TRUNCATED,
                chapter_truncated_payload(
                    attempt=attempt.attempt_no,
                    accumulated_chars=len(content),
                    reason=continuation.reason or "writer_output_truncated",
                ),
                chapter.chapter_number,
            )
            await self.events.append(
                project.id,
                ProjectEventType.CHAPTER_WRITING,
                {
                    "attempt": attempt.attempt_no,
                    "continuation": True,
                    "continuation_source": "writer_truncated",
                    "saved_chars": len(content),
                },
                chapter.chapter_number,
            )
            await self.session.commit()

        attempt.content = content
        attempt.status = AttemptStatus.REVIEWED.value
        attempt.finished_at = utcnow()
        attempt.input_snapshot = context_payload["layers"]
        attempt.input_tokens = context_payload["token_usage"]
        attempt.output_tokens = self.context_service.estimate_tokens(content)

        await self._ensure_job_active(job, stage="before_rush_accept")
        await self._finalize_accepted_chapter(
            project=project,
            chapter=chapter,
            attempt=attempt,
            content=content,
            score=None,
            improvement_notes="",
            runtime_settings=runtime_settings,
            auto_accepted=False,
            chapter_continue=bool(job_payload.get("chapter_continue")),
            chapter_retry=bool(job_payload.get("chapter_retry")),
            update_memory=False,
            passed_event_extra={"mode": GenerationMode.RUSH.value},
            current_job_id=getattr(job, "id", None),
        )
        await self.session.commit()

    async def _resolve_prompt_for_chapter(
        self,
        *,
        project: Project,
        chapter: Chapter,
        precheck: dict[str, Any] | None,
        retry_guidance: dict[str, Any],
        force_regenerate: bool,
        runtime_settings: dict[str, Any],
    ):
        try:
            return await self._run_cancelable(
                self.prompt_service.get_or_create_effective_prompt_result(
                    project_id=project.id,
                    chapter_number=chapter.chapter_number,
                    precheck=precheck,
                    retry_info=retry_guidance["prompt_retry_info"],
                    force_regenerate=force_regenerate,
                ),
                project=project,
                stage="prompt_generation",
                runtime_settings=runtime_settings,
            )
        except TimeoutError as exc:
            return await self.prompt_service.create_timeout_fallback_prompt_result(
                project_id=project.id,
                chapter_number=chapter.chapter_number,
                precheck=precheck,
                retry_info=retry_guidance["prompt_retry_info"],
                error_message=str(exc),
            )

    async def _enqueue_generate_job(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        payload: dict[str, object] | None = None,
        *,
        ignore_job_id: uuid.UUID | None = None,
    ) -> GenerationJob:
        if not hasattr(self.session, "execute"):
            job = build_generate_chapter_job(
                project_id=project_id,
                chapter_number=chapter_number,
                payload=payload,
            )
            self.session.add(job)
            return job

        existing_job = await self._get_active_job(project_id, chapter_number, ignore_job_id=ignore_job_id)
        if existing_job is not None:
            return existing_job

        job = build_generate_chapter_job(
            project_id=project_id,
            chapter_number=chapter_number,
            payload=payload,
        )
        stmt = (
            insert(GenerationJob)
            .values(
                id=job.id,
                project_id=job.project_id,
                chapter_number=job.chapter_number,
                job_type=job.job_type,
                status=job.status,
                payload=job.payload,
                lease_owner=job.lease_owner,
                lease_expires_at=job.lease_expires_at,
                run_count=job.run_count,
                last_error=job.last_error,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    GenerationJob.project_id,
                    GenerationJob.chapter_number,
                    GenerationJob.job_type,
                ],
                index_where=text("chapter_number IS NOT NULL AND status IN ('queued', 'leased')"),
            )
        )
        result = await self.session.execute(stmt)
        if result.rowcount:
            return job

        existing_job = await self._get_active_job(project_id, chapter_number, ignore_job_id=ignore_job_id)
        if existing_job is not None:
            return existing_job

        raise ValueError("该章节的任务已在另一处处理完毕，无法重复入队")

    async def _handle_critic_review_failure(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        current_job: GenerationJob,
        feedback_message: str,
        event_reason: str,
        error_message: str,
    ) -> None:
        existing_notes = (chapter.improvement_notes or "").strip()
        if feedback_message not in existing_notes:
            chapter.improvement_notes = "\n".join(
                part for part in (existing_notes, feedback_message) if part
            )
        attempt.status = AttemptStatus.ERRORED.value
        retry_job = apply_review_failure(project=project, chapter=chapter)
        if retry_job is None:
            await self.events.append(
                project.id,
                ProjectEventType.PROJECT_PAUSED,
                project_paused_payload(reason="max_retries_exceeded", phase="critic_review"),
                chapter.chapter_number,
            )
        else:
            self._defer_retry_job(current_job, retry_job)
            await self.events.append(
                project.id,
                ProjectEventType.CHAPTER_REWRITING,
                chapter_rewriting_payload(
                    retry_count=chapter.retry_count,
                    reason=event_reason,
                    error=error_message,
                ),
                chapter.chapter_number,
            )
        await self.session.commit()

    async def _handle_critic_provider_unavailable(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        feedback_message: str,
        error_message: str,
    ) -> None:
        chapter.status = ChapterStatus.FAILED.value
        chapter.last_error = error_message
        existing_notes = (chapter.improvement_notes or "").strip()
        if feedback_message not in existing_notes:
            chapter.improvement_notes = "\n".join(
                part for part in (existing_notes, feedback_message) if part
            )
        attempt.status = AttemptStatus.ERRORED.value
        attempt.finished_at = utcnow()
        project.status = ProjectStatus.PAUSED.value
        project.last_error = feedback_message
        await self.events.append(
            project.id,
            ProjectEventType.PROJECT_PAUSED,
            project_paused_payload(reason="critic_provider_unavailable", phase="critic_review"),
            chapter.chapter_number,
        )
        await self.session.commit()

    async def _handle_writer_generation_failure(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        current_job: GenerationJob,
        feedback_message: str,
        event_reason: str,
        error_message: str,
    ) -> None:
        existing_notes = (chapter.improvement_notes or "").strip()
        if feedback_message not in existing_notes:
            chapter.improvement_notes = "\n".join(
                part for part in (existing_notes, feedback_message) if part
            )
        attempt.status = AttemptStatus.ERRORED.value
        attempt.finished_at = utcnow()
        retry_job = apply_review_failure(project=project, chapter=chapter)
        if retry_job is None:
            await self.events.append(
                project.id,
                ProjectEventType.PROJECT_PAUSED,
                project_paused_payload(reason="max_retries_exceeded", phase="writer_generate"),
                chapter.chapter_number,
            )
        else:
            self._defer_retry_job(current_job, retry_job)
            await self.events.append(
                project.id,
                ProjectEventType.CHAPTER_REWRITING,
                chapter_rewriting_payload(
                    retry_count=chapter.retry_count,
                    reason=event_reason,
                    error=error_message,
                ),
                chapter.chapter_number,
            )
        await self.session.commit()

    async def _handle_writer_provider_unavailable(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        feedback_message: str,
        error_message: str,
    ) -> None:
        chapter.status = ChapterStatus.FAILED.value
        chapter.last_error = error_message
        existing_notes = (chapter.improvement_notes or "").strip()
        if feedback_message not in existing_notes:
            chapter.improvement_notes = "\n".join(
                part for part in (existing_notes, feedback_message) if part
            )
        attempt.status = AttemptStatus.ERRORED.value
        attempt.finished_at = utcnow()
        project.status = ProjectStatus.PAUSED.value
        project.last_error = feedback_message
        await self.events.append(
            project.id,
            ProjectEventType.PROJECT_PAUSED,
            project_paused_payload(reason="writer_provider_unavailable", phase="writer_generate"),
            chapter.chapter_number,
        )
        await self.session.commit()

    def _build_review_failure_summary(self, normalized_review: dict[str, Any]) -> dict[str, Any]:
        blocking_issues = self._normalize_review_text_list(normalized_review.get("blocking_issues"), limit=3)
        violated_instructions = self._normalize_review_text_list(normalized_review.get("violated_instructions"), limit=3)
        improvement_suggestions = self._normalize_review_text_list(normalized_review.get("improvement_suggestions"), limit=3)

        return {
            "overall_score": float(normalized_review.get("overall_score", 0.0)),
            "blocking_issue_count": len(self._normalize_review_text_list(normalized_review.get("blocking_issues"))),
            "top_blocking_issues": blocking_issues,
            "violated_instruction_count": len(self._normalize_review_text_list(normalized_review.get("violated_instructions"))),
            "top_violated_instructions": violated_instructions,
            "improvement_suggestion_count": len(self._normalize_review_text_list(normalized_review.get("improvement_suggestions"))),
            "top_improvement_suggestions": improvement_suggestions,
        }

    def _normalize_review_text_list(self, value: Any, *, limit: int | None = None) -> list[str]:
        if not isinstance(value, list):
            return []

        items: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                raw = item.get("issue") or item.get("reason") or item.get("summary") or item.get("text")
                text = raw.strip() if isinstance(raw, str) else ""
            else:
                text = ""
            if text:
                items.append(text)

        if limit is not None:
            return items[:limit]
        return items

    def _defer_retry_job(self, current_job: GenerationJob, retry_job: GenerationJob) -> None:
        current_payload = current_job.payload if isinstance(current_job.payload, dict) else {}
        current_job.payload = {
            **current_payload,
            DEFERRED_RETRY_JOB_KEY: {
                "project_id": str(retry_job.project_id),
                "chapter_number": retry_job.chapter_number,
                "payload": retry_job.payload or {},
            },
        }

    async def _create_attempt(self, chapter_id: uuid.UUID, prompt_id: uuid.UUID | None = None) -> ChapterAttempt:
        stmt = select(func.max(ChapterAttempt.attempt_no)).where(ChapterAttempt.chapter_id == chapter_id)
        result = await self.session.execute(stmt)
        current = result.scalar_one()
        attempt = ChapterAttempt(
            chapter_id=chapter_id,
            attempt_no=(current or 0) + 1,
            prompt_version_id=prompt_id,
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def _get_chapter(self, project_id: uuid.UUID, chapter_number: int) -> Chapter | None:
        stmt = select(Chapter).where(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_next_runnable_chapter(self, project_id: uuid.UUID, after_chapter_number: int) -> Chapter | None:
        await self._repair_orphan_queued_chapters(project_id, after_chapter_number=after_chapter_number)
        stmt = (
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.chapter_number > after_chapter_number,
                Chapter.status.in_(
                    [
                        ChapterStatus.PENDING.value,
                        ChapterStatus.FAILED.value,
                        ChapterStatus.PAUSED.value,
                    ]
                ),
            )
            .order_by(Chapter.chapter_number.asc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _repair_orphan_queued_chapters(
        self,
        project_id: uuid.UUID,
        *,
        after_chapter_number: int | None = None,
    ) -> int:
        stmt = select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.status == ChapterStatus.QUEUED.value,
        )
        if after_chapter_number is not None:
            stmt = stmt.where(Chapter.chapter_number > after_chapter_number)
        result = await self.session.execute(stmt)
        chapters = list(result.scalars())

        repaired = 0
        for chapter in chapters:
            if not await self._has_active_job(project_id, chapter.chapter_number):
                pause_queued_chapter(chapter)
                repaired += 1
        return repaired

    async def _has_active_job(self, project_id: uuid.UUID, chapter_number: int | None = None) -> bool:
        return await self._get_active_job(project_id, chapter_number) is not None

    async def _get_active_job(
        self,
        project_id: uuid.UUID,
        chapter_number: int | None = None,
        *,
        ignore_job_id: uuid.UUID | None = None,
    ) -> GenerationJob | None:
        now = utcnow()
        stmt = select(GenerationJob).where(
            GenerationJob.project_id == project_id,
            or_(
                GenerationJob.status == JobStatus.QUEUED.value,
                (
                    GenerationJob.status == JobStatus.LEASED.value
                )
                & (GenerationJob.lease_expires_at.is_not(None))
                & (GenerationJob.lease_expires_at >= now),
            ),
        )
        if chapter_number is not None:
            stmt = stmt.where(GenerationJob.chapter_number == chapter_number)
        if ignore_job_id is not None:
            stmt = stmt.where(GenerationJob.id != ignore_job_id)
        stmt = stmt.order_by(GenerationJob.created_at.asc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _ensure_job_active(self, job: GenerationJob, *, stage: str = "generation") -> None:
        if not hasattr(self.session, "execute") or not hasattr(job, "id"):
            return

        row = await self.session.execute(
            select(GenerationJob.status, GenerationJob.lease_owner, GenerationJob.lease_expires_at).where(GenerationJob.id == job.id)
        )
        current = row.one_or_none()
        if current is None:
            raise GenerationInterrupted(stage)

        status, lease_owner, lease_expires_at = current
        if status != JobStatus.LEASED.value:
            raise GenerationInterrupted(stage)
        if lease_owner != self.settings.worker_name:
            raise GenerationInterrupted(stage)
        if lease_expires_at is None or lease_expires_at < utcnow():
            raise GenerationInterrupted(stage)
        project = await self.session.get(Project, job.project_id)
        if project is None:
            raise GenerationInterrupted(stage)
        await self._ensure_project_running(project, stage=stage)

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

    def _build_review(
        self,
        attempt_id: uuid.UUID,
        payload: dict[str, Any],
        normalized_payload: dict[str, Any] | None = None,
    ) -> ChapterReview:
        return build_review(attempt_id, payload, normalized_payload)

    async def _save_review(
        self,
        attempt_id: uuid.UUID,
        payload: dict[str, Any],
        normalized_payload: dict[str, Any],
    ) -> ChapterReview:
        review = await self._get_review_for_attempt(attempt_id)
        if review is None:
            review = self._build_review(attempt_id, payload, normalized_payload)
            self.session.add(review)
            await self.session.flush()
            return review

        updated_review = self._build_review(attempt_id, payload, normalized_payload)
        review.overall_score = updated_review.overall_score
        review.passed = updated_review.passed
        review.outline_score = updated_review.outline_score
        review.instruction_score = updated_review.instruction_score
        review.continuity_score = updated_review.continuity_score
        review.character_score = updated_review.character_score
        review.writing_score = updated_review.writing_score
        review.blocking_issues = updated_review.blocking_issues
        review.uncovered_outline_points = updated_review.uncovered_outline_points
        review.violated_instructions = updated_review.violated_instructions
        review.improvement_suggestions = updated_review.improvement_suggestions
        review.non_scoring_notes = updated_review.non_scoring_notes
        review.raw_json = updated_review.raw_json
        await self.session.flush()
        return review

    def _is_passed(
        self,
        payload: dict[str, Any],
        *,
        runtime_settings: dict[str, Any] | None = None,
        hard_gates_enabled: bool = True,
    ) -> bool:
        return is_review_passed(
            payload,
            runtime_settings=runtime_settings,
            hard_gates_enabled=hard_gates_enabled,
        )

    def _normalize_review_payload(
        self,
        payload: Any,
        *,
        runtime_settings: dict[str, Any] | None = None,
        hard_gates_enabled: bool = True,
    ) -> dict[str, Any]:
        return normalize_review_payload(
            payload,
            runtime_settings=runtime_settings,
            hard_gates_enabled=hard_gates_enabled,
        )

    async def _generate_writer_content(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        writer_client,
        system_prompt: str,
        user_message: str,
        existing_content_prefix: str,
        content_parts: list[str],
        writer_streaming_enabled: bool,
        runtime_settings: dict[str, Any],
    ) -> WriterGenerationResult:
        if not writer_streaming_enabled:
            return await self._generate_writer_content_non_stream(
                project=project,
                chapter=chapter,
                attempt=attempt,
                writer_client=writer_client,
                system_prompt=system_prompt,
                user_message=user_message,
                existing_content_prefix=existing_content_prefix,
                runtime_settings=runtime_settings,
            )

        try:
            content = await self._stream_with_pause_support(
                project=project,
                chapter=chapter,
                attempt=attempt,
                content_parts=content_parts,
                system_prompt=system_prompt,
                user_message=user_message,
                writer_client=writer_client,
                runtime_settings=runtime_settings,
            )
        except httpx.RemoteProtocolError as exc:
            if not self._is_recoverable_stream_disconnect(exc):
                raise
            recovered_content = self._recover_usable_stream_content(attempt=attempt, content_parts=content_parts)
            if recovered_content is None:
                raise
            await self.events.append(
                project.id,
                ProjectEventType.WRITER_STREAM_FALLBACK_SUCCEEDED,
                {
                    "reason": "stream_disconnected_after_usable_content",
                    "fallback_chars": len(recovered_content),
                    "streamed_chars": len(recovered_content),
                },
                chapter.chapter_number,
            )
            await self.session.commit()
            return WriterGenerationResult(
                content=recovered_content,
                truncation_reason="stream_disconnected_after_usable_content",
            )
        return await self._finalize_writer_content(
            project=project,
            chapter=chapter,
            attempt=attempt,
            writer_client=writer_client,
            system_prompt=system_prompt,
            user_message=user_message,
            existing_content_prefix=existing_content_prefix,
            streamed_content=content,
            runtime_settings=runtime_settings,
        )

    def _recover_usable_stream_content(self, *, attempt: ChapterAttempt, content_parts: list[str]) -> str | None:
        candidates = [
            self._normalize_writer_output("".join(content_parts)),
            self._normalize_writer_output(getattr(attempt, "content", None)),
        ]
        for candidate in candidates:
            if self._is_usable_writer_content(candidate):
                return candidate
        return None

    def _is_recoverable_stream_disconnect(self, exc: httpx.RemoteProtocolError) -> bool:
        message = str(exc).lower()
        return "incomplete chunked read" in message or "peer closed connection without sending complete message body" in message

    async def _generate_writer_content_non_stream(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        writer_client,
        system_prompt: str,
        user_message: str,
        existing_content_prefix: str,
        runtime_settings: dict[str, Any] | None = None,
    ) -> WriterGenerationResult:
        stage_timeout_seconds = (runtime_settings or {}).get("llm_stage_timeout_seconds", self.settings.llm_stage_timeout_seconds)
        fallback_read_timeout = max(stage_timeout_seconds, 180.0)
        fallback_attempts = 2 if hasattr(writer_client, "generate_text_fallback") else max(1, self.settings.llm_max_retries + 1)
        fallback_backoff_total = 0.0
        if fallback_attempts > 1:
            fallback_backoff_total = sum(
                self.settings.llm_retry_backoff_seconds * (2**idx)
                for idx in range(fallback_attempts - 1)
            )
        timeout_seconds = max(
            stage_timeout_seconds + 120.0,
            fallback_read_timeout * fallback_attempts + fallback_backoff_total + 30.0,
        )
        await self.events.append(
            project.id,
            ProjectEventType.WRITER_NON_STREAM_STARTED,
            {"timeout_seconds": timeout_seconds},
            chapter.chapter_number,
        )
        await self.session.commit()
        generate_operation = getattr(writer_client, "generate_text_fallback", None)
        if generate_operation is None:
            generate_operation = writer_client.generate
        content = await self._run_cancelable(
            generate_operation(
                system_prompt=system_prompt,
                user_message=user_message,
            ),
            project=project,
            stage="writer_generate",
            timeout_seconds=timeout_seconds,
            runtime_settings=runtime_settings,
        )
        normalized_content = self._normalize_writer_output(content)
        merged_content = self._merge_continuation_content(existing_content_prefix, normalized_content)
        attempt.content = merged_content
        await self.session.commit()
        await self.events.append(
            project.id,
            ProjectEventType.WRITER_NON_STREAM_SUCCEEDED,
            {"content_chars": len(merged_content)},
            chapter.chapter_number,
        )
        if self._is_usable_writer_content(merged_content):
            return WriterGenerationResult(content=merged_content)
        raise ValueError("writer non-stream returned unusable content")

    async def _run_cancelable(
        self,
        operation: Coroutine[Any, Any, Any],
        *,
        project: Project,
        stage: str,
        poll_seconds: float = 0.5,
        timeout_seconds: float | None = None,
        runtime_settings: dict[str, Any] | None = None,
    ) -> Any:
        task = asyncio.create_task(operation)
        started_at = utcnow()
        stage_timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else (runtime_settings or {}).get("llm_stage_timeout_seconds", self.settings.llm_stage_timeout_seconds)
        )
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=poll_seconds)
                if task in done:
                    return await task
                elapsed = (utcnow() - started_at).total_seconds()
                if elapsed > stage_timeout_seconds:
                    raise TimeoutError(f"stage {stage} timed out after {stage_timeout_seconds:.0f}s")
                await self._ensure_project_running(project, stage=stage)
        except Exception:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            raise

    async def _close_stream_iterator(self, iterator, *, timeout_seconds: float = 2.0) -> None:
        aclose = getattr(iterator, 'aclose', None)
        if aclose is None or not callable(aclose):
            return
        try:
            await asyncio.wait_for(cast(Awaitable[Any], aclose()), timeout=timeout_seconds)
        except (asyncio.TimeoutError, Exception):
            return

    def _get_writer_no_text_timeout_seconds(self, runtime_settings: dict[str, Any] | None = None) -> float:
        base_timeout = (runtime_settings or {}).get("llm_stage_timeout_seconds", self.settings.llm_stage_timeout_seconds)
        return max(base_timeout + 120.0, 300.0)

    async def _return_stream_content_after_no_text_timeout(
        self,
        *,
        project: Project,
        chapter: Chapter,
        iterator: AsyncIterator[str],
        pending_chunk_task: asyncio.Task[Any] | None,
        content_parts: list[str],
        waited_seconds: float,
    ) -> str:
        await self.events.append(
            project.id,
            ProjectEventType.WRITER_STREAM_NO_TEXT_FALLBACK,
            {"waited_seconds": waited_seconds},
            chapter.chapter_number,
        )
        await self.session.commit()
        if pending_chunk_task is not None and not pending_chunk_task.done():
            pending_chunk_task.cancel()
            with suppress(asyncio.CancelledError):
                await pending_chunk_task
        await self._close_stream_iterator(iterator)
        return "".join(content_parts)

    async def _stream_with_pause_support(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        content_parts: list[str],
        system_prompt: str,
        user_message: str,
        writer_client,
        runtime_settings: dict[str, Any] | None = None,
        poll_seconds: float = 0.5,
    ) -> str:
        stream = writer_client.stream_generate(
            system_prompt=system_prompt,
            user_message=user_message,
        )
        iterator: AsyncIterator[str] = stream.__aiter__()
        pending_chunk_task: asyncio.Task[Any] | None = None
        last_progress_at = utcnow()
        stream_started_at = last_progress_at
        no_text_timeout_seconds = self._get_writer_no_text_timeout_seconds(runtime_settings)
        stage_timeout_seconds = (runtime_settings or {}).get("llm_stage_timeout_seconds", self.settings.llm_stage_timeout_seconds)
        received_text = False
        chunk_batch_count = 0
        last_batch_commit_at = utcnow()
        BATCH_COMMIT_CHUNK_COUNT = 20
        BATCH_COMMIT_INTERVAL_SECONDS = 2.0
        try:
            while True:
                next_chunk = cast(Coroutine[Any, Any, str], anext(iterator))
                pending_chunk_task = asyncio.create_task(next_chunk)
                while True:
                    done, _ = await asyncio.wait({pending_chunk_task}, timeout=poll_seconds)
                    if pending_chunk_task in done:
                        break
                    now = utcnow()
                    if not received_text and (now - stream_started_at).total_seconds() > no_text_timeout_seconds:
                        return await self._return_stream_content_after_no_text_timeout(
                            project=project,
                            chapter=chapter,
                            iterator=iterator,
                            pending_chunk_task=pending_chunk_task,
                            content_parts=content_parts,
                            waited_seconds=no_text_timeout_seconds,
                        )
                    if (now - last_progress_at).total_seconds() > stage_timeout_seconds:
                        raise TimeoutError(
                            f"stage writer_stream timed out after {stage_timeout_seconds:.0f}s without progress"
                        )
                    await self._ensure_project_running(project, stage="writer_stream")
                try:
                    chunk = await pending_chunk_task
                except StopAsyncIteration:
                    break
                now = utcnow()
                if chunk:
                    received_text = True
                    last_progress_at = now
                    content_parts.append(chunk)
                    attempt.content = (attempt.content or "") + chunk
                    await self.events.append(project.id, ProjectEventType.CONTENT_CHUNK, {"chunk": chunk}, chapter.chapter_number)
                    chunk_batch_count += 1
                    should_batch_commit = (
                        chunk_batch_count >= BATCH_COMMIT_CHUNK_COUNT
                        or (now - last_batch_commit_at).total_seconds() >= BATCH_COMMIT_INTERVAL_SECONDS
                    )
                    if should_batch_commit:
                        await self.session.commit()
                        chunk_batch_count = 0
                        last_batch_commit_at = now
                else:
                    last_progress_at = now
                    if not received_text and (now - stream_started_at).total_seconds() > no_text_timeout_seconds:
                        return await self._return_stream_content_after_no_text_timeout(
                            project=project,
                            chapter=chapter,
                            iterator=iterator,
                            pending_chunk_task=None,
                            content_parts=content_parts,
                            waited_seconds=no_text_timeout_seconds,
                        )
                await self._ensure_project_running(project, stage="writer_stream")
            if chunk_batch_count > 0:
                await self.session.commit()
            return "".join(content_parts)
        except Exception:
            if pending_chunk_task is not None and not pending_chunk_task.done():
                pending_chunk_task.cancel()
                with suppress(asyncio.CancelledError):
                    await pending_chunk_task
            await self._close_stream_iterator(iterator)
            raise

    async def _finalize_writer_content(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt,
        writer_client,
        system_prompt: str,
        user_message: str,
        existing_content_prefix: str,
        streamed_content: str,
        runtime_settings: dict[str, Any] | None = None,
    ) -> WriterGenerationResult:
        normalized_streamed = self._normalize_writer_output(streamed_content)
        merged_streamed = self._merge_continuation_content(existing_content_prefix, normalized_streamed)
        if self._is_usable_writer_content(merged_streamed):
            attempt.content = merged_streamed
            return WriterGenerationResult(content=merged_streamed)

        supports_fallback = getattr(writer_client, "supports_stream_text_fallback", None)
        if callable(supports_fallback) and supports_fallback():
            stage_timeout_seconds = (runtime_settings or {}).get("llm_stage_timeout_seconds", self.settings.llm_stage_timeout_seconds)
            fallback_timeout_seconds = max(stage_timeout_seconds + 120.0, 300.0)
            await self.events.append(
                project.id,
                ProjectEventType.WRITER_STREAM_FALLBACK_STARTED,
                {
                    "reason": "empty_or_low_quality_stream_output",
                    "streamed_chars": len(normalized_streamed),
                    "timeout_seconds": fallback_timeout_seconds,
                },
                chapter.chapter_number,
            )
            await self.session.commit()
            fallback_operation = getattr(writer_client, "generate_text_fallback", writer_client.generate)
            fallback_content = await self._run_cancelable(
                fallback_operation(
                    system_prompt=system_prompt,
                    user_message=user_message,
                ),
                project=project,
                stage="writer_generate_fallback",
                timeout_seconds=fallback_timeout_seconds,
                runtime_settings=runtime_settings,
            )
            normalized_fallback = self._normalize_writer_output(fallback_content)
            merged_fallback = self._merge_continuation_content(existing_content_prefix, normalized_fallback)
            attempt.content = merged_fallback
            await self.session.commit()
            await self.events.append(
                project.id,
                ProjectEventType.WRITER_STREAM_FALLBACK_SUCCEEDED,
                {
                    "fallback_chars": len(merged_fallback),
                    "streamed_chars": len(merged_streamed),
                },
                chapter.chapter_number,
            )
            if self._is_usable_writer_content(merged_fallback):
                return WriterGenerationResult(content=merged_fallback)
            raise ValueError("writer fallback returned unusable content")

        raise ValueError("writer stream returned unusable content")

    async def _resolve_resume_attempt(
        self,
        chapter_id: uuid.UUID,
        job_payload: dict[str, Any],
    ) -> ChapterAttempt | None:
        if not job_payload.get("resume"):
            return None
        requested_attempt_id = job_payload.get("continue_from_attempt_id")
        if isinstance(requested_attempt_id, str):
            requested_attempt = await self._get_attempt_by_id(chapter_id, requested_attempt_id)
            if requested_attempt is None or not await self._attempt_can_resume(requested_attempt):
                return None
            content = self._normalize_writer_output(requested_attempt.content)
            if not content:
                return None
            return requested_attempt

        latest_attempt = await self._get_latest_attempt(chapter_id)
        if latest_attempt is None or not await self._attempt_can_resume(latest_attempt):
            return None
        content = self._normalize_writer_output(latest_attempt.content)
        if not content:
            return None
        return latest_attempt

    async def _resolve_continuation_attempt(
        self,
        chapter_id: uuid.UUID,
        job_payload: dict[str, Any],
    ) -> ChapterAttempt | None:
        if not job_payload.get("chapter_continue"):
            return None
        requested_attempt_id = job_payload.get("continue_from_attempt_id")
        if not isinstance(requested_attempt_id, str):
            return None
        requested_attempt = await self._get_attempt_by_id(chapter_id, requested_attempt_id)
        if requested_attempt is None:
            return None
        content = self._normalize_writer_output(requested_attempt.content)
        if not content:
            return None
        return requested_attempt

    async def _get_attempt_by_id(self, chapter_id: uuid.UUID, attempt_id: str) -> ChapterAttempt | None:
        if not hasattr(self.session, "execute"):
            return None
        try:
            parsed_attempt_id = uuid.UUID(attempt_id)
        except ValueError:
            return None

        stmt = (
            select(ChapterAttempt)
            .where(
                ChapterAttempt.chapter_id == chapter_id,
                ChapterAttempt.id == parsed_attempt_id,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_latest_attempt(self, chapter_id: uuid.UUID) -> ChapterAttempt | None:
        stmt = (
            select(ChapterAttempt)
            .where(ChapterAttempt.chapter_id == chapter_id)
            .order_by(ChapterAttempt.attempt_no.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_review_for_attempt(self, attempt_id: uuid.UUID) -> ChapterReview | None:
        if not hasattr(self.session, "execute"):
            return None
        stmt = select(ChapterReview).where(ChapterReview.attempt_id == attempt_id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _attempt_can_resume(self, attempt: ChapterAttempt) -> bool:
        if getattr(attempt, "status", None) not in {
            AttemptStatus.RUNNING.value,
            AttemptStatus.ERRORED.value,
        }:
            return False
        review = await self._get_review_for_attempt(attempt.id)
        return review is None

    def _detect_continuation_candidate(
        self,
        *,
        content: str,
        truncation_reason: str | None,
        continuation_round: int,
    ) -> ContinuationCandidate:
        if continuation_round >= MAX_CONTINUATION_ROUNDS:
            return ContinuationCandidate(False)
        if not content:
            return ContinuationCandidate(False)
        stripped = content.rstrip()
        if not stripped:
            return ContinuationCandidate(False)
        if truncation_reason is not None:
            if self._has_terminal_writer_ending(stripped):
                return ContinuationCandidate(False)
            return ContinuationCandidate(True, truncation_reason)
        tail = stripped[-1]
        if self._has_terminal_writer_ending(stripped):
            return ContinuationCandidate(False)
        if stripped.endswith(("，", "、", "：", "；", "（", "[", "【", "“")):
            return ContinuationCandidate(True, "writer_output_ended_mid_sentence")
        recent_tail = stripped[-40:]
        if "\n\n" not in recent_tail and tail not in {"。", "！", "？", "”", '"', "』", "」", "…"}:
            return ContinuationCandidate(True, "writer_output_may_be_incomplete")
        return ContinuationCandidate(False)

    def _has_terminal_writer_ending(self, content: str) -> bool:
        return bool(content) and content[-1] in {"。", "！", "？", "”", '"', "』", "」", "…"}

    def _merge_continuation_content(self, existing_content_prefix: str, new_content: str) -> str:
        prefix = self._normalize_writer_output(existing_content_prefix)
        suffix = self._normalize_writer_output(new_content)
        if not prefix:
            return suffix
        if not suffix:
            return prefix
        if suffix.startswith(prefix):
            return suffix
        if prefix.endswith(suffix):
            return prefix
        return f"{prefix}\n{suffix}" if not prefix.endswith("\n") and not suffix.startswith("\n") else f"{prefix}{suffix}"

    def _normalize_writer_output(self, content: str | None) -> str:
        if not content:
            return ""
        return content.replace("\r\n", "\n").strip()

    def _is_usable_writer_content(self, content: str) -> bool:
        if not content:
            return False
        visible_chars = [char for char in content if not char.isspace()]
        if len(visible_chars) < 80:
            return False
        replacement_count = content.count("�")
        ascii_letters = sum(1 for char in content if char.isascii() and char.isalpha())
        cjk_chars = sum(1 for char in content if "\u4e00" <= char <= "\u9fff")
        if replacement_count >= 3:
            return False
        if cjk_chars == 0 and ascii_letters > 0:
            return False
        if cjk_chars > 0 and ascii_letters > cjk_chars * 0.6:
            return False
        return True

    async def _ensure_project_running(self, project: Project, *, stage: str = "generation") -> None:
        now = time.monotonic()
        if now - self._last_status_check_at < 2.0:
            if project.status != ProjectStatus.RUNNING.value:
                raise GenerationInterrupted(stage)
            return
        self._last_status_check_at = now
        with self.session.no_autoflush:
            current_status = await self.session.scalar(
                select(Project.status).where(Project.id == project.id)
            )
        if current_status != ProjectStatus.RUNNING.value:
            raise GenerationInterrupted(stage)

    async def _pause_current_generation(
        self,
        *,
        project: Project,
        chapter: Chapter,
        attempt: ChapterAttempt | None,
        content: str,
        context_payload: dict[str, Any] | None,
        stage: str,
    ) -> None:
        existing_content = attempt.content if attempt is not None and attempt.content is not None else ''
        pause_current_generation(
            project=project,
            chapter=chapter,
            attempt=attempt,
            content=content,
            input_snapshot=context_payload.get("layers") if context_payload is not None else None,
            input_tokens=context_payload.get("token_usage") if context_payload is not None else None,
            output_tokens=self.context_service.estimate_tokens(content or existing_content),
        )

        await self.events.append(
            project.id,
            ProjectEventType.CHAPTER_PAUSED,
            {
                "reason": "manual_pause",
                "stage": stage,
                "saved_chars": len(content),
            },
            chapter.chapter_number,
        )
        await self.session.commit()
