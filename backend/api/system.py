import uuid

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.datetime_utils import serialize_datetime
from backend.db.base import get_db_session
from backend.model_config_utils import (
    ModelConfigItem,
    ModelConnectivityTestRequest,
    serialize_model_extra_config,
    validate_model_configs_payload,
)
from backend.services.channel_service import ChannelService
from backend.services.model_connectivity_service import ModelConnectivityService
from backend.services.system_runtime_settings_service import SystemRuntimeSettingsService
from backend.services.system_settings_service import SystemSettingsService

router = APIRouter(prefix="/api/system", tags=["system"])


class SystemProjectDefaultsRequest(BaseModel):
    default_total_chapters: int = Field(ge=1)
    default_auto_mode: bool
    default_max_retries: int = Field(ge=1, le=20)


class ReviewPolicyRequest(BaseModel):
    overall_score_threshold: float = Field(ge=0, le=10)
    outline_score_threshold: float = Field(ge=0, le=10)
    instruction_score_threshold: float = Field(ge=0, le=10)


class MemoryPolicyRequest(BaseModel):
    auto_apply_confidence_threshold: float = Field(ge=0, le=1)


class ContextBudgetRequest(BaseModel):
    writer_target_input_tokens: int = Field(gt=0)
    writer_hard_limit_tokens: int = Field(gt=0)
    critic_target_input_tokens: int = Field(gt=0)
    critic_hard_limit_tokens: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_limits(self):
        if self.writer_hard_limit_tokens < self.writer_target_input_tokens:
            raise ValueError("writer_hard_limit_tokens 必须大于等于 writer_target_input_tokens")
        if self.critic_hard_limit_tokens < self.critic_target_input_tokens:
            raise ValueError("critic_hard_limit_tokens 必须大于等于 critic_target_input_tokens")
        return self


class ExecutionPolicyRequest(BaseModel):
    llm_stage_timeout_seconds: float = Field(gt=0)


class ChannelModelInfo(BaseModel):
    """Model information for a channel."""

    id: str | None = None
    model_name: str
    display_name: str | None = None
    provider_model_id: str | None = None
    owned_by: str | None = None
    is_default: bool = False
    is_enabled: bool = True


class ChannelCreateRequest(BaseModel):
    name: str
    provider: str
    base_url: str
    default_model_name: str
    api_key: str | None = None
    api_key_env_var: str | None = None
    clear_api_key: bool = False
    is_enabled: bool = True
    discovered_models: list[ChannelModelInfo] | None = None


