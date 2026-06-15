import uuid
from copy import deepcopy
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import (
    Character,
    CharacterRevision,
    Chapter,
    ChapterOutline,
    ChapterPrompt,
    ChapterStatus,
    ChapterSummary,
    GenerationJob,
    GenerationMode,
    ModelRole,
    Project,
    ProjectEvent,
    ProjectModelConfig,
    ProjectStatus,
    WorldSetting,
    WorldSettingRevision,
)
from backend.model_config_utils import merge_extra_config_for_storage, normalize_role
from backend.services.system_settings_service import SystemSettingsService


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.system_settings = SystemSettingsService(session)

    async def create_project(
        self,
        *,
        title: str,
        genre: str | None,
        style: str | None,
        global_prompt: str,
        total_chapters: int,
        auto_mode: bool,
        auto_accept_critic_failed: bool,
        auto_accept_on_max_retries: bool,
        hard_review_gates_enabled: bool,
        max_retries: int,
        writer_streaming_enabled: bool = False,
        generation_mode: str = GenerationMode.STANDARD.value,
        rush_previous_chapter_count: int = 10,
    ) -> Project:
        project = Project(
            title=title,
            genre=genre,
            style=style,
            global_prompt=global_prompt,
            total_chapters=total_chapters,
            auto_mode=auto_mode,
            auto_accept_critic_failed=auto_accept_critic_failed,
            auto_accept_on_max_retries=auto_accept_on_max_retries,
            hard_review_gates_enabled=hard_review_gates_enabled,
            max_retries=max_retries,
            writer_streaming_enabled=writer_streaming_enabled,
            generation_mode=getattr(generation_mode, "value", generation_mode),
            rush_previous_chapter_count=rush_previous_chapter_count,
            status=ProjectStatus.DRAFT.value,
        )
        self.session.add(project)
        await self.session.flush()
        await self.save_model_configs(project.id, await self.system_settings.build_default_model_snapshots_for_project())
        return project

    async def list_projects(self) -> list[Project]:
        result = await self.session.execute(select(Project).order_by(Project.created_at.desc()))
        return list(result.scalars())

    async def get_project(self, project_id: uuid.UUID) -> Project | None:
        return await self.session.get(Project, project_id)

    async def update_project(
        self,
        project_id: uuid.UUID,
        *,
        title: str,
        genre: str | None,
        style: str | None,
        global_prompt: str,
        total_chapters: int,
        auto_mode: bool,
        auto_accept_critic_failed: bool,
        auto_accept_on_max_retries: bool,
        hard_review_gates_enabled: bool,
        max_retries: int,
        writer_streaming_enabled: bool = False,
        generation_mode: str = GenerationMode.STANDARD.value,
        rush_previous_chapter_count: int = 10,
    ) -> Project | None:
        project = await self.get_project(project_id)
        if project is None:
            return None

        project.title = title
        project.genre = genre
        project.style = style
        project.global_prompt = global_prompt
        project.total_chapters = total_chapters
        project.auto_mode = auto_mode
        project.auto_accept_critic_failed = auto_accept_critic_failed
        project.auto_accept_on_max_retries = auto_accept_on_max_retries
        project.hard_review_gates_enabled = hard_review_gates_enabled
        project.max_retries = max_retries
        project.writer_streaming_enabled = writer_streaming_enabled
        project.generation_mode = getattr(generation_mode, "value", generation_mode)
        project.rush_previous_chapter_count = rush_previous_chapter_count
        await self.session.flush()
        return project

    async def copy_project(self, project_id: uuid.UUID) -> Project | None:
        source = await self.get_project(project_id)
        if source is None:
            return None

        copied_project = Project(
            title=self._build_copy_title(source.title),
            genre=source.genre,
            style=source.style,
            global_prompt=source.global_prompt,
            total_chapters=source.total_chapters,
            current_chapter=0,
            status=ProjectStatus.DRAFT.value,
            auto_mode=source.auto_mode,
            auto_accept_critic_failed=source.auto_accept_critic_failed,
            auto_accept_on_max_retries=source.auto_accept_on_max_retries,
            hard_review_gates_enabled=source.hard_review_gates_enabled,
            max_retries=source.max_retries,
            writer_streaming_enabled=source.writer_streaming_enabled,
            generation_mode=source.generation_mode,
            rush_previous_chapter_count=source.rush_previous_chapter_count,
        )
        self.session.add(copied_project)
        await self.session.flush()

        for config in await self.get_model_configs(project_id):
            self.session.add(
                ProjectModelConfig(
                    project_id=copied_project.id,
                    role=cast(str, config.role),
                    channel_id=config.channel_id,
                    provider=config.provider,
                    base_url=config.base_url,
                    model_name=config.model_name,
                    temperature=config.temperature,
                    max_tokens=config.max_tokens,
                    extra_config=deepcopy(config.extra_config) if config.extra_config else {},
                )
            )

        outlines = await self.list_outlines(project_id)
        for outline in outlines:
            self.session.add(
                ChapterOutline(
                    project_id=copied_project.id,
                    chapter_number=outline.chapter_number,
                    outline_text=outline.outline_text,
                    tags=deepcopy(outline.tags) if outline.tags else {},
                )
            )
            self.session.add(
                Chapter(
                    project_id=copied_project.id,
                    chapter_number=outline.chapter_number,
                    status=ChapterStatus.PENDING.value,
                )
            )

        if outlines:
            copied_project.total_chapters = len(outlines)
            copied_project.status = ProjectStatus.READY.value

        await self.session.flush()
        return copied_project

    async def delete_project(self, project_id: uuid.UUID) -> bool:
        project = await self.get_project(project_id)
        if project is None:
            return False
        await self.session.delete(project)
        await self.session.flush()
        return True

    @staticmethod
    def _build_copy_title(title: str) -> str:
        suffix = "（副本）"
        max_length = 200
        if len(title) + len(suffix) <= max_length:
            return f"{title}{suffix}"
        return f"{title[: max_length - len(suffix)]}{suffix}"

    async def save_model_configs(self, project_id: uuid.UUID, configs: list[dict[str, Any]]) -> list[ProjectModelConfig]:
        stmt = select(ProjectModelConfig).where(ProjectModelConfig.project_id == project_id)
        result = await self.session.execute(stmt)
        existing_configs = list(result.scalars())
        existing_by_role = {normalize_role(item.role): item for item in existing_configs}
        payload_roles = {normalize_role(item["role"]) for item in configs}

        saved: list[ProjectModelConfig] = []
        for item in configs:
            role = normalize_role(item["role"])
            existing_config = existing_by_role.get(role)
            merged_extra_config = merge_extra_config_for_storage(
                existing_config.extra_config if existing_config is not None else None,
                item.get("extra_config"),
                role=role,
                scope_label="项目级",
            )

            config = existing_config
            if config is None:
                config = ProjectModelConfig(project_id=project_id, role=role)
                self.session.add(config)

            config.provider = cast(str, item["provider"])
            config.base_url = cast(str, item["base_url"])
            config.model_name = cast(str, item["model_name"])
            channel_id = item.get("channel_id")
            config.channel_id = uuid.UUID(cast(str, channel_id)) if channel_id else None
            config.temperature = item.get("temperature")
            config.max_tokens = item.get("max_tokens")
            config.extra_config = merged_extra_config
            saved.append(config)

        for role, config in existing_by_role.items():
            if role not in payload_roles:
                await self.session.delete(config)

        await self.session.flush()
        return saved

    async def get_model_configs(self, project_id: uuid.UUID) -> list[ProjectModelConfig]:
        stmt = select(ProjectModelConfig).where(ProjectModelConfig.project_id == project_id)
        result = await self.session.execute(stmt)
        configs = list(result.scalars())
        if configs:
            role_map = {normalize_role(item.role): item for item in configs}
            for item in await self._build_default_model_config_entities(project_id):
                if normalize_role(item.role) not in role_map:
                    configs.append(item)
            return configs
        project = await self.get_project(project_id)
        if project is None:
            return []
        return await self._build_default_model_config_entities(project_id)

    async def get_default_model_configs(self) -> list[dict[str, Any]]:
        return await self.system_settings.build_default_model_snapshots_for_project()

    async def get_default_project_seed(self) -> dict[str, Any]:
        return await self.system_settings.build_project_seed_payload_async()

    async def import_outlines(self, project_id: uuid.UUID, outlines: list[dict[str, Any]]) -> None:
        await self.session.execute(delete(GenerationJob).where(GenerationJob.project_id == project_id))
        await self.session.execute(delete(ProjectEvent).where(ProjectEvent.project_id == project_id))
        await self.session.execute(delete(ChapterPrompt).where(ChapterPrompt.project_id == project_id))
        await self.session.execute(delete(ChapterSummary).where(ChapterSummary.project_id == project_id))
        await self.session.execute(delete(CharacterRevision).where(CharacterRevision.project_id == project_id))
        await self.session.execute(delete(WorldSettingRevision).where(WorldSettingRevision.project_id == project_id))
        await self.session.execute(delete(Character).where(Character.project_id == project_id))
        await self.session.execute(delete(WorldSetting).where(WorldSetting.project_id == project_id))
        await self.session.execute(delete(ChapterOutline).where(ChapterOutline.project_id == project_id))
        await self.session.execute(delete(Chapter).where(Chapter.project_id == project_id))

        for item in outlines:
            chapter_number = item["chapter_number"]
            self.session.add(
                ChapterOutline(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    outline_text=item["outline_text"],
                    tags=item.get("tags") or {},
                )
            )
            self.session.add(
                Chapter(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    status=ChapterStatus.PENDING.value,
                )
            )
        project = await self.get_project(project_id)
        if project:
            project.current_chapter = 0
            project.last_error = None
            project.total_chapters = len(outlines)
            project.status = ProjectStatus.READY.value
            project.lease_owner = None
            project.lease_expires_at = None
            project.distant_memory_cache = None
            project.distant_memory_updated_chapter = None
        await self.session.flush()

    async def list_outlines(self, project_id: uuid.UUID) -> list[ChapterOutline]:
        stmt = (
            select(ChapterOutline)
            .where(ChapterOutline.project_id == project_id)
            .order_by(ChapterOutline.chapter_number.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def update_outline(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        outline_text: str,
        tags: dict[str, object] | None = None,
    ) -> ChapterOutline | None:
        stmt = select(ChapterOutline).where(
            ChapterOutline.project_id == project_id,
            ChapterOutline.chapter_number == chapter_number,
        )
        result = await self.session.execute(stmt)
        outline = result.scalar_one_or_none()
        if not outline:
            return None
        outline.outline_text = outline_text
        outline.tags = tags or {}
        await self.session.flush()
        return outline

    async def ensure_required_model_roles(self, project_id: uuid.UUID) -> bool:
        project = await self.get_project(project_id)
        configs = await self.get_model_configs(project_id)
        roles = {normalize_role(item.role) for item in configs}
        if project is not None and getattr(project, "generation_mode", GenerationMode.STANDARD.value) == GenerationMode.RUSH.value:
            return ModelRole.WRITER.value in roles
        return {
            ModelRole.WRITER.value,
            ModelRole.CRITIC.value,
            ModelRole.MEMORY.value,
        }.issubset(roles)

    async def _build_default_model_config_entities(self, project_id: uuid.UUID) -> list[ProjectModelConfig]:
        return [
            ProjectModelConfig(
                id=uuid.uuid4(),
                project_id=project_id,
                role=cast(str, item["role"]),
                channel_id=uuid.UUID(cast(str, item["channel_id"])) if item.get("channel_id") else None,
                provider=cast(str, item["provider"]),
                base_url=cast(str, item["base_url"]),
                model_name=cast(str, item["model_name"]),
                temperature=item.get("temperature"),
                max_tokens=item.get("max_tokens"),
                extra_config=item.get("extra_config") or {},
            )
            for item in await self.system_settings.build_default_model_snapshots_for_project()
        ]
