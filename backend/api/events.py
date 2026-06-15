import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.datetime_utils import serialize_datetime
from backend.db.base import AsyncSessionLocal, get_db_session
from backend.services.event_service import EventService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["events"])

SSE_HEARTBEAT_INTERVAL = 15
SSE_POLL_INTERVAL = 2


@router.get("/{project_id}/events")
async def list_events(
    project_id: uuid.UUID,
    after_id: int = 0,
    session: AsyncSession = Depends(get_db_session),
):
    events = await EventService(session).list_after(project_id, after_id=after_id)
    return [
        {
            "id": item.id,
            "event_type": item.event_type,
            "chapter_number": item.chapter_number,
            "event_data": item.event_data,
            "created_at": serialize_datetime(item.created_at),
        }
        for item in events
    ]


@router.get("/{project_id}/events/stream")
async def stream_events(
    project_id: uuid.UUID,
    request: Request,
    after_id: int = 0,
):
    async def event_generator():
        cursor = after_id
        last_heartbeat = asyncio.get_event_loop().time()
        while True:
            if await request.is_disconnected():
                break
            try:
                async with AsyncSessionLocal() as session:
                    events = await EventService(session).list_after(project_id, after_id=cursor)
                    if events:
                        for item in events:
                            cursor = item.id
                            payload = {
                                "id": item.id,
                                "type": item.event_type,
                                "chapter_number": item.chapter_number,
                                "data": item.event_data,
                                "created_at": serialize_datetime(item.created_at),
                            }
                            yield f"id: {item.id}\nevent: {item.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        last_heartbeat = asyncio.get_event_loop().time()
                    else:
                        now = asyncio.get_event_loop().time()
                        if now - last_heartbeat >= SSE_HEARTBEAT_INTERVAL:
                            yield ": heartbeat\n\n"
                            last_heartbeat = now
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("SSE stream query failed for project %s", str(project_id))
                try:
                    yield ": heartbeat\n\n"
                except Exception:
                    break
                last_heartbeat = asyncio.get_event_loop().time()
            await asyncio.sleep(SSE_POLL_INTERVAL)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
