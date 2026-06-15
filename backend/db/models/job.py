import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.datetime_utils import utcnow
from backend.db.base import Base


class JobType(str, enum.Enum):
    GENERATE_CHAPTER = "generate_chapter"
    REBUILD_PROMPT = "rebuild_prompt"
    EXPORT_NOVEL = "export_novel"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        Index(
            "uq_generation_jobs_active_project_chapter_type",
            "project_id",
            "chapter_number",
            "job_type",
            unique=True,
            postgresql_where=text("chapter_number IS NOT NULL AND status IN ('queued', 'leased')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_number: Mapped[int | None] = mapped_column(Integer)
    job_type: Mapped[JobType] = mapped_column(String(30))
    status: Mapped[JobStatus] = mapped_column(String(30), default=JobStatus.QUEUED.value)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, default=dict)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="jobs")
