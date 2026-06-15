import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import ModelRole, SystemModelConfig, SystemSetting
from backend.model_config_utils import (
    merge_extra_config_for_storage,
    model_config_signature,
    normalize_role,
    should_enable_json_schema_by_default,
)


class SystemSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def get_project_defaults(self) -> SystemSetting:
        existing = await self.session.get(SystemSetting, 1)
        if existing is not None:
            return existing
        return SystemSetting(
            id=1,
            default_total_chapters=60,
            default_auto_mode=True,
            default_max_retries=5,
        )

    async def save_project_defaults(
        self,
        *,
        default_total_chapters: int,
        default_auto_mode: bool,
        default_max_retries: int,
    ) -> SystemSetting:
        current = await self.session.get(SystemSetting, 1)
        if current is None:
            current = SystemSetting(id=1)
            self.session.add(current)

        current.default_total_chapters = default_total_chapters
        current.default_auto_mode = default_auto_mode
        current.default_max_retries = default_max_retries
        await self.session.flush()
        return current

    async def get_model_configs(self) -> list[SystemModelConfig]:
        stmt = select(SystemModelConfig)
        result = await self.session.execute(stmt)
        configs = list(result.scalars())
        if not configs:
            return self._build_default_model_config_entities()

        role_map = {normalize_role(item.role): item for item in configs}
        env_role_map = {
            normalize_role(item.role): item
            for item in self._build_default_model_config_entities()
            if normalize_role(item.role) in {
                ModelRole.WRITER.value,
                ModelRole.CRITIC.value,
                ModelRole.MEMORY.value,
            }
        }
        for role, item in env_role_map.items():
            if role not in role_map:
                configs.append(item)
        return configs

    async def save_model_configs(self, configs: list[dict[str, Any]]) -> list[SystemModelConfig]:
        stmt = select(SystemModelConfig)
        result = await self.session.execute(stmt)
        existing_configs = list(result.scalars())
        existing_by_role = {normalize_role(item.role): item for item in existing_configs}
        payload_roles = {normalize_role(item["role"]) for item in configs}

        saved: list[SystemModelConfig] = []
        for item in configs:
            role = normalize_role(item["role"])
            existing_config = existing_by_role.get(role)
            merged_extra_config = merge_extra_config_for_storage(
                existing_config.extra_config if existing_config is not None else None,
                item.get("extra_config"),
                role=role,
                scope_label="系统级",
            )

            config = existing_config
            if config is None:
                config = SystemModelConfig(role=role)
                self.session.add(config)

            config.provider = item["provider"]
            config.base_url = item["base_url"]
            config.model_name = item["model_name"]
            config.channel_id = uuid.UUID(item["channel_id"]) if item.get("channel_id") else None
            config.temperature = item.get("temperature")
            config.max_tokens = item.get("max_tokens")
            config.extra_config = merged_extra_config
            saved.append(config)

        for role, config in existing_by_role.items():
            if role not in payload_roles:
                await self.session.delete(config)

        await self.session.flush()
        return saved

    async def get_settings_payload(self) -> dict[str, Any]:
        project_defaults = await self.get_project_defaults()
        model_configs = await self.get_model_configs()
        return {
            "project_defaults": {
                "default_total_chapters": project_defaults.default_total_chapters,
                "default_auto_mode": project_defaults.default_auto_mode,
                "default_max_retries": project_defaults.default_max_retries,
            },
            "model_configs": [self.serialize_model_config(item) for item in model_configs],
        }

    async def get_default_model_config_payloads(self) -> list[dict[str, Any]]:
        return [self.serialize_model_config(item) for item in await self.get_model_configs()]

    def serialize_model_config(self, config: SystemModelConfig) -> dict[str, Any]:
        return {
            "id": str(config.id) if config.id else None,
            "role": normalize_role(config.role),
            "channel_id": str(config.channel_id) if config.channel_id else None,
            "provider": config.provider,
            "base_url": config.base_url,
            "model_name": config.model_name,
            "temperature": float(config.temperature) if config.temperature is not None else None,
            "max_tokens": config.max_tokens,
            "extra_config": dict(config.extra_config or {}),
        }

    def build_project_seed_payload(self) -> dict[str, Any]:
        defaults = self._fallback_project_defaults_from_env()
        return {
            "total_chapters": defaults["default_total_chapters"],
            "auto_mode": defaults["default_auto_mode"],
            "auto_accept_critic_failed": False,
            "auto_accept_on_max_retries": False,
            "hard_review_gates_enabled": True,
            "max_retries": defaults["default_max_retries"],
            "writer_streaming_enabled": False,
        }

    async def build_project_seed_payload_async(self) -> dict[str, Any]:
        persisted = await self.session.get(SystemSetting, 1)
        if persisted is None:
            return self.build_project_seed_payload()
        return {
            "total_chapters": persisted.default_total_chapters,
            "auto_mode": persisted.default_auto_mode,
            "auto_accept_critic_failed": False,
            "auto_accept_on_max_retries": False,
            "hard_review_gates_enabled": True,
            "max_retries": persisted.default_max_retries,
            "writer_streaming_enabled": False,
        }

    async def build_default_model_snapshots_for_project(self) -> list[dict[str, Any]]:
        configs = await self.get_model_configs()
        snapshots = [self.serialize_model_config(item) for item in configs]
        writer_config = next((item for item in snapshots if item["role"] == ModelRole.WRITER.value), None)
        prompt_builder_config = next((item for item in snapshots if item["role"] == ModelRole.PROMPT_BUILDER.value), None)

        if writer_config is not None and prompt_builder_config is not None:
            if model_config_signature(prompt_builder_config) == model_config_signature(writer_config):
                snapshots = [item for item in snapshots if item["role"] != ModelRole.PROMPT_BUILDER.value]

        return [
            {
                **item,
                "extra_config": {
                    **dict(item.get("extra_config") or {}),
                    **(
                        {"supports_json_schema_output": True}
                        if should_enable_json_schema_by_default(item.get("role"), item.get("extra_config"))
                        else {}
                    ),
                },
            }
            for item in snapshots
        ]

    def _build_default_model_config_entities(self) -> list[SystemModelConfig]:
        return [
            SystemModelConfig(
                id=uuid.uuid4(),
                role=item["role"],
                channel_id=uuid.UUID(item["channel_id"]) if item.get("channel_id") else None,
                provider=item["provider"],
                base_url=item["base_url"],
                model_name=item["model_name"],
                temperature=item.get("temperature"),
                max_tokens=item.get("max_tokens"),
                extra_config=item.get("extra_config") or {},
            )
            for item in self._default_model_configs_from_env()
        ]

    def _fallback_project_defaults_from_env(self) -> dict[str, Any]:
        return {
            "default_total_chapters": 60,
            "default_auto_mode": True,
            "default_max_retries": 5,
        }

    def _default_model_configs_from_env(self) -> list[dict[str, Any]]:
        writer_config = {
            "role": ModelRole.WRITER.value,
            "provider": self.settings.default_writer_provider,
            "base_url": self.settings.default_writer_base_url,
            "model_name": self.settings.default_writer_model_name,
            "temperature": self.settings.default_writer_temperature,
            "max_tokens": self.settings.default_writer_max_tokens,
            "extra_config": {"api_key_env_var": self.settings.default_writer_api_key_env_var},
        }
        critic_config = {
            "role": ModelRole.CRITIC.value,
            "provider": self.settings.default_critic_provider,
            "base_url": self.settings.default_critic_base_url,
            "model_name": self.settings.default_critic_model_name,
            "temperature": self.settings.default_critic_temperature,
            "max_tokens": self.settings.default_critic_max_tokens,
            "extra_config": {
                "api_key_env_var": self.settings.default_critic_api_key_env_var,
                "supports_json_schema_output": True,
            },
        }
        memory_config = {
            "role": ModelRole.MEMORY.value,
            "provider": self.settings.default_memory_provider,
            "base_url": self.settings.default_memory_base_url,
            "model_name": self.settings.default_memory_model_name,
            "temperature": self.settings.default_memory_temperature,
            "max_tokens": self.settings.default_memory_max_tokens,
            "extra_config": {"api_key_env_var": self.settings.default_memory_api_key_env_var},
        }
        prompt_builder_config = {
            "role": ModelRole.PROMPT_BUILDER.value,
            "provider": self.settings.default_prompt_builder_provider,
            "base_url": self.settings.default_prompt_builder_base_url,
            "model_name": self.settings.default_prompt_builder_model_name,
            "temperature": self.settings.default_prompt_builder_temperature,
            "max_tokens": self.settings.default_prompt_builder_max_tokens,
            "extra_config": {"api_key_env_var": self.settings.default_prompt_builder_api_key_env_var},
        }

        return [writer_config, critic_config, memory_config, prompt_builder_config]
