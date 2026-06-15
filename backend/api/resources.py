import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.datetime_utils import serialize_datetime
from backend.db.base import get_db_session
from backend.services.export_service import ExportService
from backend.services.memory_service import MemoryService
from backend.services.project_service import ProjectService

router = APIRouter(prefix="/api/projects", tags=["resources"])


def build_content_disposition(filename_base: str, extension: str) -> str:
    utf8_filename = f"{filename_base}.{extension}"
    ascii_base = "".join(
        ch if ch.isascii() and ch not in '\\"/<>|:?*' and ch not in "\r\n" else "_"
        for ch in filename_base
    ).strip(" ._")
    ascii_filename = f"{ascii_base or 'novel'}.{extension}"
    encoded_filename = quote(utf8_filename, safe="")
    return f"""attachment; filename="{ascii_filename}"; filename*=UTF-8''{encoded_filename}"""


def serialize_character_revision(item) -> dict:
    return {
        "id": str(item.id),
        "chapter_number": item.chapter_number,
        "character_name": item.character_name,
        "change_type": item.change_type,
        "patch_json": item.patch_json,
        "confidence": float(item.confidence) if item.confidence is not None else None,
        "apply_mode": item.apply_mode,
        "created_at": serialize_datetime(item.created_at),
    }


def serialize_world_revision(item) -> dict:
    return {
        "id": str(item.id),
        "chapter_number": item.chapter_number,
        "category": item.category,
        "name": item.name,
        "change_type": item.change_type,
        "patch_json": item.patch_json,
        "confidence": float(item.confidence) if item.confidence is not None else None,
        "apply_mode": item.apply_mode,
        "created_at": serialize_datetime(item.created_at),
    }


@router.get("/{project_id}/summaries")
async def list_summaries(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    items = await MemoryService(session).list_summaries(project_id)
    return [
        {
            "chapter_number": item.chapter_number,
            "summary_text": item.summary_text,
            "key_events": item.key_events,
            "unresolved_threads": item.unresolved_threads,
            "emotional_tone": item.emotional_tone,
            "time_location": item.time_location,
        }
        for item in items
    ]


@router.get("/{project_id}/characters")
async def list_characters(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    memory = MemoryService(session)
    items = await memory.list_characters(project_id)
    if not items:
        return await memory.build_character_resources_from_summaries(project_id)
    return [
        {
            "name": item.name,
            "role": item.role,
            "profile_json": item.profile_json,
            "is_active": item.is_active,
        }
        for item in items
    ]


@router.get("/{project_id}/world-settings")
async def list_world_settings(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    memory = MemoryService(session)
    items = await memory.list_world_settings(project_id)
    if not items:
        return await memory.build_world_setting_resources_from_summaries(project_id)
    return [
        {
            "category": item.category,
            "name": item.name,
            "setting_json": item.setting_json,
        }
        for item in items
    ]


@router.get("/{project_id}/character-revisions")
async def list_character_revisions(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    items = await MemoryService(session).list_character_revisions(project_id)
    return [serialize_character_revision(item) for item in items]


@router.post("/{project_id}/character-revisions/{revision_id}/apply")
async def apply_character_revision(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    revision = await MemoryService(session).apply_character_revision(project_id, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="人物变更提案不存在")
    await session.commit()
    return serialize_character_revision(revision)


@router.post("/{project_id}/character-revisions/{revision_id}/reject")
async def reject_character_revision(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    revision = await MemoryService(session).reject_character_revision(project_id, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="人物变更提案不存在")
    await session.commit()
    return serialize_character_revision(revision)


@router.get("/{project_id}/world-setting-revisions")
async def list_world_setting_revisions(project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    items = await MemoryService(session).list_world_setting_revisions(project_id)
    return [serialize_world_revision(item) for item in items]


@router.post("/{project_id}/world-setting-revisions/{revision_id}/apply")
async def apply_world_setting_revision(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    revision = await MemoryService(session).apply_world_setting_revision(project_id, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="世界观变更提案不存在")
    await session.commit()
    return serialize_world_revision(revision)


@router.post("/{project_id}/world-setting-revisions/{revision_id}/reject")
async def reject_world_setting_revision(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    revision = await MemoryService(session).reject_world_setting_revision(project_id, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="世界观变更提案不存在")
    await session.commit()
    return serialize_world_revision(revision)


@router.get("/{project_id}/export")
async def export_novel(
    project_id: uuid.UUID,
    format: str = "markdown",
    session: AsyncSession = Depends(get_db_session),
):
    project = await ProjectService(session).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    export_service = ExportService(session)
    format_key = format.lower()
    filename_base = "".join(ch for ch in (project.title or "novel") if ch not in '\\"/<>|:?*') or "novel"
    if format_key == "markdown":
        content = await export_service.export_markdown(project_id)
        return PlainTextResponse(
            content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": build_content_disposition(filename_base, "md")},
        )
    if format_key == "txt":
        content = await export_service.export_txt(project_id)
        return PlainTextResponse(
            content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": build_content_disposition(filename_base, "txt")},
        )
    if format_key == "epub":
        content = await export_service.export_epub(project_id)
        return Response(
            content=content,
            media_type="application/epub+zip",
            headers={"Content-Disposition": build_content_disposition(filename_base, "epub")},
        )
    raise HTTPException(status_code=400, detail="仅支持 markdown、txt、epub 三种导出格式")
