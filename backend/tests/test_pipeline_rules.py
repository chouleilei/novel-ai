import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import httpx
import pytest

from backend.db.models import SystemRuntimeSetting
from backend.datetime_utils import utcnow
from backend.llm.openai_compatible import LLMEmptyResponseError, LLMJSONDecodeError
from backend.services.memory_service import MemoryService
from backend.services.prompt_service import PromptGenerationResult
from backend.worker.pipeline import DEFERRED_RETRY_JOB_KEY, Pipeline, WriterGenerationResult


class _ScalarListResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self._rows


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def one_or_none(self):
        return self._row


class _ExecuteResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


def make_pipeline(session) -> Pipeline:
    pipeline = Pipeline(session)  # type: ignore[arg-type]
    pipeline.retention_cleanup = SimpleNamespace(
        cleanup_chapter_content_chunks=AsyncMock(),
        cleanup_completed_project=AsyncMock(),
    )
    return pipeline


def make_runtime_settings() -> SystemRuntimeSetting:
    return SystemRuntimeSetting(
        id=1,
        review_overall_score_threshold=8.0,
        review_outline_score_threshold=8.0,
        review_instruction_score_threshold=8.0,
        memory_auto_apply_confidence_threshold=0.75,
        writer_target_input_tokens=64000,
        writer_hard_limit_tokens=96000,
        critic_target_input_tokens=32000,
        critic_hard_limit_tokens=48000,
        llm_stage_timeout_seconds=180.0,
    )


@pytest.mark.asyncio
async def test_finalize_accepted_chapter_cleans_chunks_without_completed_cleanup_when_more_chapters():
    pipeline = make_pipeline(SimpleNamespace())
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=1,
        status="queued",
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        auto_accepted=False,
        last_error=None,
        improvement_notes=None,
    )
    attempt = SimpleNamespace(id=uuid.uuid4(), status="running")
    next_chapter = SimpleNamespace(chapter_number=2, status="pending", last_error=None)
    pipeline.events.append = AsyncMock()
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=next_chapter)

    await pipeline._finalize_accepted_chapter(
        project=project,
        chapter=chapter,
        attempt=attempt,
        content="正文",
        score=8.5,
        improvement_notes="",
        runtime_settings={},
        auto_accepted=False,
        update_memory=False,
    )

    pipeline.retention_cleanup.cleanup_chapter_content_chunks.assert_awaited_once_with(project.id, 1)
    pipeline.retention_cleanup.cleanup_completed_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_accepted_chapter_cleans_completed_project_when_no_next_chapter():
    pipeline = make_pipeline(SimpleNamespace())
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=1,
        status="queued",
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        auto_accepted=False,
        last_error=None,
        improvement_notes=None,
    )
    attempt = SimpleNamespace(id=uuid.uuid4(), status="running")
    pipeline.events.append = AsyncMock()
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)

    await pipeline._finalize_accepted_chapter(
        project=project,
        chapter=chapter,
        attempt=attempt,
        content="正文",
        score=8.5,
        improvement_notes="",
        runtime_settings={},
        auto_accepted=False,
        update_memory=False,
    )

    assert project.status == "completed"
    pipeline.retention_cleanup.cleanup_chapter_content_chunks.assert_awaited_once_with(project.id, 1)
    pipeline.retention_cleanup.cleanup_completed_project.assert_awaited_once_with(project.id, exclude_job_id=None)


@pytest.mark.asyncio
async def test_pipeline_critic_review_uses_extended_timeout():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.8, "passed": True}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    observed_timeouts: list[float | None] = []

    async def fake_run_cancelable(operation, **kwargs):
        if kwargs.get("stage") == "critic_review":
            observed_timeouts.append(kwargs.get("timeout_seconds"))
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=None)
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")
    pipeline._normalize_review_payload = Mock(return_value={
        "overall_score": 8.8,
        "passed": True,
        "improvement_suggestions": [],
        "blocking_issues": [],
        "dimensions": {
            "outline_adherence": {"score": 9},
            "instruction_adherence": {"score": 9},
            "continuity_consistency": {"score": 8},
            "character_consistency": {"score": 8},
            "writing_quality": {"score": 8},
        },
    })
    pipeline._is_passed = Mock(return_value=True)
    pipeline._build_review = Mock(return_value=SimpleNamespace(id=uuid.uuid4(), overall_score=8.8, passed=True))
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._enqueue_generate_job = AsyncMock()

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert observed_timeouts == [420.0]


@pytest.mark.asyncio
async def test_pipeline_rush_mode_skips_prompt_critic_and_memory_stages():
    project = SimpleNamespace(
        id=uuid.uuid4(),
        status="running",
        current_chapter=0,
        last_error=None,
        auto_mode=True,
        max_retries=5,
        writer_streaming_enabled=False,
        generation_mode="rush",
        rush_previous_chapter_count=10,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=project), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="旧建议",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        auto_accepted=False,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    runtime_settings = {
        "review_overall_score_threshold": 8.0,
        "review_outline_score_threshold": 8.0,
        "review_instruction_score_threshold": 8.0,
        "memory_auto_apply_confidence_threshold": 0.75,
        "writer_target_input_tokens": 64000,
        "writer_hard_limit_tokens": 96000,
        "critic_target_input_tokens": 32000,
        "critic_hard_limit_tokens": 48000,
        "llm_stage_timeout_seconds": 180.0,
    }
    writer_client = SimpleNamespace()

    async def fake_run_cancelable(operation, **_kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._generate_writer_content = AsyncMock(
        return_value=WriterGenerationResult(content="Rush 生成正文，长度足够用于直接通过。")
    )
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(return_value=writer_client)
    pipeline.runtime_settings.get_runtime_snapshot = AsyncMock(return_value=runtime_settings)
    pipeline.context_service.build_rush_writer_context = AsyncMock(
        return_value={"user_message": "rush writer context", "layers": {"current_outline": "第2章大纲"}, "token_usage": 123}
    )
    pipeline.context_service.estimate_tokens = Mock(return_value=456)
    pipeline.memory_service.continuity_precheck = AsyncMock()
    pipeline.memory_service.save_summary_and_revisions = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock()
    pipeline.context_service.build_writer_context = AsyncMock()
    pipeline.context_service.build_critic_context = AsyncMock()

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    pipeline.memory_service.continuity_precheck.assert_not_awaited()
    pipeline.memory_service.save_summary_and_revisions.assert_not_awaited()
    pipeline.prompt_service.get_or_create_effective_prompt_result.assert_not_awaited()
    pipeline.context_service.build_writer_context.assert_not_awaited()
    pipeline.context_service.build_critic_context.assert_not_awaited()
    pipeline.runtime.get_client.assert_awaited_once_with(project.id, "writer")
    pipeline.context_service.build_rush_writer_context.assert_awaited_once()
    assert chapter.status == "passed"
    assert chapter.final_content == "Rush 生成正文，长度足够用于直接通过。"
    assert chapter.final_score is None
    assert attempt.status == "accepted"
    assert project.status == "completed"


def test_pipeline_is_passed_can_disable_hard_gates():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "overall_score": 6.4,
        "passed": True,
        "dimensions": {
            "outline_adherence": {"score": 5.8},
            "instruction_adherence": {"score": 5.5},
            "continuity_consistency": {"score": 7.5},
            "character_consistency": {"score": 7.3},
            "writing_quality": {"score": 7.4},
        },
        "blocking_issues": ["关键落点缺失"],
    }

    assert pipeline._is_passed(payload, hard_gates_enabled=False) is True


