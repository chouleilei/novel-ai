from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import cast

from backend.db.models import ProjectEvent
from backend.services.project_events import ProjectEventType


class EventService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        project_id,
        event_type: str | ProjectEventType,
        event_data: dict[str, object],
        chapter_number: int | None = None,
    ) -> ProjectEvent:
        normalized_event_type = event_type if isinstance(event_type, str) else cast(str, event_type.value)
        event = ProjectEvent(
            project_id=project_id,
            chapter_number=chapter_number,
            event_type=normalized_event_type,
            event_data=event_data,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_after(self, project_id, after_id: int = 0, limit: int = 100) -> list[ProjectEvent]:
        stmt = select(ProjectEvent).where(ProjectEvent.project_id == project_id)
        if after_id > 0:
            stmt = (
                stmt.where(ProjectEvent.id > after_id)
                .order_by(ProjectEvent.id.asc())
                .limit(limit)
            )
        else:
            stmt = (
                stmt.order_by(ProjectEvent.id.desc())
                .limit(limit)
            )
        result = await self.session.execute(stmt)
        items = list(result.scalars())
        if after_id > 0:
            return items
        return list(reversed(items))
