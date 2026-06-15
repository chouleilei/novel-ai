import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.datetime_utils import utcnow
from backend.db.base import Base


class ChapterStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    WRITING = "writing"
    REVIEWING = "reviewing"
    PASSED = "passed"
    FAILED = "failed"
    PAUSED = "paused"


class AttemptStatus(str, enum.Enum):
    RUNNING = "running"
    REVIEWED = "reviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERRORED = "errored"


class ChapterOutline(Base):
    __tablename__ = "chapter_outlines"
    __table_args__ = (UniqueConstraint("project_id", "chapter_number", name="uq_outline_project_chapter"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_number: Mapped[int] = mapped_column(Integer)
    outline_text: Mapped[str] = mapped_column(Text)
    tags: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="outlines")


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("project_id", "chapter_number", name="uq_chapter_project_chapter"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[ChapterStatus] = mapped_column(String(30), default=ChapterStatus.PENDING.value)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    final_content: Mapped[str | None] = mapped_column(Text)
    final_score: Mapped[float | None] = mapped_column(Numeric(4, 2))
    accepted_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    auto_accepted: Mapped[bool] = mapped_column(default=False)
    improvement_notes: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="chapters")
    attempts = relationship("ChapterAttempt", back_populates="chapter", cascade="all, delete-orphan")


class ChapterAttempt(Base):
    __tablename__ = "chapter_attempts"
    __table_args__ = (UniqueConstraint("chapter_id", "attempt_no", name="uq_attempt_chapter_no"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    input_snapshot: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AttemptStatus] = mapped_column(String(30), default=AttemptStatus.RUNNING.value)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    chapter = relationship("Chapter", back_populates="attempts")
    review = relationship("ChapterReview", back_populates="attempt", uselist=False, cascade="all, delete-orphan")


class ChapterReview(Base):
    __tablename__ = "chapter_reviews"
    __table_args__ = (UniqueConstraint("attempt_id", name="uq_review_attempt"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapter_attempts.id", ondelete="CASCADE"))
    overall_score: Mapped[float] = mapped_column(Numeric(4, 2))
    passed: Mapped[bool] = mapped_column(default=False)
    outline_score: Mapped[float] = mapped_column(Numeric(4, 2))
    instruction_score: Mapped[float] = mapped_column(Numeric(4, 2))
    continuity_score: Mapped[float] = mapped_column(Numeric(4, 2))
    character_score: Mapped[float] = mapped_column(Numeric(4, 2))
    writing_score: Mapped[float] = mapped_column(Numeric(4, 2))
    blocking_issues: Mapped[list] = mapped_column(JSONB, default=list)
    uncovered_outline_points: Mapped[list] = mapped_column(JSONB, default=list)
    violated_instructions: Mapped[list] = mapped_column(JSONB, default=list)
    improvement_suggestions: Mapped[list] = mapped_column(JSONB, default=list)
    non_scoring_notes: Mapped[list] = mapped_column(JSONB, default=list)
    raw_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    attempt = relationship("ChapterAttempt", back_populates="review")