class ReviewStub:
    def __init__(
        self,
        *,
        overall_score: float,
        blocking_issues: list[str] | None = None,
        uncovered_outline_points: list[str] | None = None,
        violated_instructions: list[str] | None = None,
        improvement_suggestions: list[str] | None = None,
    ) -> None:
        self.overall_score = overall_score
        self.blocking_issues = blocking_issues or []
        self.uncovered_outline_points = uncovered_outline_points or []
        self.violated_instructions = violated_instructions or []
        self.improvement_suggestions = improvement_suggestions or []


class FakeReviewResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


def test_is_passed_requires_hard_gates():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "overall_score": 8.5,
        "dimensions": {
            "outline_adherence": {"score": 8},
            "instruction_adherence": {"score": 8},
            "continuity_consistency": {"score": 8},
            "character_consistency": {"score": 8},
            "writing_quality": {"score": 8},
        },
        "blocking_issues": [],
    }
    assert pipeline._is_passed(payload) is True


def test_is_passed_rejects_when_instruction_score_too_low():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "overall_score": 9.1,
        "dimensions": {
            "outline_adherence": {"score": 9},
            "instruction_adherence": {"score": 7},
            "continuity_consistency": {"score": 9},
            "character_consistency": {"score": 8},
            "writing_quality": {"score": 9},
        },
        "blocking_issues": [],
    }
    assert pipeline._is_passed(payload) is False


def test_is_passed_uses_runtime_thresholds_when_review_payload_omits_passed_flag():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "overall_score": 7.6,
        "dimensions": {
            "outline_adherence": {"score": 7.1},
            "instruction_adherence": {"score": 6.6},
            "continuity_consistency": {"score": 8.5},
            "character_consistency": {"score": 8.4},
            "writing_quality": {"score": 8.3},
        },
        "blocking_issues": [],
    }
    runtime_settings = {
        "review_overall_score_threshold": 7.5,
        "review_outline_score_threshold": 7.0,
        "review_instruction_score_threshold": 6.5,
    }

    assert pipeline._is_passed(payload, runtime_settings=runtime_settings) is True


def test_is_passed_still_rejects_when_blocking_issues_exist_under_relaxed_thresholds():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "overall_score": 7.8,
        "dimensions": {
            "outline_adherence": {"score": 7.4},
            "instruction_adherence": {"score": 6.8},
            "continuity_consistency": {"score": 8.2},
            "character_consistency": {"score": 8.1},
            "writing_quality": {"score": 8.0},
        },
        "blocking_issues": ["核心情节缺失"],
    }
    runtime_settings = {
        "review_overall_score_threshold": 7.5,
        "review_outline_score_threshold": 7.0,
        "review_instruction_score_threshold": 6.5,
    }

    assert pipeline._is_passed(payload, runtime_settings=runtime_settings) is False


def test_build_review_normalizes_missing_dimensions():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "overall_score": 8.6,
        "passed": True,
        "improvement_suggestions": "延续当前线索推进",
    }

    review = pipeline._build_review(uuid.uuid4(), payload)

    assert review.overall_score == 8.6
    assert review.passed is True
    assert review.outline_score == 8.6
    assert review.instruction_score == 8.6
    assert review.continuity_score == 8.6
    assert review.character_score == 8.6
    assert review.writing_score == 8.6
    assert review.improvement_suggestions == ["延续当前线索推进"]
    assert review.raw_json == payload


def test_is_passed_infers_missing_dimensions_from_overall_score():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "overall_score": 8.2,
        "passed": True,
        "blocking_issues": [],
    }

    assert pipeline._is_passed(payload) is True


def test_is_passed_handles_missing_scores_as_failed():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "passed": False,
        "blocking_issues": ["存在关键情节缺失"],
    }

    assert pipeline._is_passed(payload) is False


@pytest.mark.asyncio
async def test_pipeline_get_next_runnable_chapter_repairs_orphan_queued_before_selecting() -> None:
    project_id = uuid.uuid4()
    chapter = SimpleNamespace(
        chapter_number=5,
        status='queued',
        last_error=None,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarListResult([chapter]),
                _ScalarResult(chapter),
            ]
        )
    )
    pipeline = make_pipeline(session)
    pipeline._has_active_job = AsyncMock(return_value=False)

    result = await pipeline._get_next_runnable_chapter(project_id, 4)

    assert result is chapter
    assert chapter.status == 'paused'
    assert chapter.last_error == '已在执行前停止，可点击继续后重跑本章。'
    pipeline._has_active_job.assert_awaited_once_with(project_id, 5)
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_pipeline_repair_orphan_queued_keeps_chapter_when_job_is_active() -> None:
    project_id = uuid.uuid4()
    chapter = SimpleNamespace(
        chapter_number=6,
        status='queued',
        last_error=None,
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=_ScalarListResult([chapter])))
    pipeline = make_pipeline(session)
    pipeline._has_active_job = AsyncMock(return_value=True)

    repaired = await pipeline._repair_orphan_queued_chapters(project_id)

    assert repaired == 0
    assert chapter.status == 'queued'
    assert chapter.last_error is None
    pipeline._has_active_job.assert_awaited_once_with(project_id, 6)
    session.execute.assert_awaited_once()




def test_normalize_review_payload_preserves_structured_issue_lists_with_issue_key():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "overall_score": 7.6,
        "dimensions": {
            "outline_adherence": {"score": 7, "comment": "关键落点不足"},
            "instruction_adherence": {"score": 8, "comment": "指令基本满足"},
            "continuity_consistency": {"score": 8, "comment": "连续性稳定"},
            "character_consistency": {"score": 8, "comment": "人物一致"},
            "writing_quality": {"score": 8, "comment": "文笔稳定"},
        },
        "blocking_issues": [
            {"issue": "缺少主角亲手触发总控的动作", "impact": "关键大纲点未落地"},
        ],
        "uncovered_outline_points": [
            {"title": "展示‘官方抹去’的直接证据"},
        ],
        "violated_instructions": [
            {"reason": "收尾没有停在明确悬念上"},
        ],
    }

    normalized = pipeline._normalize_review_payload(payload)

    assert normalized["blocking_issues"] == ["缺少主角亲手触发总控的动作"]
    assert normalized["uncovered_outline_points"] == ["展示‘官方抹去’的直接证据"]
    assert normalized["violated_instructions"] == ["收尾没有停在明确悬念上"]






def test_normalize_review_payload_uses_runtime_thresholds_for_implicit_failure_reasons():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]
    payload = {
        "overall_score": 7.6,
        "scores": {
            "outline_adherence": {"score": 7.1, "reason": "大纲覆盖不足"},
            "instruction_adherence": {"score": 6.2, "reason": "指令执行偏弱"},
            "continuity_consistency": {"score": 8.0},
            "character_consistency": {"score": 8.0},
            "writing_quality": {"score": 8.0},
        },
    }
    runtime_settings = {
        "review_overall_score_threshold": 7.5,
        "review_outline_score_threshold": 7.0,
        "review_instruction_score_threshold": 6.5,
    }

    normalized = pipeline._normalize_review_payload(payload, runtime_settings=runtime_settings)

    assert normalized["passed"] is False
    assert normalized["blocking_issues"] == []
    assert normalized["violated_instructions"] == ["指令执行偏弱"]


