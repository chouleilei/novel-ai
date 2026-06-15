import uuid

from backend.services.project_events import (
    ProjectEventType,
    chapter_passed_payload,
    chapter_rewriting_payload,
    job_failed_payload,
    memory_updated_payload,
    pipeline_complete_payload,
    pipeline_start_payload,
    prompt_generated_payload,
    project_paused_payload,
)


def test_project_event_enum_values_match_existing_contract_names():
    assert ProjectEventType.PIPELINE_START.value == 'pipeline_start'
    assert ProjectEventType.CHAPTER_WRITING.value == 'chapter_writing'
    assert ProjectEventType.PRECHECK_DONE.value == 'precheck_done'
    assert ProjectEventType.PROMPT_GENERATED.value == 'prompt_generated'
    assert ProjectEventType.PROMPT_GENERATION_FALLBACK.value == 'prompt_generation_fallback'
    assert ProjectEventType.CONTENT_CHUNK.value == 'content_chunk'
    assert ProjectEventType.WRITER_NON_STREAM_STARTED.value == 'writer_non_stream_started'
    assert ProjectEventType.WRITER_NON_STREAM_SUCCEEDED.value == 'writer_non_stream_succeeded'
    assert ProjectEventType.WRITER_STREAM_NO_TEXT_FALLBACK.value == 'writer_stream_no_text_fallback'
    assert ProjectEventType.CHAPTER_REVIEWING.value == 'chapter_reviewing'
    assert ProjectEventType.CHAPTER_SCORED.value == 'chapter_scored'
    assert ProjectEventType.MEMORY_UPDATED.value == 'memory_updated'
    assert ProjectEventType.CHAPTER_PASSED.value == 'chapter_passed'
    assert ProjectEventType.CHAPTER_PAUSED.value == 'chapter_paused'
    assert ProjectEventType.CHAPTER_REWRITING.value == 'chapter_rewriting'
    assert ProjectEventType.JOB_FAILED.value == 'job_failed'
    assert ProjectEventType.PROJECT_PAUSED.value == 'project_paused'
    assert ProjectEventType.PIPELINE_COMPLETE.value == 'pipeline_complete'


def test_project_event_payload_helpers_preserve_existing_shapes():
    project_id = uuid.uuid4()
    job_id = uuid.uuid4()

    assert pipeline_start_payload(chapter_number=2) == {'chapter_number': 2}
    assert pipeline_start_payload(resume=True) == {'resume': True}
    assert prompt_generated_payload(version_no=3, source='generated') == {'version_no': 3, 'source': 'generated'}
    assert chapter_passed_payload(score=9.1, manual=True) == {'score': 9.1, 'manual': True}
    assert chapter_passed_payload(score=7.4, auto_accepted=True) == {'score': 7.4, 'auto_accepted': True}
    assert memory_updated_payload(
        chapter_number=2,
        character_revision_count=1,
        world_revision_count=2,
        applied_revision_count=3,
        needs_review_count=4,
    ) == {
        'chapter_number': 2,
        'character_revision_count': 1,
        'world_revision_count': 2,
        'applied_revision_count': 3,
        'needs_review_count': 4,
    }
    assert project_paused_payload(reason='waiting_manual_resume', next_chapter=3) == {
        'reason': 'waiting_manual_resume',
        'next_chapter': 3,
    }
    assert chapter_rewriting_payload(
        retry_count=2,
        reason='review_failed',
        failure_summary={
            'overall_score': 6.8,
            'blocking_issue_count': 1,
            'top_blocking_issues': ['关键情节缺失'],
        },
    ) == {
        'retry_count': 2,
        'reason': 'review_failed',
        'failure_summary': {
            'overall_score': 6.8,
            'blocking_issue_count': 1,
            'top_blocking_issues': ['关键情节缺失'],
        },
    }
    assert pipeline_complete_payload(project_id) == {'project_id': str(project_id)}
    assert job_failed_payload(job_id=job_id, error='boom') == {'job_id': str(job_id), 'error': 'boom'}
