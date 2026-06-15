import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.datetime_utils import utcnow
from backend.db.base import Base


class ChapterSummary(Base):
    __tablename__ = "chapter_summaries"
    __table_args__ = (UniqueConstraint("project_id", "chapter_number", name="uq_summary_project_chapter"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_number: Mapped[int] = mapped_column(Integer)
    summary_text: Mapped[str] = mapped_column(Text)
    key_events: Mapped[list | None] = mapped_column(JSONB, default=list)
    character_changes: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    world_changes: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    unresolved_threads: Mapped[list | None] = mapped_column(JSONB, default=list)
    emotional_tone: Mapped[str | None] = mapped_column(String(50))
    time_location: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project = relationship("Project", back_populates="summaries")


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_character_project_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str | None] = mapped_column(String(50))
    profile_json: Mapped[dict] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="characters")


class CharacterRevision(Base):
    __tablename__ = "character_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_number: Mapped[int] = mapped_column(Integer)
    character_name: Mapped[str] = mapped_column(String(100))
    change_type: Mapped[str] = mapped_column(String(30))
    patch_json: Mapped[dict] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 2))
    apply_mode: Mapped[str] = mapped_column(String(30), default="auto_safe")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WorldSetting(Base):
    __tablename__ = "world_settings"
    __table_args__ = (UniqueConstraint("project_id", "category", "name", name="uq_world_project_category_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    setting_json: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="world_settings")


class WorldSettingRevision(Base):
    __tablename__ = "world_setting_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_number: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    change_type: Mapped[str] = mapped_column(String(30))
    patch_json: Mapped[dict] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 2))
    apply_mode: Mapped[str] = mapped_column(String(30), default="auto_safe")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