@pytest.mark.asyncio
async def test_pipeline_retry_uses_aggregated_retry_guidance_for_prompt_and_writer_context():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=3,
        status="queued",
        retry_count=1,
        improvement_notes="补强钟楼压迫感",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第3章：沈夜在钟楼追查密钥")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=2,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=4, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.4, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "阻塞问题：\n- 缺少钟楼压迫感",
        "critic_review_history": "同章重试复盘：\n- 缺少钟楼压迫感",
        "prompt_retry_info": {
            "recent_failed_attempts": 1,
            "failed_reasons": ["优先修复：缺少钟楼压迫感"],
        },
    }
    memory_result = {
        "character_revision_count": 0,
        "world_revision_count": 0,
        "applied_revision_count": 0,
        "needs_review_count": 0,
    }

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": ["承接上一章尾声"]})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=3, payload={"retry": True})

    await pipeline.process_generate_chapter(job)

    pipeline.prompt_service.get_or_create_effective_prompt_result.assert_awaited_once()
    prompt_call = pipeline.prompt_service.get_or_create_effective_prompt_result.await_args
    assert prompt_call is not None
    prompt_kwargs = prompt_call.kwargs
    assert prompt_kwargs["force_regenerate"] is True
    assert prompt_kwargs["retry_info"] == retry_guidance["prompt_retry_info"]

    writer_call = pipeline.context_service.build_writer_context.await_args
    assert writer_call is not None
    writer_kwargs = writer_call.kwargs
    assert writer_kwargs["retry_guidance"] == retry_guidance


@pytest.mark.asyncio
async def test_pipeline_prompt_generation_timeout_falls_back_and_continues():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="fallback prompt")
    prompt_result = PromptGenerationResult(
        prompt=prompt,
        source="fallback",
        diagnostics={"failure_type": "stage_timeout", "message": "stage prompt_generation timed out after 180s"},
    )
    writer_client = SimpleNamespace()
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 0,
        "world_revision_count": 0,
        "applied_revision_count": 0,
        "needs_review_count": 0,
    }

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get("stage")
        if stage == "prompt_generation":
            operation.close()
            raise TimeoutError("stage prompt_generation timed out after 180s")
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock()
    pipeline.prompt_service.create_timeout_fallback_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    pipeline.prompt_service.create_timeout_fallback_prompt_result.assert_awaited_once()
    pipeline._stream_with_pause_support.assert_awaited_once()
    event_types = [call.args[1] for call in pipeline.events.append.await_args_list]
    assert "prompt_generation_fallback" in event_types


@pytest.mark.asyncio
async def test_pipeline_retry_detection_ignores_non_dict_job_payload():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 0,
        "world_revision_count": 0,
        "applied_revision_count": 0,
        "needs_review_count": 0,
    }

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(
        return_value=(
            "第2章 正文\n\n"
            "钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。"
            "他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。"
            "短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。"
        )
    )
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload=["retry"])

    await pipeline.process_generate_chapter(job)

    prompt_call = pipeline.prompt_service.get_or_create_effective_prompt_result.await_args
    assert prompt_call is not None
    prompt_kwargs = prompt_call.kwargs
    assert prompt_kwargs["force_regenerate"] is False


@pytest.mark.asyncio
async def test_pipeline_critic_review_timeout_retries_chapter_instead_of_failing_worker():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock())
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get("stage")
        if stage == "critic_review":
            operation.close()
            raise TimeoutError("stage critic_review timed out after 180s")
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.retry_count == 1
    assert chapter.status == "queued"
    assert attempt.status == "errored"
    assert "评审超时" in chapter.improvement_notes
    deferred_retry = job.payload[DEFERRED_RETRY_JOB_KEY]
    assert deferred_retry["chapter_number"] == 2
    assert deferred_retry["payload"] == {"retry": True}
    assert project.status == "running"
    assert not any(call.args[1] == "job_failed" for call in pipeline.events.append.await_args_list)
    rewriting_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "chapter_rewriting")
    assert rewriting_event.args[2]["reason"] == "critic_review_timeout"


@pytest.mark.asyncio
async def test_pipeline_critic_review_timeout_pauses_when_retry_limit_reached():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=1, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock())
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get("stage")
        if stage == "critic_review":
            operation.close()
            raise TimeoutError("stage critic_review timed out after 180s")
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.retry_count == 1
    assert chapter.status == "failed"
    assert attempt.status == "errored"
    assert project.status == "paused"
    assert project.last_error == "章节达到最大重试次数，项目已暂停。"
    assert not any(getattr(call.args[0], "job_type", None) == "generate_chapter" for call in session.add.call_args_list)
    paused_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "project_paused")
    assert paused_event.args[2]["reason"] == "max_retries_exceeded"
    assert paused_event.args[2]["phase"] == "critic_review"


@pytest.mark.asyncio
async def test_pipeline_auto_accepts_when_critic_review_fails_but_project_setting_enabled():
    project = SimpleNamespace(
        id=uuid.uuid4(),
        status="running",
        current_chapter=0,
        last_error=None,
        auto_mode=False,
        auto_accept_critic_failed=True,
        max_retries=5,
        writer_streaming_enabled=True,
    )
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        auto_accepted=False,
        improvement_notes="",
        last_error=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 6.2, "passed": False}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 1,
        "world_revision_count": 0,
        "applied_revision_count": 1,
        "needs_review_count": 0,
    }

    async def fake_run_cancelable(operation, **_kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 自动放行\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他重新核对失踪档案与现场遗留痕迹，确认管理员刻意回避的那条旧记录正是案件突破口。短暂迟疑后，他压下不安，决定立刻潜入档案室，把被人藏起的那页记录完整翻出。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")
    pipeline._normalize_review_payload = Mock(return_value={
        "overall_score": 6.2,
        "passed": False,
        "improvement_suggestions": ["补强收尾钩子"],
        "blocking_issues": ["收尾力度不足"],
        "dimensions": {
            "outline_adherence": {"score": 7},
            "instruction_adherence": {"score": 7},
            "continuity_consistency": {"score": 6},
            "character_consistency": {"score": 6},
            "writing_quality": {"score": 6},
        },
    })
    pipeline._is_passed = Mock(return_value=False)
    pipeline._build_review = Mock(return_value=SimpleNamespace(id=uuid.uuid4(), overall_score=6.2, passed=False))
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=SimpleNamespace(chapter_number=3, status="pending", last_error=None))
    pipeline._enqueue_generate_job = AsyncMock()

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.status == "passed"
    assert chapter.auto_accepted is True
    assert chapter.final_content == "第2章 自动放行\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他重新核对失踪档案与现场遗留痕迹，确认管理员刻意回避的那条旧记录正是案件突破口。短暂迟疑后，他压下不安，决定立刻潜入档案室，把被人藏起的那页记录完整翻出。"
    assert chapter.final_score == 6.2
    assert chapter.retry_count == 0
    assert chapter.improvement_notes == "补强收尾钩子"
    assert attempt.status == "accepted"
    assert project.status == "paused"
    assert project.current_chapter == 2
    pipeline.memory_service.save_summary_and_revisions.assert_awaited_once_with(
        project.id,
        2,
        "第2章 自动放行\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他重新核对失踪档案与现场遗留痕迹，确认管理员刻意回避的那条旧记录正是案件突破口。短暂迟疑后，他压下不安，决定立刻潜入档案室，把被人藏起的那页记录完整翻出。",
        confidence_threshold=0.75,
        stage_runner=ANY,
    )
    pipeline.memory_service.sync_project_review_warning.assert_awaited_once_with(project.id)
    event_types = [call.args[1] for call in pipeline.events.append.await_args_list]
    assert "chapter_rewriting" not in event_types
    assert "memory_updated" in event_types
    passed_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "chapter_passed")
    assert passed_event.args[2] == {"score": 6.2, "auto_accepted": True}


