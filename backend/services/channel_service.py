import os
import uuid
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.project import ProviderChannel, ProviderChannelModel
from backend.datetime_utils import utcnow
from backend.services.provider_discovery_service import (
    ProviderDiscoveryError,
    ProviderDiscoveryService,
)
from backend.services.secret_service import decrypt_model_api_key, encrypt_model_api_key, has_configured_encryption_key


class ChannelService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.discovery = ProviderDiscoveryService()

    async def create_channel(
        self,
        name: str,
        provider: str,
        base_url: str,
        default_model_name: str,
        api_key: str | None = None,
        api_key_env_var: str | None = None,
        clear_api_key: bool = False,
        is_enabled: bool = True,
    ) -> ProviderChannel:
        channel = ProviderChannel(
            name=name,
            provider=provider,
            base_url=base_url,
            default_model_name=default_model_name,
            api_key=None if clear_api_key else self._prepare_api_key_for_storage(api_key),
            api_key_env_var=api_key_env_var,
            is_enabled=is_enabled,
        )
        self.session.add(channel)
        await self.session.flush()
        return channel

    async def get_channel(self, channel_id: uuid.UUID) -> ProviderChannel | None:
        stmt = select(ProviderChannel).where(ProviderChannel.id == channel_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_channel_with_models(self, channel_id: uuid.UUID) -> ProviderChannel | None:
        """Get channel with its associated models eagerly loaded."""
        stmt = (
            select(ProviderChannel)
            .where(ProviderChannel.id == channel_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_channels(self) -> list[ProviderChannel]:
        stmt = select(ProviderChannel).order_by(ProviderChannel.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_channels_with_models(self) -> list[ProviderChannel]:
        """List all channels with their models eagerly loaded."""
        stmt = select(ProviderChannel).order_by(ProviderChannel.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def update_channel(self, channel_id: uuid.UUID, **kwargs: Any) -> ProviderChannel:
        channel = await self.get_channel(channel_id)
        if channel is None:
            raise ValueError(f"Channel with id {channel_id} not found")

        clear_api_key = bool(kwargs.pop("clear_api_key", False))
        if clear_api_key:
            channel.api_key = None

        for key, value in kwargs.items():
            if key == "api_key":
                if isinstance(value, str) and value.strip():
                    channel.api_key = self._prepare_api_key_for_storage(value)
                continue
            if hasattr(channel, key):
                setattr(channel, key, value)

        await self.session.flush()
        return channel

    async def delete_channel(self, channel_id: uuid.UUID) -> bool:
        channel = await self.get_channel(channel_id)
        if channel is None:
            return False

        await self.session.delete(channel)
        await self.session.flush()
        return True

    async def get_channel_defaults(self, channel_id: uuid.UUID) -> dict[str, Any]:
        channel = await self.get_channel(channel_id)
        if channel is None:
            raise ValueError(f"Channel with id {channel_id} not found")

        return {
            "provider": channel.provider,
            "base_url": channel.base_url,
            "api_key_env_var": channel.api_key_env_var,
            "has_api_key": bool(channel.api_key),
        }

    # ProviderChannelModel operations

    async def list_channel_models(self, channel_id: uuid.UUID) -> list[ProviderChannelModel]:
        """List all models for a specific channel."""
        stmt = (
            select(ProviderChannelModel)
            .where(ProviderChannelModel.channel_id == channel_id)
            .where(ProviderChannelModel.is_enabled == True)
            .order_by(ProviderChannelModel.model_name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_default_channel_model(self, channel_id: uuid.UUID) -> ProviderChannelModel | None:
        """Get the default model for a channel."""
        stmt = (
            select(ProviderChannelModel)
            .where(ProviderChannelModel.channel_id == channel_id)
            .where(ProviderChannelModel.is_default == True)
            .where(ProviderChannelModel.is_enabled == True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_default_model(self, channel_id: uuid.UUID, model_name: str) -> ProviderChannelModel:
        """Set a model as the default for a channel."""
        stmt = (
            select(ProviderChannelModel)
            .where(ProviderChannelModel.channel_id == channel_id)
        )
        result = await self.session.execute(stmt)
        models = list(result.scalars())
        model = next((item for item in models if item.model_name == model_name), None)

        if model is None:
            raise ValueError(f"Model '{model_name}' not found in channel {channel_id}")

        for item in models:
            item.is_default = item.model_name == model_name
        await self.session.flush()
        return model

    async def try_set_default_model(self, channel_id: uuid.UUID, model_name: str) -> ProviderChannelModel | None:
        stmt = (
            select(ProviderChannelModel)
            .where(ProviderChannelModel.channel_id == channel_id)
            .where(ProviderChannelModel.model_name == model_name)
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return await self.set_default_model(channel_id, model_name)

    async def discover_channel_models(
        self,
        provider: str,
        base_url: str,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Discover available models from a provider without saving.

        This is used for the 'Test Connection' and 'Fetch Models' buttons
        in the ChannelForm before the channel is saved.
        """
        return await self.discovery.discover_channel(provider, base_url, api_key)

    async def refresh_channel_models(
        self,
        channel_id: uuid.UUID,
        provider_override: str | None = None,
        base_url_override: str | None = None,
        api_key_override: str | None = None,
    ) -> dict[str, Any]:
        """Refresh the model list for a saved channel.

        Fetches models from the provider and updates the database.
        """
        channel = await self.get_channel(channel_id)
        if channel is None:
            raise ValueError(f"Channel with id {channel_id} not found")

        provider = provider_override or channel.provider
        base_url = base_url_override or channel.base_url
        api_key = self._resolve_channel_api_key(channel, api_key_override)

        try:
            discovery_result = await self.discovery.discover_channel(
                provider,
                base_url,
                api_key,
            )
        except ProviderDiscoveryError as e:
            return {
                "success": False,
                "message": str(e),
                "models": [],
                "default_model_name": None,
            }

        if not discovery_result["success"]:
            return discovery_result

        # Clear existing models for this channel
        stmt = sa_delete(ProviderChannelModel).where(ProviderChannelModel.channel_id == channel_id)
        await self.session.execute(stmt)

        # Insert new models
        now = utcnow()
        available_models = discovery_result.get("models", [])
        persisted_default_model_name = channel.default_model_name
        default_model_name = persisted_default_model_name
        if not default_model_name or not any(model["model_name"] == default_model_name for model in available_models):
            default_model_name = discovery_result.get("default_model_name")

        for model_data in available_models:
            model = ProviderChannelModel(
                channel_id=channel_id,
                model_name=model_data["model_name"],
                display_name=model_data.get("display_name"),
                provider_model_id=model_data.get("provider_model_id"),
                owned_by=model_data.get("owned_by"),
                raw_payload=model_data.get("raw_payload"),
                is_default=(model_data["model_name"] == default_model_name),
                is_enabled=True,
                last_synced_at=now,
            )
            self.session.add(model)

        # Update channel's default_model_name to match
        if default_model_name:
            channel.default_model_name = default_model_name

        await self.session.flush()

        refreshed_models = [
            {
                "id": None,
                "model_name": model_data["model_name"],
                "display_name": model_data.get("display_name"),
                "provider_model_id": model_data.get("provider_model_id"),
                "owned_by": model_data.get("owned_by"),
                "is_default": model_data["model_name"] == default_model_name,
                "is_enabled": True,
            }
            for model_data in available_models
        ]

        return {
            "success": True,
            "message": f"Successfully refreshed {len(discovery_result['models'])} models",
            "models": refreshed_models,
            "models_count": len(discovery_result["models"]),
            "default_model_name": default_model_name,
        }

    async def replace_channel_models(
        self,
        channel_id: uuid.UUID,
        models_data: list[dict[str, Any]],
        default_model_name: str | None = None,
    ) -> list[ProviderChannelModel]:
        """Replace all models for a channel with the provided list.

        Used when frontend sends back discovered models to save.
        """
        channel = await self.get_channel(channel_id)
        if channel is None:
            raise ValueError(f"Channel with id {channel_id} not found")

        # Clear existing models
        stmt = sa_delete(ProviderChannelModel).where(ProviderChannelModel.channel_id == channel_id)
        await self.session.execute(stmt)

        # Insert new models
        now = utcnow()
        saved_models = []

        for model_data in models_data:
            model = ProviderChannelModel(
                channel_id=channel_id,
                model_name=model_data["model_name"],
                display_name=model_data.get("display_name"),
                provider_model_id=model_data.get("provider_model_id"),
                owned_by=model_data.get("owned_by"),
                raw_payload=model_data.get("raw_payload"),
                is_default=(model_data["model_name"] == default_model_name),
                is_enabled=model_data.get("is_enabled", True),
                last_synced_at=now,
            )
            self.session.add(model)
            saved_models.append(model)

        # Update channel's default_model_name
        if default_model_name:
            channel.default_model_name = default_model_name

        await self.session.flush()
        return saved_models

    def _prepare_api_key_for_storage(self, api_key: str | None) -> str | None:
        if not isinstance(api_key, str) or not api_key.strip():
            return None
        normalized = api_key.strip()
        if has_configured_encryption_key():
            return encrypt_model_api_key(normalized)
        return normalized

    def _resolve_channel_api_key(self, channel: ProviderChannel, api_key_override: str | None = None) -> str | None:
        if isinstance(api_key_override, str) and api_key_override.strip():
            return api_key_override.strip()

        stored_api_key = channel.api_key.strip() if isinstance(channel.api_key, str) and channel.api_key.strip() else None
        if stored_api_key:
            if has_configured_encryption_key():
                try:
                    return decrypt_model_api_key(stored_api_key)
                except ValueError:
                    # Backward compatibility for historical plaintext rows.
                    return stored_api_key
            return stored_api_key

        env_var = channel.api_key_env_var.strip() if isinstance(channel.api_key_env_var, str) and channel.api_key_env_var.strip() else None
        if env_var:
            return os.getenv(env_var)
        return None
