import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.datetime_utils import serialize_datetime
from backend.db.base import get_db_session
from backend.db.models import GenerationMode
from backend.services.model_connectivity_service import ModelConnectivityService
from backend.services.project_service import ProjectService
from backend.model_config_utils import (
    ModelConfigItem,
    ModelConnectivityTestRequest,
    serialize_model_extra_config,
    validate_model_configs_payload,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])

MAX_OUTLINE_IMPORT_CHAPTERS = 500


class ProjectCreateRequest(BaseModel):
    title: str = Field(max_length=200)
    genre: str | None = Field(default=None, max_length=100)
    style: str | None = Field(default=None, max_length=200)
    global_prompt: str = Field(max_length=50000)
    total_chapters: int = Field(default=60, ge=1, le=500)
    auto_mode: bool = True
    auto_accept_critic_failed: bool = False
    auto_accept_on_max_retries: bool = False
    hard_review_gates_enabled: bool = True
    max_retries: int = Field(default=5, ge=1, le=20)
    writer_streaming_enabled: bool = False
    generation_mode: GenerationMode = GenerationMode.STANDARD
    rush_previous_chapter_count: int = Field(default=10, ge=0, le=50)


class ProjectUpdateRequest(ProjectCreateRequest):
    pass


class OutlineItem(BaseModel):
    chapter_number: int = Field(ge=1)
    outline_text: str = Field(max_length=50000)
    tags: dict[str, Any] | None = None


class OutlineUpdateRequest(BaseModel):
    outline_text: str = Field(max_length=50000)
    tags: dict[str, Any] | None = None


class ProjectDefaultsResponse(BaseModel):
    total_chapters: int = Field(ge=1)
    auto_mode: bool
    auto_accept_critic_failed: bool
    auto_accept_on_max_retries: bool
    hard_review_gates_enabled: bool
    max_retries: int = Field(ge=1, le=20)
    writer_streaming_enabled: bool


def validate_outlines_payload(payload: list[OutlineItem]) -> None:
    if not payload:
        raise HTTPException(status_code=400, detail="至少需要导入一章大纲")
    if len(payload) > MAX_OUTLINE_IMPORT_CHAPTERS:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多导入 {MAX_OUTLINE_IMPORT_CHAPTERS} 章大纲，请检查是否误把正文或小节拆成章节",
        )

    chapter_numbers = [item.chapter_number for item in payload]
    duplicates = sorted({number for number in chapter_numbers if chapter_numbers.count(number) > 1})
    if duplicates:
        duplicate_text = ", ".join(str(item) for item in duplicates)
        raise HTTPException(status_code=400, detail=f"章节号不能重复: {duplicate_text}")

    expected = list(range(1, len(chapter_numbers) + 1))
    actual = sorted(chapter_numbers)
    if actual != expected:
        raise HTTPException(status_code=400, detail="章节号必须从 1 开始连续递增")


def serialize_project_summary(project) -> dict[str, Any]:
    generation_mode = getattr(project, "generation_mode", GenerationMode.STANDARD.value)
    return {
        "id": str(project.id),
        "title": project.title,
        "genre": project.genre,
        "style": project.style,
        "status": project.status,
        "total_chapters": project.total_chapters,
        "current_chapter": project.current_chapter,
        "auto_mode": project.auto_mode,
        "auto_accept_critic_failed": project.auto_accept_critic_failed,
        "auto_accept_on_max_retries": project.auto_accept_on_max_retries,
        "hard_review_gates_enabled": project.hard_review_gates_enabled,
        "generation_mode": getattr(generation_mode, "value", generation_mode),
        "rush_previous_chapter_count": getattr(project, "rush_previous_chapter_count", 10),
        "updated_at": serialize_datetime(project.updated_at),
    }