@pytest.mark.asyncio
async def test_pipeline_auto_accepts_when_final_retry_review_fails_and_retry_limit_setting_enabled():
    project = SimpleNamespace(
        id=uuid.uuid4(),
        status="running",
        current_chapter=0,
        last_error=None,
        auto_mode=False,
        auto_accept_critic_failed=False,
        auto_accept_on_max_retries=True,
        max_retries=2,
        writer_streaming_enabled=True,
    )
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=1,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        auto_accepted=False,
        improvement_notes="",
        last_error=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=2,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 6.4, "passed": False}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 0,
        "world_revision_count": 0,
        "applied_revision_count": 0,
        "needs_review_count": 0,
    }

    async def fake_run_cancelable(operation, **_kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 最终重试自动放行\n\n钟楼外的风声沿着石壁回旋，沈夜把纸条边缘压进掌心，重新比对失踪档案的时间线与现场细节。他顺着墙缝摸到那枚冰冷金属件时，终于意识到这就是撬开整起案件的关键物证，于是当场决定今夜潜入档案室取回真相。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")
    pipeline._normalize_review_payload = Mock(return_value={
        "overall_score": 6.4,
        "passed": False,
        "improvement_suggestions": ["补足情绪回响"],
        "blocking_issues": ["结尾张力不足"],
        "dimensions": {
            "outline_adherence": {"score": 7},
            "instruction_adherence": {"score": 7},
            "continuity_consistency": {"score": 6},
            "character_consistency": {"score": 6},
            "writing_quality": {"score": 6},
        },
    })
    pipeline._is_passed = Mock(return_value=False)
    pipeline._build_review = Mock(return_value=SimpleNamespace(id=uuid.uuid4(), overall_score=6.4, passed=False))
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=SimpleNamespace(chapter_number=3, status="pending", last_error=None))
    pipeline._enqueue_generate_job = AsyncMock()

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.status == "passed"
    assert chapter.auto_accepted is True
    assert chapter.retry_count == 1
    assert chapter.final_content == "第2章 最终重试自动放行\n\n钟楼外的风声沿着石壁回旋，沈夜把纸条边缘压进掌心，重新比对失踪档案的时间线与现场细节。他顺着墙缝摸到那枚冰冷金属件时，终于意识到这就是撬开整起案件的关键物证，于是当场决定今夜潜入档案室取回真相。"
    assert chapter.improvement_notes == "补足情绪回响"
    assert attempt.status == "accepted"
    assert project.current_chapter == 2
    event_types = [call.args[1] for call in pipeline.events.append.await_args_list]
    assert "chapter_rewriting" not in event_types
    passed_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "chapter_passed")
    assert passed_event.args[2] == {"score": 6.4, "auto_accepted": True}


@pytest.mark.asyncio
async def test_pipeline_critic_review_invalid_json_retries_chapter_instead_of_failing_worker():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock())
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get("stage")
        if stage == "critic_review":
            operation.close()
            raise LLMJSONDecodeError("Expecting value: line 1 column 1 (char 0)", raw_preview="")
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.retry_count == 1
    assert chapter.status == "queued"
    assert attempt.status == "errored"
    assert "评审结果解析失败" in chapter.improvement_notes
    deferred_retry = job.payload[DEFERRED_RETRY_JOB_KEY]
    assert deferred_retry["chapter_number"] == 2
    assert deferred_retry["payload"] == {"retry": True}
    assert project.status == "running"
    assert not any(call.args[1] == "job_failed" for call in pipeline.events.append.await_args_list)
    rewriting_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "chapter_rewriting")
    assert rewriting_event.args[2]["reason"] == "critic_review_invalid_json"
    assert rewriting_event.args[2]["error"] == "Expecting value: line 1 column 1 (char 0)"


@pytest.mark.asyncio
async def test_pipeline_critic_review_invalid_json_pauses_when_retry_limit_reached():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=1, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock())
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get("stage")
        if stage == "critic_review":
            operation.close()
            raise LLMJSONDecodeError("Expecting value: line 1 column 1 (char 0)", raw_preview="")
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.retry_count == 1
    assert chapter.status == "failed"
    assert attempt.status == "errored"
    assert project.status == "paused"
    assert project.last_error == "章节达到最大重试次数，项目已暂停。"
    assert not any(getattr(call.args[0], "job_type", None) == "generate_chapter" for call in session.add.call_args_list)
    assert not any(call.args[1] == "job_failed" for call in pipeline.events.append.await_args_list)
    paused_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "project_paused")
    assert paused_event.args[2]["reason"] == "max_retries_exceeded"
    assert paused_event.args[2]["phase"] == "critic_review"


@pytest.mark.asyncio
async def test_pipeline_critic_empty_response_pauses_immediately_without_retrying():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock())
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get("stage")
        if stage == "critic_review":
            operation.close()
            raise LLMEmptyResponseError("llm response contained no usable assistant content", raw_preview='{"content":null}')
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.retry_count == 0
    assert chapter.status == "failed"
    assert chapter.last_error == "llm response contained no usable assistant content"
    assert attempt.status == "errored"
    assert project.status == "paused"
    assert "critic 配置不可用" in project.last_error
    assert DEFERRED_RETRY_JOB_KEY not in job.payload
    paused_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "project_paused")
    assert paused_event.args[2]["reason"] == "critic_provider_unavailable"
    assert paused_event.args[2]["phase"] == "critic_review"


@pytest.mark.asyncio
async def test_pipeline_writer_generation_invalid_response_retries_chapter_instead_of_failing_worker():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(
        supports_stream_text_fallback=lambda: True,
        generate_text_fallback=AsyncMock(),
        generate=AsyncMock(),
    )
    critic_client = SimpleNamespace(generate_json=AsyncMock())
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get("stage")
        if stage == "writer_generate_fallback":
            operation.close()
            raise LLMJSONDecodeError("Expecting value: line 1 column 1 (char 0)", raw_preview="")
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.retry_count == 1
    assert chapter.status == "queued"
    assert attempt.status == "errored"
    assert attempt.finished_at is not None
    assert "正文生成结果解析失败" in chapter.improvement_notes
    deferred_retry = job.payload[DEFERRED_RETRY_JOB_KEY]
    assert deferred_retry["chapter_number"] == 2
    assert deferred_retry["payload"] == {"retry": True}
    assert project.status == "running"
    assert not any(call.args[1] == "job_failed" for call in pipeline.events.append.await_args_list)
    rewriting_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "chapter_rewriting")
    assert rewriting_event.args[2]["reason"] == "writer_generation_invalid_response"
    assert rewriting_event.args[2]["error"] == "Expecting value: line 1 column 1 (char 0)"


