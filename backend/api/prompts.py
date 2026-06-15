import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.datetime_utils import serialize_datetime
from backend.db.base import get_db_session
from backend.db.models import ChapterPrompt
from backend.services.event_service import EventService
from backend.services.project_events import ProjectEventType, prompt_generated_payload
from backend.services.prompt_service import PromptBuilderGenerationError, PromptService


def _prompt_builder_http_status(failure_type: str) -> int:
    if failure_type in {"missing_configuration", "missing_api_key", "unsupported_provider"}:
        return 400
    return 502

router = APIRouter(prefix="/api/projects", tags=["prompts"])


class PromptUpdateRequest(BaseModel):
    edited_prompt: str


class GlobalPromptGenerateRequest(BaseModel):
    outlines_text: str | None = None
    title: str | None = None
    genre: str | None = None
    style: str | None = None


class OutlineGenerateRequest(BaseModel):
    global_prompt: str | None = None
    title: str | None = None
    genre: str | None = None
    style: str | None = None
    total_chapters: int | None = Field(default=None, ge=1)


@router.post("/{project_id}/global-prompt/generate")
async def generate_global_prompt(
    project_id: uuid.UUID,
    payload: GlobalPromptGenerateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = PromptService(session)
    try:
        result = await service.generate_global_prompt(
            project_id=project_id,
            outlines_text=payload.outlines_text,
            title=payload.title,
            genre=payload.genre,
            style=payload.style,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PromptBuilderGenerationError as exc:
        raise HTTPException(
            status_code=_prompt_builder_http_status(exc.diagnostics.get("failure_type", "request_failed")),
            detail={
                "message": str(exc),
                "builder_diagnostics": exc.diagnostics,
            },
        ) from exc
    return {
        "title": result["title"],
        "genre": result["genre"],
        "style": result["style"],
        "global_prompt": result["global_prompt"],
        "builder_payload": result["builder_payload"],
    }


@router.post("/{project_id}/outlines/generate")
async def generate_outlines(
    project_id: uuid.UUID,
    payload: OutlineGenerateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = PromptService(session)
    try:
        result = await service.generate_outlines_from_global_prompt(
            project_id=project_id,
            global_prompt=payload.global_prompt,
            title=payload.title,
            genre=payload.genre,
            style=payload.style,
            total_chapters=payload.total_chapters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PromptBuilderGenerationError as exc:
        raise HTTPException(
            status_code=_prompt_builder_http_status(exc.diagnostics.get("failure_type", "request_failed")),
            detail={
                "message": str(exc),
                "builder_diagnostics": exc.diagnostics,
            },
        ) from exc
    return {
        "outlines": result["outlines"],
        "builder_payload": result["builder_payload"],
    }


@router.post("/{project_id}/chapters/{chapter_number}/prompt/generate")
async def generate_prompt(project_id: uuid.UUID, chapter_number: int, session: AsyncSession = Depends(get_db_session)):
    service = PromptService(session)
    try:
        result = await service.get_or_create_effective_prompt_result(
            project_id=project_id,
            chapter_number=chapter_number,
            precheck={},
            retry_info={},
            force_regenerate=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PromptBuilderGenerationError as exc:
        raise HTTPException(
            status_code=_prompt_builder_http_status(exc.diagnostics.get("failure_type", "request_failed")),
            detail={
                "message": str(exc),
                "builder_diagnostics": exc.diagnostics,
            },
        ) from exc
    prompt = result.prompt
    if result.diagnostics is not None:
        await EventService(session).append(
            project_id,
            ProjectEventType.PROMPT_GENERATION_FALLBACK,
            result.diagnostics,
            chapter_number=chapter_number,
        )
    await EventService(session).append(
        project_id,
        ProjectEventType.PROMPT_GENERATED,
        prompt_generated_payload(
            version_no=prompt.version_no,
            source=result.source,
            fallback=result.diagnostics,
        ),
        chapter_number=chapter_number,
    )
    await session.commit()
    return {"version_no": prompt.version_no}


@router.get("/{project_id}/chapters/{chapter_number}/prompts")
async def list_prompts(project_id: uuid.UUID, chapter_number: int, session: AsyncSession = Depends(get_db_session)):
    stmt = (
        select(ChapterPrompt)
        .where(
            ChapterPrompt.project_id == project_id,
            ChapterPrompt.chapter_number == chapter_number,
        )
        .order_by(ChapterPrompt.version_no.desc())
    )
    result = await session.execute(stmt)
    prompts = list(result.scalars())
    return [
        {
            "id": str(item.id),
            "project_id": str(item.project_id),
            "chapter_number": item.chapter_number,
            "version_no": item.version_no,
            "status": item.status,
            "generated_system_prompt": item.generated_system_prompt,
            "user_edited_prompt": item.user_edited_prompt,
            "effective_system_prompt": item.effective_system_prompt,
            "created_at": serialize_datetime(item.created_at),
        }
        for item in prompts
    ]


@router.put("/{project_id}/chapters/{chapter_number}/prompts/{version_no}")
async def update_prompt(
    project_id: uuid.UUID,
    chapter_number: int,
    version_no: int,
    payload: PromptUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = PromptService(session)
    prompt = await service.update_prompt(project_id, chapter_number, version_no, payload.edited_prompt)
    if prompt is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    await EventService(session).append(
        project_id,
        ProjectEventType.PROMPT_UPDATED,
        {"version_no": version_no},
        chapter_number=chapter_number,
    )
    await session.commit()
    return {"version_no": prompt.version_no, "status": prompt.status}


@router.post("/{project_id}/chapters/{chapter_number}/prompt/approve")
async def approve_prompt(
    project_id: uuid.UUID,
    chapter_number: int,
    version_no: int,
    session: AsyncSession = Depends(get_db_session),
):
    service = PromptService(session)
    prompt = await service.approve_prompt(project_id, chapter_number, version_no)
    if prompt is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    await EventService(session).append(
        project_id,
        ProjectEventType.PROMPT_UPDATED,
        {"version_no": version_no, "status": prompt.status},
        chapter_number=chapter_number,
    )
    await session.commit()
    return {"version_no": prompt.version_no, "status": prompt.status}
