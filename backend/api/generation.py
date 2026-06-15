import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.datetime_utils import serialize_datetime
from backend.db.base import get_db_session
from backend.db.models import ChapterAttempt, ChapterReview, GenerationMode
from backend.services.generation_service import GenerationService
from backend.services.project_service import ProjectService

router = APIRouter(prefix="/api/projects", tags=["generation"])


@router.post("/{project_id}/start")
async def start_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    project_service = ProjectService(session)
    generation_service = GenerationService(session)
    project = await project_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not await project_service.ensure_required_model_roles(project_id):
        if getattr(project, "generation_mode", GenerationMode.STANDARD.value) == GenerationMode.RUSH.value:
            raise HTTPException(status_code=400, detail="请先配置 writer 模型")
        raise HTTPException(status_code=400, detail="请先配置 writer、critic、memory 三类模型")
    try:
        job = await generation_service.start_project(project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {"job_id": str(job.id), "chapter_number": job.chapter_number}


@router.post("/{project_id}/pause")
async def pause_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    project_service = ProjectService(session)
    generation_service = GenerationService(session)
    project = await project_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        await generation_service.pause_project(project, "manual_pause")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {"status": "paused"}


@router.post("/{project_id}/resume")
async def resume_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    project_service = ProjectService(session)
    generation_service = GenerationService(session)
    project = await project_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        job = await generation_service.resume_project(project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {"job_id": str(job.id), "chapter_number": job.chapter_number}


@router.post("/{project_id}/chapters/{chapter_number}/retry")
async def retry_chapter(project_id: uuid.UUID, chapter_number: int, session: AsyncSession = Depends(get_db_session)):
    project_service = ProjectService(session)
    generation_service = GenerationService(session)
    project = await project_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        job = await generation_service.retry_chapter(project, chapter_number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    await session.commit()
    return {"job_id": str(job.id), "chapter_number": chapter_number}


@router.post("/{project_id}/chapters/{chapter_number}/rewrite-from-here")
async def rewrite_from_chapter(project_id: uuid.UUID, chapter_number: int, session: AsyncSession = Depends(get_db_session)):
    project_service = ProjectService(session)
    generation_service = GenerationService(session)
    project = await project_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        job = await generation_service.rewrite_from_chapter(project, chapter_number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    await session.commit()
    return {"job_id": str(job.id), "chapter_number": chapter_number}


@router.post("/{project_id}/chapters/{chapter_number}/manual-approve")
async def manual_approve_chapter(project_id: uuid.UUID, chapter_number: int, session: AsyncSession = Depends(get_db_session)):
    project_service = ProjectService(session)
    generation_service = GenerationService(session)
    project = await project_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        chapter = await generation_service.manual_approve_chapter(project, chapter_number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    await session.commit()
    return {
        "chapter_number": chapter.chapter_number,
        "accepted_attempt_id": str(chapter.accepted_attempt_id) if chapter.accepted_attempt_id else None,
        "status": chapter.status,
    }


@router.post("/{project_id}/chapters/{chapter_number}/continue")
async def continue_chapter(project_id: uuid.UUID, chapter_number: int, session: AsyncSession = Depends(get_db_session)):
    project_service = ProjectService(session)
    generation_service = GenerationService(session)
    project = await project_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        job = await generation_service.continue_chapter(project, chapter_number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    await session.commit()
    return {"job_id": str(job.id), "chapter_number": chapter_number}

@router.get("/{project_id}/chapters")
async def list_chapters(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    generation_service = GenerationService(session)
    chapters = await generation_service.list_chapters(project_id)
    active_job_chapter_numbers = await generation_service.list_active_job_chapter_numbers(project_id)
    return [
        {
            "chapter_number": item.chapter_number,
            "status": item.status,
            "retry_count": item.retry_count,
            "final_score": float(item.final_score) if item.final_score is not None else None,
            "auto_accepted": item.auto_accepted,
            "last_error": item.last_error,
            "has_active_generation_job": item.chapter_number in active_job_chapter_numbers,
        }
        for item in chapters
    ]


@router.get("/{project_id}/chapters/{chapter_number}")
async def get_chapter(project_id: uuid.UUID, chapter_number: int, session: AsyncSession = Depends(get_db_session)):
    generation_service = GenerationService(session)
    chapter = await generation_service.get_chapter(project_id, chapter_number)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    has_active_generation_job = await generation_service.chapter_has_active_job(project_id, chapter_number)
    return {
        "chapter_number": chapter.chapter_number,
        "status": chapter.status,
        "retry_count": chapter.retry_count,
        "final_score": float(chapter.final_score) if chapter.final_score is not None else None,
        "final_content": chapter.final_content,
        "accepted_attempt_id": str(chapter.accepted_attempt_id) if chapter.accepted_attempt_id else None,
        "auto_accepted": chapter.auto_accepted,
        "improvement_notes": chapter.improvement_notes,
        "last_error": chapter.last_error,
        "has_active_generation_job": has_active_generation_job,
    }


@router.get("/{project_id}/chapters/{chapter_number}/attempts")
async def list_attempts(project_id: uuid.UUID, chapter_number: int, session: AsyncSession = Depends(get_db_session)):
    generation_service = GenerationService(session)
    chapter = await generation_service.get_chapter(project_id, chapter_number)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    stmt = (
        select(ChapterAttempt)
        .where(ChapterAttempt.chapter_id == chapter.id)
        .order_by(ChapterAttempt.attempt_no.asc())
    )
    result = await session.execute(stmt)
    attempts = list(result.scalars())
    return [
        {
            "id": str(item.id),
            "attempt_no": item.attempt_no,
            "prompt_version_id": str(item.prompt_version_id) if item.prompt_version_id else None,
            "content": item.content,
            "status": item.status,
            "started_at": serialize_datetime(item.started_at),
            "finished_at": serialize_datetime(item.finished_at),
        }
        for item in attempts
    ]


@router.get("/{project_id}/chapters/{chapter_number}/reviews")
async def list_reviews(project_id: uuid.UUID, chapter_number: int, session: AsyncSession = Depends(get_db_session)):
    generation_service = GenerationService(session)
    chapter = await generation_service.get_chapter(project_id, chapter_number)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    stmt = (
        select(ChapterReview)
        .join(ChapterAttempt, ChapterReview.attempt_id == ChapterAttempt.id)
        .where(ChapterAttempt.chapter_id == chapter.id)
        .order_by(ChapterAttempt.attempt_no.asc())
    )
    result = await session.execute(stmt)
    reviews = list(result.scalars())
    return [
        {
            "attempt_id": str(item.attempt_id),
            "overall_score": float(item.overall_score),
            "passed": item.passed,
            "outline_score": float(item.outline_score),
            "instruction_score": float(item.instruction_score),
            "continuity_score": float(item.continuity_score),
            "character_score": float(item.character_score),
            "writing_score": float(item.writing_score),
            "blocking_issues": item.blocking_issues,
            "uncovered_outline_points": item.uncovered_outline_points,
            "violated_instructions": item.violated_instructions,
            "improvement_suggestions": item.improvement_suggestions,
            "non_scoring_notes": item.non_scoring_notes,
            "created_at": serialize_datetime(item.created_at),
        }
        for item in reviews
    ]