@pytest.mark.asyncio
async def test_pipeline_retry_defers_same_chapter_retry_until_current_job_finishes():
    project = SimpleNamespace(
        id=uuid.uuid4(),
        status='running',
        current_chapter=0,
        last_error=None,
        auto_mode=True,
        max_retries=5,
        writer_streaming_enabled=True,
    )
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status='queued',
        retry_count=0,
        improvement_notes='',
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    current_job_id = uuid.uuid4()
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt='effective prompt')
    prompt_result = PromptGenerationResult(prompt=prompt, source='generated', diagnostics=None)
    writer_client = SimpleNamespace(
        supports_stream_text_fallback=lambda: True,
        generate_text_fallback=AsyncMock(),
        generate=AsyncMock(),
    )
    critic_client = SimpleNamespace(generate_json=AsyncMock())
    retry_guidance = {
        'writer_feedback': '',
        'critic_review_history': '',
        'prompt_retry_info': {},
    }

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get('stage')
        if stage == 'writer_generate_fallback':
            operation.close()
            raise LLMJSONDecodeError('Expecting value: line 1 column 1 (char 0)', raw_preview='')
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=SimpleNamespace(outline_text='第2章大纲'))
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value='')
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={'continuity_notes': []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={'user_message': 'writer context', 'layers': {}, 'token_usage': 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value='critic context')

    job = SimpleNamespace(id=current_job_id, project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    deferred_retry = job.payload[DEFERRED_RETRY_JOB_KEY]
    assert deferred_retry['chapter_number'] == 2
    assert deferred_retry['payload'] == {'retry': True}


@pytest.mark.asyncio
async def test_pipeline_writer_generation_invalid_response_pauses_when_retry_limit_reached():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=1, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(
        supports_stream_text_fallback=lambda: True,
        generate_text_fallback=AsyncMock(),
        generate=AsyncMock(),
    )
    critic_client = SimpleNamespace(generate_json=AsyncMock())
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get("stage")
        if stage == "writer_generate_fallback":
            operation.close()
            raise LLMJSONDecodeError("Expecting value: line 1 column 1 (char 0)", raw_preview="")
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.retry_count == 1
    assert chapter.status == "failed"
    assert attempt.status == "errored"
    assert attempt.finished_at is not None
    assert project.status == "paused"
    assert project.last_error == "章节达到最大重试次数，项目已暂停。"
    assert not any(getattr(call.args[0], "job_type", None) == "generate_chapter" for call in session.add.call_args_list)
    assert not any(call.args[1] == "job_failed" for call in pipeline.events.append.await_args_list)
    paused_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "project_paused")
    assert paused_event.args[2]["reason"] == "max_retries_exceeded"
    assert paused_event.args[2]["phase"] == "writer_generate"


@pytest.mark.asyncio
async def test_pipeline_writer_auth_error_pauses_without_retrying():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(
        supports_stream_text_fallback=lambda: True,
        generate_text_fallback=AsyncMock(),
        generate=AsyncMock(),
    )
    critic_client = SimpleNamespace(generate_json=AsyncMock())
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    request = httpx.Request("POST", "http://45.205.31.38:7860/v1/chat/completions")
    response = httpx.Response(status_code=403, request=request)
    auth_error = httpx.HTTPStatusError("forbidden", request=request, response=response)

    async def fake_run_cancelable(operation, **kwargs):
        stage = kwargs.get("stage")
        if stage == "writer_generate_fallback":
            operation.close()
            raise auth_error
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = fake_run_cancelable
    pipeline._stream_with_pause_support = AsyncMock(return_value="")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.retry_count == 0
    assert chapter.status == "failed"
    assert chapter.last_error == "forbidden"
    assert attempt.status == "errored"
    assert attempt.finished_at is not None
    assert project.status == "paused"
    assert "writer 配置不可用" in project.last_error
    assert DEFERRED_RETRY_JOB_KEY not in job.payload
    paused_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "project_paused")
    assert paused_event.args[2]["reason"] == "writer_provider_unavailable"
    assert paused_event.args[2]["phase"] == "writer_generate"


@pytest.mark.asyncio
async def test_pipeline_persists_writer_input_snapshot_before_streaming():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 0,
        "world_revision_count": 0,
        "applied_revision_count": 0,
        "needs_review_count": 0,
    }

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(
        return_value={
            "user_message": "writer context",
            "layers": {
                "previous_chapter_tail": "第1章末尾",
                "recent_chapter_cards": "第1章\n摘要：旧线索浮现",
                "relationship_state_memory": "近期关系推进：\n第1章：沈夜 ↔ 林疏：关系推进：短暂联手",
            },
            "token_usage": 456,
        }
    )
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert attempt.input_snapshot == {
        "previous_chapter_tail": "第1章末尾",
        "recent_chapter_cards": "第1章\n摘要：旧线索浮现",
        "relationship_state_memory": "近期关系推进：\n第1章：沈夜 ↔ 林疏：关系推进：短暂联手",
    }
    assert attempt.input_tokens == 456
    assert session.commit.await_count >= 4


@pytest.mark.asyncio
async def test_pipeline_writer_stream_fallback_uses_non_stream_generate_for_empty_output():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=False)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    attempt = SimpleNamespace(content=None)
    writer_client = SimpleNamespace(
        supports_stream_text_fallback=lambda: True,
        generate=AsyncMock(
            return_value="第2章 修正版\n\n钟楼外的风贴着石壁回旋，沈夜把纸条边缘压进掌心，重新比对失踪档案的时间线。他顺着墙缝摸到那枚冰冷金属件时，终于意识到这就是撬开整起案件的关键物证。短暂沉默后，他当场决定今夜潜入档案室，把被人藏起的那页旧记录翻出来。"
        ),
    )
    pipeline.events.append = AsyncMock()

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._run_cancelable = passthrough

    content = await pipeline._finalize_writer_content(
        project=project,
        chapter=chapter,
        attempt=attempt,
        writer_client=writer_client,
        system_prompt="system",
        user_message="user",
        existing_content_prefix="",
        streamed_content="",
    )

    assert "关键物证" in content.content
    writer_client.generate.assert_awaited_once()
    event_types = [call.args[1] for call in pipeline.events.append.await_args_list]
    assert "writer_stream_fallback_started" in event_types
    assert "writer_stream_fallback_succeeded" in event_types




@pytest.mark.asyncio
async def test_pipeline_respects_project_writer_streaming_disabled_flag():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=False)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(
        generate_text_fallback=AsyncMock(
            return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。"
        )
    )
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 0,
        "world_revision_count": 0,
        "applied_revision_count": 0,
        "needs_review_count": 0,
    }

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(side_effect=AssertionError("should not use stream path"))
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    writer_client.generate_text_fallback.assert_awaited_once()
    pipeline._stream_with_pause_support.assert_not_awaited()
    event_types = [call.args[1] for call in pipeline.events.append.await_args_list]
    assert "writer_non_stream_started" in event_types
    assert "writer_non_stream_succeeded" in event_types



