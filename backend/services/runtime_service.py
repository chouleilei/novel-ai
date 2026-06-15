import os
import uuid
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ModelRole, ProjectModelConfig, ProviderChannel
from backend.llm.base import BaseLLMClient
from backend.llm.factory import build_llm_client
from backend.model_config_utils import normalize_role
from backend.services.secret_service import decrypt_model_api_key, has_configured_encryption_key


ROLE_ENV_KEY = {
    ModelRole.WRITER.value: "WRITER_API_KEY",
    ModelRole.CRITIC.value: "CRITIC_API_KEY",
    ModelRole.MEMORY.value: "MEMORY_API_KEY",
    ModelRole.PROMPT_BUILDER.value: "PROMPT_BUILDER_API_KEY",
}


class RuntimeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_model_configs(self, project_id: uuid.UUID) -> dict[str, ProjectModelConfig]:
        stmt = select(ProjectModelConfig).where(ProjectModelConfig.project_id == project_id)
        result = await self.session.execute(stmt)
        configs = list(result.scalars())
        if not configs:
            from backend.services.project_service import ProjectService

            configs = await ProjectService(self.session).get_model_configs(project_id)
        return {normalize_role(item.role): item for item in configs}

    async def get_client(self, project_id: uuid.UUID, role: str, fallback_role: str | None = None) -> BaseLLMClient:
        configs = await self.get_model_configs(project_id)
        normalized_role = normalize_role(role)
        normalized_fallback_role = normalize_role(fallback_role) if fallback_role is not None else None
        config = configs.get(normalized_role)
        if config is None and normalized_fallback_role is not None:
            config = configs.get(normalized_fallback_role)
        if config is None:
            raise ValueError(f"缺少模型配置: {normalized_role}")
        api_key, env_key = await self.resolve_api_key(config)
        return build_llm_client(config, api_key=api_key, api_key_name=env_key)

    async def resolve_api_key(self, config) -> tuple[str | None, str | None]:
        extra_config = config.extra_config if isinstance(config.extra_config, Mapping) else {}
        plain_api_key = extra_config.get("api_key")
        if isinstance(plain_api_key, str) and plain_api_key.strip():
            return plain_api_key.strip(), None

        encrypted_api_key = extra_config.get("api_key_encrypted")
        if isinstance(encrypted_api_key, str) and encrypted_api_key.strip():
            return decrypt_model_api_key(encrypted_api_key), None

        custom_env_key = extra_config.get("api_key_env_var")
        if isinstance(custom_env_key, str) and custom_env_key.strip():
            env_key = custom_env_key.strip()
            return os.getenv(env_key), env_key

        channel_api_key, channel_env_key = await self._resolve_channel_api_key(config)
        if channel_api_key is not None or channel_env_key is not None:
            return channel_api_key, channel_env_key

        env_key = ROLE_ENV_KEY.get(normalize_role(config.role))
        return (os.getenv(env_key) if env_key else None, env_key)

    async def _resolve_channel_api_key(self, config) -> tuple[str | None, str | None]:
        channel_id = getattr(config, "channel_id", None)
        if not channel_id:
            return None, None

        channel = await self.session.get(ProviderChannel, channel_id)
        if channel is None:
            return None, None

        stored_api_key = channel.api_key.strip() if isinstance(channel.api_key, str) and channel.api_key.strip() else None
        if stored_api_key:
            if has_configured_encryption_key():
                try:
                    return decrypt_model_api_key(stored_api_key), None
                except ValueError:
                    # Backward compatibility for legacy plaintext channel secrets.
                    return stored_api_key, None
            return stored_api_key, None

        env_key = channel.api_key_env_var.strip() if isinstance(channel.api_key_env_var, str) and channel.api_key_env_var.strip() else None
        if env_key:
            return os.getenv(env_key), env_key
        return None, None