def serialize_project_detail(project) -> dict[str, Any]:
    return serialize_project_summary(project) | {
        "style": project.style,
        "global_prompt": project.global_prompt,
        "max_retries": project.max_retries,
        "writer_streaming_enabled": project.writer_streaming_enabled,
        "last_error": project.last_error,
        "created_at": serialize_datetime(project.created_at),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = ProjectService(session)
    project = await service.create_project(**payload.model_dump())
    await session.commit()
    return {"id": str(project.id), "status": project.status}


@router.get("")
async def list_projects(session: AsyncSession = Depends(get_db_session)):
    service = ProjectService(session)
    projects = await service.list_projects()
    return [serialize_project_summary(item) for item in projects]


@router.get("/defaults")
async def get_project_defaults(session: AsyncSession = Depends(get_db_session)):
    service = ProjectService(session)
    return ProjectDefaultsResponse(**(await service.get_default_project_seed())).model_dump()


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    service = ProjectService(session)
    project = await service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return serialize_project_detail(project)


@router.put("/{project_id}")
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = ProjectService(session)
    project = await service.update_project(project_id, **payload.model_dump())
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await session.commit()
    return serialize_project_detail(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    service = ProjectService(session)
    deleted = await service.delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="项目不存在")
    await session.commit()


@router.post("/{project_id}/copy", status_code=status.HTTP_201_CREATED)
async def copy_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    service = ProjectService(session)
    project = await service.copy_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await session.commit()
    return serialize_project_detail(project)


@router.put("/{project_id}/models")
async def save_model_configs(
    project_id: uuid.UUID,
    payload: list[ModelConfigItem],
    session: AsyncSession = Depends(get_db_session),
):
    service = ProjectService(session)
    project = await service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    validate_model_configs_payload(payload)
    try:
        configs = await service.save_model_configs(
            project_id,
            [item.model_dump(mode="python", exclude_unset=True) for item in payload],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {"count": len(configs)}


@router.get("/defaults/model-configs")
async def get_default_model_configs(session: AsyncSession = Depends(get_db_session)):
    service = ProjectService(session)
    return [
        {
            **item,
            "extra_config": serialize_model_extra_config(item.get("role"), item.get("extra_config"), include_has_api_key=True, has_api_key_override=bool((item.get("extra_config") or {}).get("api_key_encrypted"))),
        }
        for item in await service.get_default_model_configs()
    ]


@router.get("/{project_id}/models")
async def get_model_configs(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    service = ProjectService(session)
    project = await service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    configs = await service.get_model_configs(project_id)
    return [
        {
            "id": str(item.id) if item.id else None,
            "role": item.role,
            "channel_id": str(getattr(item, "channel_id", None)) if getattr(item, "channel_id", None) else None,
            "provider": item.provider,
            "base_url": item.base_url,
            "model_name": item.model_name,
            "temperature": float(item.temperature) if item.temperature is not None else None,
            "max_tokens": item.max_tokens,
            "extra_config": serialize_model_extra_config(item.role, item.extra_config, include_has_api_key=True),
        }
        for item in configs
    ]


@router.post("/{project_id}/models/test")
async def test_model_connectivity(
    project_id: uuid.UUID,
    payload: ModelConnectivityTestRequest,
    session: AsyncSession = Depends(get_db_session),
):
    project_service = ProjectService(session)
    project = await project_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    service = ModelConnectivityService(session)
    return await service.test_model(project_id, payload.model_dump(mode="python", exclude_unset=True))


@router.post("/{project_id}/outlines/import")
async def import_outlines(
    project_id: uuid.UUID,
    payload: list[OutlineItem],
    session: AsyncSession = Depends(get_db_session),
):
    service = ProjectService(session)
    project = await service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    validate_outlines_payload(payload)
    await service.import_outlines(project_id, [item.model_dump() for item in payload])
    await session.commit()
    return {"count": len(payload)}


@router.get("/{project_id}/outlines")
async def list_outlines(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    service = ProjectService(session)
    outlines = await service.list_outlines(project_id)
    return [
        {
            "chapter_number": item.chapter_number,
            "outline_text": item.outline_text,
            "tags": item.tags or {},
        }
        for item in outlines
    ]


@router.put("/{project_id}/outlines/{chapter_number}")
async def update_outline(
    project_id: uuid.UUID,
    chapter_number: int,
    payload: OutlineUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = ProjectService(session)
    outline = await service.update_outline(project_id, chapter_number, payload.outline_text, payload.tags)
    if outline is None:
        raise HTTPException(status_code=404, detail="章节大纲不存在")
    await session.commit()
    return {"chapter_number": outline.chapter_number}