@pytest.mark.asyncio
async def test_pipeline_respects_project_writer_streaming_enabled_flag():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 0,
        "world_revision_count": 0,
        "applied_revision_count": 0,
        "needs_review_count": 0,
    }

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(
        return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。"
    )
    pipeline._generate_writer_content_non_stream = AsyncMock(side_effect=AssertionError("should not use non-stream path"))
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    pipeline._stream_with_pause_support.assert_awaited_once()
    pipeline._generate_writer_content_non_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_recovers_usable_stream_content_when_tail_disconnects():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 0,
        "world_revision_count": 0,
        "applied_revision_count": 0,
        "needs_review_count": 0,
    }

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = passthrough
    partial_content = "第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。"

    async def fake_stream(**_kwargs):
        attempt.content = partial_content
        raise httpx.RemoteProtocolError("peer closed connection without sending complete message body (incomplete chunked read)")

    pipeline._stream_with_pause_support = AsyncMock(side_effect=fake_stream)
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.status == "passed"
    assert chapter.retry_count == 0
    assert chapter.final_content == partial_content
    assert attempt.status == "accepted"
    event_types = [call.args[1] for call in pipeline.events.append.await_args_list]
    assert "chapter_rewriting" not in event_types
    recovered_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "writer_stream_fallback_succeeded")
    assert recovered_event.args[2]["reason"] == "stream_disconnected_after_usable_content"


@pytest.mark.asyncio
async def test_pipeline_does_not_recover_unrelated_remote_protocol_errors():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。",
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock())

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(side_effect=httpx.RemoteProtocolError("illegal chunk header"))
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value={"writer_feedback": "", "critic_review_history": "", "prompt_retry_info": {}})
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.retry_count == 1
    assert chapter.status == "queued"
    assert attempt.status == "errored"
    rewriting_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "chapter_rewriting")
    assert rewriting_event.args[2]["reason"] == "writer_generation_invalid_response"


@pytest.mark.asyncio
async def test_pipeline_non_stream_writer_timeout_uses_extended_budget():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=False)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(chapter_number=3)
    attempt = SimpleNamespace(content=None)
    writer_client = SimpleNamespace(
        generate_text_fallback=AsyncMock(
            return_value="# 第3章\n\n林雾推开控制室的门，海风沿着门缝灌进来，吹得她耳边的碎发微微发颤。她先看见总控台上跳动的指示灯，再看见被人刻意拆开的广播线路和贴着旧编号的金属铭牌。"
        )
    )

    observed: dict[str, object] = {}

    async def fake_run_cancelable(operation, **kwargs):
        observed.update(kwargs)
        return await operation

    pipeline._run_cancelable = fake_run_cancelable
    pipeline._is_usable_writer_content = Mock(return_value=True)
    pipeline.events.append = AsyncMock()

    result = await pipeline._generate_writer_content_non_stream(
        project=project,
        chapter=chapter,
        attempt=attempt,
        writer_client=writer_client,
        system_prompt="system",
        user_message="user",
        existing_content_prefix="",
    )

    assert result.content.startswith("# 第3章")
    assert observed["stage"] == "writer_generate"
    assert observed["timeout_seconds"] == 391.5
    event_payload = pipeline.events.append.await_args_list[0].args[2]
    assert event_payload["timeout_seconds"] == 391.5


def test_detect_continuation_candidate_prefers_resume_for_stream_disconnect_without_terminal_ending():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]

    result = pipeline._detect_continuation_candidate(
        content='钟声落下前，沈夜忽然意识到墙后还有',
        truncation_reason='stream_disconnected_after_usable_content',
        continuation_round=0,
    )

    assert result.should_continue is True
    assert result.reason == 'stream_disconnected_after_usable_content'


def test_detect_continuation_candidate_stops_on_terminal_ending_even_after_stream_disconnect():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]

    result = pipeline._detect_continuation_candidate(
        content='钟声落下后，沈夜终于确定了真正的入口。',
        truncation_reason='stream_disconnected_after_usable_content',
        continuation_round=0,
    )

    assert result.should_continue is False


def test_merge_continuation_content_appends_new_segment_to_existing_prefix():
    pipeline = Pipeline(session=None)  # type: ignore[arg-type]

    merged = pipeline._merge_continuation_content(
        '第一段内容',
        '第二段内容',
    )

    assert merged == '第一段内容\n第二段内容'