class ChannelUpdateRequest(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    default_model_name: str | None = None
    api_key: str | None = None
    api_key_env_var: str | None = None
    clear_api_key: bool | None = None
    is_enabled: bool | None = None
    discovered_models: list[ChannelModelInfo] | None = None


class ChannelResponse(BaseModel):
    id: str
    name: str
    provider: str
    base_url: str
    default_model_name: str
    api_key_env_var: str | None = None
    has_api_key: bool
    is_enabled: bool
    created_at: str
    updated_at: str


class SystemRuntimeSettingsRequest(BaseModel):
    review_policy: ReviewPolicyRequest
    memory_policy: MemoryPolicyRequest
    context_budget: ContextBudgetRequest
    execution: ExecutionPolicyRequest


@router.get("/settings")
async def get_system_settings(session: AsyncSession = Depends(get_db_session)):
    service = SystemSettingsService(session)
    runtime_service = SystemRuntimeSettingsService(session)
    payload = await service.get_settings_payload()
    runtime_settings = await runtime_service.get_settings_payload()
    return {
        "project_defaults": payload["project_defaults"],
        "model_configs": [
            {
                **item,
                "extra_config": serialize_model_extra_config(
                    item.get("role"),
                    item.get("extra_config"),
                    include_has_api_key=True,
                    has_api_key_override=bool((item.get("extra_config") or {}).get("api_key_encrypted")),
                ),
            }
            for item in payload["model_configs"]
        ],
        "runtime_settings": runtime_settings,
    }


@router.put("/project-defaults")
async def save_project_defaults(
    payload: SystemProjectDefaultsRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = SystemSettingsService(session)
    saved = await service.save_project_defaults(**payload.model_dump())
    await session.commit()
    return {
        "default_total_chapters": saved.default_total_chapters,
        "default_auto_mode": saved.default_auto_mode,
        "default_max_retries": saved.default_max_retries,
    }


@router.put("/runtime-settings")
async def save_runtime_settings(
    payload: SystemRuntimeSettingsRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = SystemRuntimeSettingsService(session)
    snapshot = service.get_runtime_snapshot_from_payload(payload.model_dump())
    saved = await service.save_runtime_settings(**snapshot)
    await session.commit()
    return service.serialize_runtime_settings(saved)


@router.put("/model-configs")
async def save_system_model_configs(
    payload: list[ModelConfigItem],
    session: AsyncSession = Depends(get_db_session),
):
    validate_model_configs_payload(payload)
    service = SystemSettingsService(session)
    try:
        configs = await service.save_model_configs([item.model_dump(mode="python", exclude_unset=True) for item in payload])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {"count": len(configs)}


@router.post("/model-configs/test")
async def test_system_model_connectivity(
    payload: ModelConnectivityTestRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = ModelConnectivityService(session)
    return await service.test_system_model(payload.model_dump(mode="python", exclude_unset=True))


def serialize_channel(channel: Any) -> dict[str, Any]:
    return {
        "id": str(channel.id),
        "name": channel.name,
        "provider": channel.provider,
        "base_url": channel.base_url,
        "default_model_name": channel.default_model_name,
        "api_key_env_var": channel.api_key_env_var,
        "has_api_key": bool(channel.api_key),
        "is_enabled": channel.is_enabled,
        "created_at": serialize_datetime(channel.created_at),
        "updated_at": serialize_datetime(channel.updated_at),
    }


@router.get("/channels")
async def list_channels(session: AsyncSession = Depends(get_db_session)):
    service = ChannelService(session)
    channels = await service.list_channels()
    return [serialize_channel(c) for c in channels]


@router.post("/channels")
async def create_channel(
    payload: ChannelCreateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = ChannelService(session)
    channel = await service.create_channel(**payload.model_dump(exclude={"discovered_models"}))
    if payload.discovered_models is not None:
        await service.replace_channel_models(
            channel.id,
            [item.model_dump(exclude_unset=True) for item in payload.discovered_models],
            default_model_name=channel.default_model_name,
        )
    await session.commit()
    return serialize_channel(channel)


@router.get("/channels/{channel_id}")
async def get_channel(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    service = ChannelService(session)
    channel = await service.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return serialize_channel(channel)


@router.put("/channels/{channel_id}")
async def update_channel(
    channel_id: uuid.UUID,
    payload: ChannelUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = ChannelService(session)
    try:
        channel = await service.update_channel(
            channel_id, **payload.model_dump(exclude_unset=True, exclude={"discovered_models"})
        )
        if payload.discovered_models is not None:
            await service.replace_channel_models(
                channel_id,
                [item.model_dump(exclude_unset=True) for item in payload.discovered_models],
                default_model_name=channel.default_model_name,
            )
        elif payload.default_model_name:
            await service.try_set_default_model(channel_id, payload.default_model_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await session.commit()
    return serialize_channel(channel)


@router.delete("/channels/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    service = ChannelService(session)
    success = await service.delete_channel(channel_id)
    if not success:
        raise HTTPException(status_code=404, detail="渠道不存在")
    await session.commit()


# Channel Model Discovery and Management Endpoints

class ChannelDiscoverRequest(BaseModel):
    """Request to discover models from a provider without saving the channel."""

    provider: str
    base_url: str
    api_key: str | None = None


class ChannelRefreshRequest(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None


def serialize_channel_model(model: Any) -> dict[str, Any]:
    return {
        "id": str(model.id) if model.id else None,
        "model_name": model.model_name,
        "display_name": model.display_name,
        "provider_model_id": model.provider_model_id,
        "owned_by": model.owned_by,
        "is_default": model.is_default,
        "is_enabled": model.is_enabled,
    }


@router.post("/channels/discover-models")
async def discover_channel_models(
    payload: ChannelDiscoverRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """Discover available models from a provider without saving.

    Used by ChannelForm to test connection and fetch models before saving.
    """
    service = ChannelService(session)
    result = await service.discover_channel_models(
        provider=payload.provider,
        base_url=payload.base_url,
        api_key=payload.api_key,
    )
    return result


@router.post("/channels/{channel_id}/refresh-models")
async def refresh_channel_models(
    channel_id: uuid.UUID,
    payload: ChannelRefreshRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    """Refresh the model list for a saved channel.

    Fetches models from the provider and updates the database.
    """
    service = ChannelService(session)
    try:
        result = await service.refresh_channel_models(
            channel_id,
            provider_override=payload.provider if payload else None,
            base_url_override=payload.base_url if payload else None,
            api_key_override=payload.api_key if payload else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await session.commit()
    return result


@router.get("/channels/{channel_id}/models")
async def list_channel_models(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    """List all models for a specific channel."""
    service = ChannelService(session)
    channel = await service.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="渠道不存在")

    models = await service.list_channel_models(channel_id)
    return [serialize_channel_model(m) for m in models]
