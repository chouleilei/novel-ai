import re
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.db.models import ModelRole
from backend.services.secret_service import encrypt_model_api_key, has_configured_encryption_key

ENV_VAR_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
REQUIRED_MODEL_ROLES = {
    ModelRole.WRITER.value,
    ModelRole.CRITIC.value,
    ModelRole.MEMORY.value,
}
ALL_MODEL_ROLES: tuple[str, ...] = (
    ModelRole.WRITER.value,
    ModelRole.CRITIC.value,
    ModelRole.MEMORY.value,
    ModelRole.PROMPT_BUILDER.value,
)


class ModelExtraConfig(BaseModel):
    api_key_env_var: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    has_api_key: bool | None = None
    can_save_api_key: bool | None = None
    supports_json_schema_output: bool | None = None
    custom_params: dict[str, Any] | None = None

    @field_validator("api_key_env_var")
    @classmethod
    def normalize_api_key_env_var(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not ENV_VAR_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("API Key Env Var 必须是合法环境变量名，例如 WRITER_API_KEY")
        return normalized

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @field_validator("custom_params")
    @classmethod
    def normalize_custom_params(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if v is None:
                continue
            cleaned[k.strip()] = v
        return cleaned or None


class ModelConfigItem(BaseModel):
    role: Literal["writer", "critic", "memory", "prompt_builder"]
    channel_id: str | None = None
    provider: Literal["mock", "openai_compatible"]
    base_url: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    extra_config: ModelExtraConfig = Field(default_factory=ModelExtraConfig)

    @field_validator("base_url", "model_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("该字段不能为空")
        return normalized


class ModelConnectivityTestRequest(ModelConfigItem):
    pass


def normalize_role(role: str | ModelRole | None) -> str:
    if role is None:
        return ""
    value = role.value if isinstance(role, ModelRole) else role
    return str(value)


def should_enable_json_schema_by_default(role: str | ModelRole | None, extra_config: dict[str, Any] | None) -> bool:
    normalized_role = normalize_role(role)
    normalized_extra = extra_config or {}
    if normalized_role != ModelRole.CRITIC.value:
        return False
    if "supports_json_schema_output" not in normalized_extra:
        return True
    return bool(normalized_extra.get("supports_json_schema_output"))


def validate_model_configs_payload(payload: list[ModelConfigItem]) -> None:
    seen_roles: set[str] = set()
    duplicate_roles: list[str] = []
    for item in payload:
        if item.role in seen_roles and item.role not in duplicate_roles:
            duplicate_roles.append(item.role)
        seen_roles.add(item.role)

    if duplicate_roles:
        raise HTTPException(status_code=400, detail=f"模型角色不能重复: {', '.join(duplicate_roles)}")

    missing_roles = sorted(REQUIRED_MODEL_ROLES - seen_roles)
    if missing_roles:
        raise HTTPException(status_code=400, detail=f"模型配置缺少必需角色: {', '.join(missing_roles)}")


def serialize_model_extra_config(
    role: str | ModelRole | None,
    extra_config: dict[str, Any] | None,
    *,
    include_has_api_key: bool,
    has_api_key_override: bool | None = None,
) -> dict[str, object]:
    normalized = extra_config or {}
    payload: dict[str, object] = {
        "can_save_api_key": has_configured_encryption_key(),
    }

    api_key_env_var = normalized.get("api_key_env_var")
    if isinstance(api_key_env_var, str) and api_key_env_var.strip():
        payload["api_key_env_var"] = api_key_env_var.strip()
    else:
        payload["api_key_env_var"] = None

    if include_has_api_key:
        payload["has_api_key"] = has_api_key_override if has_api_key_override is not None else bool(normalized.get("api_key_encrypted"))

    if normalize_role(role) == ModelRole.CRITIC.value:
        payload["supports_json_schema_output"] = bool(normalized.get("supports_json_schema_output")) if "supports_json_schema_output" in normalized else True

    custom_params = normalized.get("custom_params")
    if isinstance(custom_params, dict) and custom_params:
        payload["custom_params"] = dict(custom_params)

    return payload


def model_config_signature(config: dict[str, Any]) -> tuple[Any, ...]:
    extra_config = config.get("extra_config") or {}
    custom_params = extra_config.get("custom_params")
    custom_params_tuple = tuple(sorted(custom_params.items())) if isinstance(custom_params, dict) else None
    return (
        config.get("provider"),
        config.get("base_url"),
        config.get("model_name"),
        None if config.get("temperature") is None else float(config.get("temperature")),
        None if config.get("max_tokens") is None else int(config.get("max_tokens")),
        extra_config.get("api_key_env_var"),
        extra_config.get("api_key_encrypted"),
        bool(extra_config.get("supports_json_schema_output")),
        custom_params_tuple,
    )


def merge_extra_config_for_storage(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
    *,
    role: str | ModelRole | None,
    scope_label: str,
) -> dict[str, Any]:
    current = dict(existing or {})
    updates = dict(incoming or {})

    api_key_env_var = updates.get("api_key_env_var")
    if isinstance(api_key_env_var, str) and api_key_env_var.strip():
        current["api_key_env_var"] = api_key_env_var.strip()
    else:
        current.pop("api_key_env_var", None)

    if updates.get("clear_api_key"):
        current.pop("api_key_encrypted", None)
    else:
        api_key = updates.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            if not has_configured_encryption_key():
                raise ValueError(f"未配置 NOVEL_AI_MODEL_SECRET_ENCRYPTION_KEY，不能直接保存{scope_label} API Key")
            current["api_key_encrypted"] = encrypt_model_api_key(api_key)

    current.pop("api_key", None)
    current.pop("clear_api_key", None)
    current.pop("has_api_key", None)
    current.pop("can_save_api_key", None)
    current.pop("can_save_project_api_key", None)
    if isinstance(updates.get("supports_json_schema_output"), bool):
        if updates["supports_json_schema_output"]:
            current["supports_json_schema_output"] = True
        else:
            current["supports_json_schema_output"] = False
    elif should_enable_json_schema_by_default(role, current):
        current["supports_json_schema_output"] = True

    incoming_custom = updates.get("custom_params")
    if isinstance(incoming_custom, dict):
        cleaned = {k.strip(): v for k, v in incoming_custom.items() if isinstance(k, str) and k.strip() and v is not None}
        if cleaned:
            current["custom_params"] = cleaned
        else:
            current.pop("custom_params", None)
    elif incoming_custom is None and "custom_params" in updates:
        current.pop("custom_params", None)

    return current