@pytest.mark.asyncio
async def test_pipeline_reuses_existing_attempt_when_resume_payload_contains_saved_draft():
    project = SimpleNamespace(id=uuid.uuid4(), status='running', current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=False)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status='paused',
        retry_count=0,
        improvement_notes='',
        last_error='paused',
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text='第2章大纲')
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content='前文草稿。钟声刚落，走廊里的冷气还贴着墙壁滑行，沈夜站在生锈的门边，盯着那枚仍在闪烁的旧指示灯，意识到这段记录绝不是偶然残留。',
        status='errored',
        finished_at=object(),
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt='effective prompt')
    prompt_result = PromptGenerationResult(prompt=prompt, source='generated', diagnostics=None)
    writer_client = SimpleNamespace(
        generate_text_fallback=AsyncMock(
            return_value='他压低呼吸，把拆下来的铭牌塞进口袋，顺着潮湿的楼梯继续往下摸索。每走一步，墙后的水管都会传来一次空洞回声，像有人在更深处替他计算剩余时间。'
        )
    )
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={'overall_score': 8.2, 'passed': True, 'blocking_issues': []}))

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(side_effect=AssertionError('should reuse existing attempt'))
    pipeline._get_attempt_by_id = AsyncMock(return_value=attempt)
    pipeline._get_latest_attempt = AsyncMock(return_value=attempt)
    pipeline._get_review_for_attempt = AsyncMock(return_value=None)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = passthrough
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={'continuity_notes': []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=None)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value={'writer_feedback': '', 'critic_review_history': '', 'prompt_retry_info': {}})
    pipeline.context_service.build_writer_context = AsyncMock(return_value={'user_message': 'writer context', 'layers': {}, 'token_usage': 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value='critic context')

    job = SimpleNamespace(
        project_id=project.id,
        chapter_number=2,
        payload={
            'resume': True,
            'continue_from_attempt_id': str(attempt.id),
            'continue_from_attempt_no': 1,
            'continue_from_content_chars': len(attempt.content),
        },
    )

    await pipeline.process_generate_chapter(job)

    assert attempt.status == 'accepted'
    assert chapter.final_content == (
        '前文草稿。钟声刚落，走廊里的冷气还贴着墙壁滑行，沈夜站在生锈的门边，盯着那枚仍在闪烁的旧指示灯，意识到这段记录绝不是偶然残留。\n'
        '他压低呼吸，把拆下来的铭牌塞进口袋，顺着潮湿的楼梯继续往下摸索。每走一步，墙后的水管都会传来一次空洞回声，像有人在更深处替他计算剩余时间。'
    )
    chapter_writing_events = [call for call in pipeline.events.append.await_args_list if call.args[1] == 'chapter_writing']
    assert chapter_writing_events[0].args[2]['continuation'] is True


@pytest.mark.asyncio
async def test_pipeline_does_not_resume_attempt_that_already_has_review():
    session = SimpleNamespace(execute=AsyncMock())
    pipeline = make_pipeline(session)
    chapter_id = uuid.uuid4()
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=2,
        content='已评审正文',
        status='reviewed',
    )
    pipeline._get_attempt_by_id = AsyncMock(return_value=attempt)
    pipeline._get_review_for_attempt = AsyncMock(return_value=SimpleNamespace(attempt_id=attempt.id))

    resolved = await pipeline._resolve_resume_attempt(
        chapter_id,
        {'resume': True, 'continue_from_attempt_id': str(attempt.id)},
    )

    assert resolved is None
    pipeline._get_attempt_by_id.assert_awaited_once_with(chapter_id, str(attempt.id))
    pipeline._get_review_for_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_save_review_updates_existing_review_instead_of_inserting_duplicate():
    session = SimpleNamespace(flush=AsyncMock(), add=Mock())
    pipeline = make_pipeline(session)
    attempt_id = uuid.uuid4()
    existing_review = SimpleNamespace(
        overall_score=7.1,
        passed=False,
        outline_score=7.0,
        instruction_score=7.0,
        continuity_score=7.0,
        character_score=7.0,
        writing_score=7.0,
        blocking_issues=['old'],
        uncovered_outline_points=[],
        violated_instructions=[],
        improvement_suggestions=[],
        non_scoring_notes=[],
        raw_json={'old': True},
    )
    normalized_payload = {
        'overall_score': 8.6,
        'passed': True,
        'dimensions': {
            'outline_adherence': {'score': 8.8, 'comment': ''},
            'instruction_adherence': {'score': 8.5, 'comment': ''},
            'continuity_consistency': {'score': 8.4, 'comment': ''},
            'character_consistency': {'score': 8.7, 'comment': ''},
            'writing_quality': {'score': 8.6, 'comment': ''},
        },
        'blocking_issues': [],
        'uncovered_outline_points': [],
        'violated_instructions': [],
        'improvement_suggestions': ['保留当前版本'],
        'non_scoring_notes': ['note'],
    }
    payload = {'overall_score': 8.6, 'passed': True}
    pipeline._get_review_for_attempt = AsyncMock(return_value=existing_review)

    saved_review = await pipeline._save_review(attempt_id, payload, normalized_payload)

    assert saved_review is existing_review
    assert existing_review.overall_score == 8.6
    assert existing_review.passed is True
    assert existing_review.outline_score == 8.8
    assert existing_review.improvement_suggestions == ['保留当前版本']
    assert existing_review.raw_json == payload
    session.add.assert_not_called()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_keeps_running_when_memory_needs_review_and_auto_mode_enabled():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    next_chapter = SimpleNamespace(chapter_number=3, status="pending", last_error="old")
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 1,
        "world_revision_count": 1,
        "applied_revision_count": 2,
        "needs_review_count": 0,
    }

    async def passthrough(operation, **kwargs):
        return await operation

    async def sync_warning(_: uuid.UUID) -> None:
        project.last_error = None

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=next_chapter)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock(side_effect=sync_warning)
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert project.status == "running"
    assert project.last_error is None
    assert next_chapter.status == "queued"
    queued_jobs = [call.args[0] for call in session.add.call_args_list if getattr(call.args[0], "job_type", None) == "generate_chapter"]
    assert len(queued_jobs) == 1
    assert queued_jobs[0].chapter_number == 3
    assert queued_jobs[0].status == "queued"
    assert queued_jobs[0].payload == {}
    pipeline.memory_service.sync_project_review_warning.assert_awaited_once_with(project.id)
    memory_updated = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "memory_updated")
    assert memory_updated.args[2]["applied_revision_count"] == 2
    assert memory_updated.args[2]["needs_review_count"] == 0
    assert all(call.args[1] != "project_paused" for call in pipeline.events.append.await_args_list)


@pytest.mark.asyncio
async def test_pipeline_chapter_retry_completion_does_not_enqueue_later_chapter():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=False)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
        auto_accepted=False,
    )
    next_chapter = SimpleNamespace(chapter_number=3, status="pending", last_error=None)
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=2,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(generate_text_fallback=AsyncMock(return_value='第2章 重试完成\n\n钟楼外的风贴着石壁回旋，沈夜把纸条边缘压进掌心，重新比对失踪档案的时间线。他顺着墙缝摸到那枚冰冷金属件时，终于意识到这就是撬开整起案件的关键物证。短暂沉默后，他当场决定今夜潜入档案室，把被人藏起的那页旧记录翻出来。'))
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.4, "passed": True, "blocking_issues": []}))

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=next_chapter)
    pipeline._run_cancelable = passthrough
    pipeline._ensure_project_running = AsyncMock()
    pipeline._enqueue_generate_job = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=None)
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value={"writer_feedback": "", "critic_review_history": "", "prompt_retry_info": {}})
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={"retry": True, "chapter_retry": True})

    await pipeline.process_generate_chapter(job)

    assert chapter.final_content == '第2章 重试完成\n\n钟楼外的风贴着石壁回旋，沈夜把纸条边缘压进掌心，重新比对失踪档案的时间线。他顺着墙缝摸到那枚冰冷金属件时，终于意识到这就是撬开整起案件的关键物证。短暂沉默后，他当场决定今夜潜入档案室，把被人藏起的那页旧记录翻出来。'
    assert project.status == 'paused'
    assert project.last_error == '当前章节重试已完成，后续章节保持原样。'
    pipeline._get_next_runnable_chapter.assert_awaited_once()
    pipeline._enqueue_generate_job.assert_not_awaited()
    paused_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == 'project_paused')
    assert paused_event.args[2]['reason'] == 'chapter_retry_completed'


@pytest.mark.asyncio
async def test_pipeline_only_pauses_for_waiting_manual_resume_when_memory_needs_review_and_auto_mode_disabled():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=False, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    next_chapter = SimpleNamespace(chapter_number=3, status="pending", last_error="old")
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 1,
        "world_revision_count": 1,
        "applied_revision_count": 2,
        "needs_review_count": 0,
    }

    async def passthrough(operation, **kwargs):
        return await operation

    async def sync_warning(_: uuid.UUID) -> None:
        project.last_error = None

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=next_chapter)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock(side_effect=sync_warning)
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert project.status == "paused"
    assert project.last_error is None
    assert next_chapter.status == "pending"
    assert not any(getattr(call.args[0], "job_type", None) == "generate_chapter" for call in session.add.call_args_list)
    pipeline.memory_service.sync_project_review_warning.assert_awaited_once_with(project.id)
    project_paused = [call for call in pipeline.events.append.await_args_list if call.args[1] == "project_paused"]
    assert len(project_paused) == 1
    assert project_paused[0].args == (
        project.id,
        "project_paused",
        {"reason": "waiting_manual_resume", "next_chapter": 3},
        2,
    )


