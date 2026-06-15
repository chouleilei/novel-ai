import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.datetime_utils import utcnow
from backend.db.base import Base


class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class GenerationMode(str, enum.Enum):
    STANDARD = "standard"
    RUSH = "rush"


class ModelRole(str, enum.Enum):
    WRITER = "writer"
    CRITIC = "critic"
    MEMORY = "memory"
    PROMPT_BUILDER = "prompt_builder"


class ProviderChannel(Base):
    __tablename__ = "provider_channels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50))  # 'mock' or 'openai_compatible'
    base_url: Mapped[str] = mapped_column(Text)
    default_model_name: Mapped[str] = mapped_column(String(200))
    api_key_env_var: Mapped[str | None] = mapped_column(String(100), nullable=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # 加密存储的 API Key
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # 关系：一个渠道有多个模型
    models: Mapped[list["ProviderChannelModel"]] = relationship(
        "ProviderChannelModel", back_populates="channel", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<ProviderChannel(name='{self.name}', provider='{self.provider}', is_enabled={self.is_enabled})>"


class ProviderChannelModel(Base):
    """渠道下的可用模型，支持一个渠道关联多个模型"""

    __tablename__ = "provider_channel_models"
    __table_args__ = (UniqueConstraint("channel_id", "model_name", name="uq_channel_model_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_channels.id", ondelete="CASCADE")
    )
    model_name: Mapped[str] = mapped_column(String(200))  # 模型标识名，如 gpt-4o
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 展示名
    provider_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 原始provider返回的ID
    owned_by: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 拥有者信息
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # 原始响应数据
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否是默认模型
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # 是否启用
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 上次同步时间
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # 关系
    channel: Mapped["ProviderChannel"] = relationship("ProviderChannel", back_populates="models")

    def __repr__(self) -> str:
        return f"<ProviderChannelModel(channel_id='{self.channel_id}', model_name='{self.model_name}')>"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    genre: Mapped[str | None] = mapped_column(String(50))
    style: Mapped[str | None] = mapped_column(String(50))
    global_prompt: Mapped[str] = mapped_column(Text)
    total_chapters: Mapped[int] = mapped_column(Integer)
    current_chapter: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ProjectStatus] = mapped_column(String(30), default=ProjectStatus.DRAFT.value)
    auto_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_accept_critic_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_accept_on_max_retries: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    hard_review_gates_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    max_retries: Mapped[int] = mapped_column(Integer, default=5)
    writer_streaming_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    generation_mode: Mapped[str] = mapped_column(String(30), default=GenerationMode.STANDARD.value, server_default=text("'standard'"))
    rush_previous_chapter_count: Mapped[int] = mapped_column(Integer, default=10, server_default=text("10"))
    last_error: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    distant_memory_cache: Mapped[str | None] = mapped_column(Text)
    distant_memory_updated_chapter: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    model_configs = relationship("ProjectModelConfig", back_populates="project", cascade="all, delete-orphan")
    outlines = relationship("ChapterOutline", back_populates="project", cascade="all, delete-orphan")
    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")
    prompts = relationship("ChapterPrompt", back_populates="project", cascade="all, delete-orphan")
    summaries = relationship("ChapterSummary", back_populates="project", cascade="all, delete-orphan")
    characters = relationship("Character", back_populates="project", cascade="all, delete-orphan")
    world_settings = relationship("WorldSetting", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("GenerationJob", back_populates="project", cascade="all, delete-orphan")
    events = relationship("ProjectEvent", back_populates="project", cascade="all, delete-orphan")


class ProjectModelConfig(Base):
    __tablename__ = "project_model_configs"
    __table_args__ = (UniqueConstraint("project_id", "role", name="uq_project_role"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    role: Mapped[ModelRole] = mapped_column(String(30))
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_channels.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(50))
    base_url: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(200))
    temperature: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)

    project = relationship("Project", back_populates="model_configs")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    default_total_chapters: Mapped[int] = mapped_column(Integer, default=60)
    default_auto_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    default_max_retries: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SystemRuntimeSetting(Base):
    __tablename__ = "system_runtime_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    review_overall_score_threshold: Mapped[float] = mapped_column(Numeric(4, 2), default=8.0)
    review_outline_score_threshold: Mapped[float] = mapped_column(Numeric(4, 2), default=8.0)
    review_instruction_score_threshold: Mapped[float] = mapped_column(Numeric(4, 2), default=8.0)
    memory_auto_apply_confidence_threshold: Mapped[float] = mapped_column(Numeric(4, 2), default=0.75)
    writer_target_input_tokens: Mapped[int] = mapped_column(Integer, default=64000)
    writer_hard_limit_tokens: Mapped[int] = mapped_column(Integer, default=96000)
    critic_target_input_tokens: Mapped[int] = mapped_column(Integer, default=32000)
    critic_hard_limit_tokens: Mapped[int] = mapped_column(Integer, default=48000)
    llm_stage_timeout_seconds: Mapped[float] = mapped_column(Numeric(8, 2), default=180.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SystemModelConfig(Base):
    __tablename__ = "system_model_configs"
    __table_args__ = (UniqueConstraint("role", name="uq_system_model_role"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role: Mapped[ModelRole] = mapped_column(String(30))
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_channels.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(50))
    base_url: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(200))
    temperature: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
