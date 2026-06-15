import enum
import uuid
from typing import Any, TypedDict


class ProjectEventType(str, enum.Enum):
    PIPELINE_START = 'pipeline_start'
    CHAPTER_WRITING = 'chapter_writing'
    CHAPTER_TRUNCATED = 'chapter_truncated'
    PRECHECK_DONE = 'precheck_done'
    PROMPT_GENERATED = 'prompt_generated'
    PROMPT_GENERATION_FALLBACK = 'prompt_generation_fallback'
    PROMPT_UPDATED = 'prompt_updated'
    CONTENT_CHUNK = 'content_chunk'
    WRITER_NON_STREAM_STARTED = 'writer_non_stream_started'
    WRITER_NON_STREAM_SUCCEEDED = 'writer_non_stream_succeeded'
    WRITER_STREAM_NO_TEXT_FALLBACK = 'writer_stream_no_text_fallback'
    WRITER_STREAM_FALLBACK_STARTED = 'writer_stream_fallback_started'
    WRITER_STREAM_FALLBACK_SUCCEEDED = 'writer_stream_fallback_succeeded'
    CHAPTER_REVIEWING = 'chapter_reviewing'
    CHAPTER_SCORED = 'chapter_scored'
    MEMORY_UPDATED = 'memory_updated'
    CHAPTER_PASSED = 'chapter_passed'
    CHAPTER_PAUSED = 'chapter_paused'
    CHAPTER_REWRITING = 'chapter_rewriting'
    JOB_FAILED = 'job_failed'
    PROJECT_PAUSED = 'project_paused'
    PIPELINE_COMPLETE = 'pipeline_complete'


class PromptGeneratedPayload(TypedDict, total=False):
    version_no: int
    source: str
    fallback: dict[str, Any] | None


class MemoryUpdatedPayload(TypedDict, total=False):
    chapter_number: int
    character_revision_count: int
    world_revision_count: int
    applied_revision_count: int
    needs_review_count: int
    confidence_threshold: float


class ProjectPausedPayload(TypedDict, total=False):
    reason: str
    phase: str
    cancelled_jobs: int
    paused_chapters: int
    next_chapter: int


class ChapterRewriteFailureSummary(TypedDict, total=False):
    overall_score: float
    blocking_issue_count: int
    top_blocking_issues: list[str]
    violated_instruction_count: int
    top_violated_instructions: list[str]
    improvement_suggestion_count: int
    top_improvement_suggestions: list[str]


class ChapterTruncatedPayload(TypedDict, total=False):
    attempt: int
    accumulated_chars: int
    reason: str


class ChapterRewritingPayload(TypedDict, total=False):
    retry_count: int
    queued: bool
    reason: str
    error: str
    failure_summary: ChapterRewriteFailureSummary


class JobFailedPayload(TypedDict):
    job_id: str
    error: str


def pipeline_start_payload(*, chapter_number: int | None = None, resume: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if chapter_number is not None:
        payload['chapter_number'] = chapter_number
    if resume:
        payload['resume'] = True
    return payload


def prompt_generated_payload(*, version_no: int, source: str, fallback: dict[str, Any] | None = None) -> PromptGeneratedPayload:
    payload: PromptGeneratedPayload = {
        'version_no': version_no,
        'source': source,
    }
    if fallback is not None:
        payload['fallback'] = fallback
    return payload


def memory_updated_payload(
    *,
    chapter_number: int,
    character_revision_count: int,
    world_revision_count: int,
    applied_revision_count: int,
    needs_review_count: int,
    confidence_threshold: float | None = None,
) -> MemoryUpdatedPayload:
    payload: MemoryUpdatedPayload = {
        'chapter_number': chapter_number,
        'character_revision_count': character_revision_count,
        'world_revision_count': world_revision_count,
        'applied_revision_count': applied_revision_count,
        'needs_review_count': needs_review_count,
    }
    if confidence_threshold is not None:
        payload['confidence_threshold'] = confidence_threshold
    return payload


def chapter_passed_payload(*, score: float | None, manual: bool = False, auto_accepted: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {'score': score}
    if manual:
        payload['manual'] = True
    if auto_accepted:
        payload['auto_accepted'] = True
    return payload


def chapter_truncated_payload(
    *,
    attempt: int,
    accumulated_chars: int,
    reason: str,
) -> ChapterTruncatedPayload:
    return {
        'attempt': attempt,
        'accumulated_chars': accumulated_chars,
        'reason': reason,
    }


def chapter_rewriting_payload(
    *,
    retry_count: int | None = None,
    queued: bool = False,
    reason: str | None = None,
    error: str | None = None,
    failure_summary: ChapterRewriteFailureSummary | None = None,
) -> ChapterRewritingPayload:
    payload: ChapterRewritingPayload = {}
    if retry_count is not None:
        payload['retry_count'] = retry_count
    if queued:
        payload['queued'] = True
    if reason is not None:
        payload['reason'] = reason
    if error is not None:
        payload['error'] = error
    if failure_summary is not None:
        payload['failure_summary'] = failure_summary
    return payload


def project_paused_payload(
    *,
    reason: str,
    phase: str | None = None,
    cancelled_jobs: int | None = None,
    paused_chapters: int | None = None,
    next_chapter: int | None = None,
) -> ProjectPausedPayload:
    payload: ProjectPausedPayload = {'reason': reason}
    if phase is not None:
        payload['phase'] = phase
    if cancelled_jobs is not None:
        payload['cancelled_jobs'] = cancelled_jobs
    if paused_chapters is not None:
        payload['paused_chapters'] = paused_chapters
    if next_chapter is not None:
        payload['next_chapter'] = next_chapter
    return payload


def pipeline_complete_payload(project_id: uuid.UUID) -> dict[str, str]:
    return {'project_id': str(project_id)}


def job_failed_payload(*, job_id: uuid.UUID, error: str) -> JobFailedPayload:
    return {'job_id': str(job_id), 'error': error}