@pytest.mark.asyncio
async def test_pipeline_reuses_existing_active_job_when_retrying_after_review_failure():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock(), execute=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 6.5, "passed": False, "blocking_issues": ["缺少关键落点"]}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    existing_job = SimpleNamespace(
        project_id=project.id,
        chapter_number=2,
        job_type="generate_chapter",
        status="queued",
        payload={"retry": True},
        created_at=0,
    )

    async def passthrough(operation, **kwargs):
        return await operation

    session.execute.return_value = _ScalarResult(existing_job)
    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline._ensure_job_active = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(id=uuid.uuid4(), project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    queued_jobs = [call.args[0] for call in session.add.call_args_list if getattr(call.args[0], "job_type", None) == "generate_chapter"]
    assert queued_jobs == []
    assert chapter.retry_count == 1
    assert chapter.status == "queued"
    assert attempt.status == "rejected"
    rewriting_event = next(call for call in pipeline.events.append.await_args_list if call.args[1] == "chapter_rewriting")
    assert rewriting_event.args[2]["reason"] == "review_failed"
    assert rewriting_event.args[2]["failure_summary"]["overall_score"] == 6.5
    assert rewriting_event.args[2]["failure_summary"]["top_blocking_issues"] == ["缺少关键落点"]


@pytest.mark.asyncio
async def test_pipeline_enqueue_generate_job_tolerates_conflict_when_active_job_disappears_immediately():
    project_id = uuid.uuid4()
    session = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
        execute=AsyncMock(side_effect=[_ScalarResult(None), _ExecuteResult(0), _ScalarResult(None)]),
    )
    pipeline = make_pipeline(session)

    with pytest.raises(ValueError, match="已在另一处处理完毕"):
        await pipeline._enqueue_generate_job(project_id, 60, payload={"retry": True})


@pytest.mark.asyncio
async def test_pipeline_completes_project_when_memory_needs_review_on_last_chapter():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }
    memory_result = {
        "character_revision_count": 1,
        "world_revision_count": 1,
        "applied_revision_count": 2,
        "needs_review_count": 0,
    }

    async def passthrough(operation, **kwargs):
        return await operation

    async def sync_warning(_: uuid.UUID) -> None:
        project.last_error = None

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。")
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(return_value=memory_result)
    pipeline.memory_service.sync_project_review_warning = AsyncMock(side_effect=sync_warning)
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert project.status == "completed"
    assert project.last_error is None
    pipeline.memory_service.sync_project_review_warning.assert_awaited_once_with(project.id)
    assert any(call.args[1] == "pipeline_complete" for call in pipeline.events.append.await_args_list)
    assert not any(call.args[1] == "project_paused" for call in pipeline.events.append.await_args_list)


@pytest.mark.asyncio
async def test_pipeline_continues_after_pass_when_memory_update_times_out():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=2,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    next_chapter = SimpleNamespace(chapter_number=3, status="pending", last_error=None)
    outline = SimpleNamespace(outline_text="第2章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.2, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=next_chapter)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(
        return_value="第2章 正文\n\n钟楼外的风掠过石墙，沈夜贴着昏暗回廊缓慢前行。他反复核对手中的旧纸条与现场留下的细节，确认管理员刻意回避的那条记录确实与失踪档案有关。短暂迟疑后，他压下不安，决定继续追查，把这一夜所有异常线索都连成完整链条。"
    )
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(side_effect=TimeoutError("stage memory_update:summary_generation timed out after 360s"))
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=2, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.status == "passed"
    assert project.status == "running"
    assert next_chapter.status == "queued"
    pipeline.memory_service.sync_project_review_warning.assert_not_awaited()
    event_types = [call.args[1] for call in pipeline.events.append.await_args_list]
    assert "chapter_passed" in event_types
    assert "memory_updated" not in event_types


@pytest.mark.asyncio
async def test_pipeline_continues_after_pass_when_memory_update_read_timeout_fails():
    project = SimpleNamespace(id=uuid.uuid4(), status="running", current_chapter=0, last_error=None, auto_mode=True, max_retries=5, writer_streaming_enabled=True)
    session = SimpleNamespace(get=AsyncMock(side_effect=[project, make_runtime_settings()]), commit=AsyncMock(), add=Mock(), flush=AsyncMock())
    pipeline = make_pipeline(session)
    chapter = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_number=4,
        status="queued",
        retry_count=0,
        improvement_notes="",
        last_error=None,
        final_content=None,
        final_score=None,
        accepted_attempt_id=None,
    )
    outline = SimpleNamespace(outline_text="第4章大纲")
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_no=1,
        prompt_version_id=None,
        content=None,
        status=None,
        finished_at=None,
        input_snapshot=None,
        input_tokens=None,
        output_tokens=None,
    )
    prompt = SimpleNamespace(id=uuid.uuid4(), version_no=1, effective_system_prompt="effective prompt")
    prompt_result = PromptGenerationResult(prompt=prompt, source="generated", diagnostics=None)
    writer_client = SimpleNamespace(supports_stream_text_fallback=lambda: False)
    critic_client = SimpleNamespace(generate_json=AsyncMock(return_value={"overall_score": 8.4, "passed": True, "blocking_issues": []}))
    retry_guidance = {
        "writer_feedback": "",
        "critic_review_history": "",
        "prompt_retry_info": {},
    }

    async def passthrough(operation, **kwargs):
        return await operation

    pipeline._get_chapter = AsyncMock(return_value=chapter)
    pipeline._get_outline = AsyncMock(return_value=outline)
    pipeline._create_attempt = AsyncMock(return_value=attempt)
    pipeline._get_next_runnable_chapter = AsyncMock(return_value=None)
    pipeline._run_cancelable = passthrough
    pipeline._stream_with_pause_support = AsyncMock(
        return_value="第4章 正文\n\n沈夜在钟楼内确认了新的线索。他顺着冷硬石阶继续下行，把散落的编号、门锁磨损痕迹和消失的巡逻时间一一对上，终于判断出有人在案发当夜故意改写过出入路径。意识到这条线索足以撬开整起旧案后，他决定立刻追到更深处。"
    )
    pipeline._ensure_project_running = AsyncMock()
    pipeline.events.append = AsyncMock()
    pipeline.runtime.get_model_configs = AsyncMock()
    pipeline.runtime.get_client = AsyncMock(side_effect=[writer_client, critic_client])
    pipeline.memory_service.continuity_precheck = AsyncMock(return_value={"continuity_notes": []})
    pipeline.memory_service.save_summary_and_revisions = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
    pipeline.memory_service.sync_project_review_warning = AsyncMock()
    pipeline.prompt_service.get_or_create_effective_prompt_result = AsyncMock(return_value=prompt_result)
    pipeline.context_service.build_retry_guidance = AsyncMock(return_value=retry_guidance)
    pipeline.context_service.build_writer_context = AsyncMock(return_value={"user_message": "writer context", "layers": {}, "token_usage": 123})
    pipeline.context_service.build_critic_context = AsyncMock(return_value="critic context")

    job = SimpleNamespace(project_id=project.id, chapter_number=4, payload={})

    await pipeline.process_generate_chapter(job)

    assert chapter.status == "passed"
    assert project.status == "completed"
    pipeline.memory_service.sync_project_review_warning.assert_not_awaited()
    event_types = [call.args[1] for call in pipeline.events.append.await_args_list]
    assert "chapter_passed" in event_types
    assert "pipeline_complete" in event_types
    assert "memory_updated" not in event_types
