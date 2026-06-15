import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.datetime_utils import utcnow
from backend.db.base import Base


class PromptStatus(str, enum.Enum):
    GENERATED = "generated"
    EDITED = "edited"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class ChapterPrompt(Base):
    __tablename__ = "chapter_prompts"
    __table_args__ = (UniqueConstraint("project_id", "chapter_number", "version_no", name="uq_prompt_project_chapter_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_number: Mapped[int] = mapped_column(Integer)
    version_no: Mapped[int] = mapped_column(Integer)
    generated_system_prompt: Mapped[str] = mapped_column(Text)
    user_edited_prompt: Mapped[str | None] = mapped_column(Text)
    effective_system_prompt: Mapped[str] = mapped_column(Text)
    source_payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[PromptStatus] = mapped_column(String(30), default=PromptStatus.GENERATED.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project = relationship("Project", back_populates="prompts")

