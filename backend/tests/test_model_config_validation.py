import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.projects import ModelConfigItem, validate_model_configs_payload
from backend.model_config_utils import merge_extra_config_for_storage


def make_model(role: str, **overrides) -> ModelConfigItem:
    payload = {
        "role": role,
        "provider": "mock",
        "base_url": "http://mock.local",
        "model_name": f"mock-{role}",
        "temperature": None,
        "max_tokens": None,
    }
    payload.update(overrides)
    return ModelConfigItem(**payload)


def test_validate_model_configs_requires_writer_critic_memory():
    payload = [
        make_model("critic"),
        make_model("memory"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_model_configs_payload(payload)

    assert exc_info.value.status_code == 400
    assert "writer" in exc_info.value.detail


def test_validate_model_configs_rejects_duplicate_roles():
    payload = [
        make_model("writer"),
        make_model("critic"),
        make_model("memory"),
        make_model("writer", model_name="another-writer"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_model_configs_payload(payload)

    assert exc_info.value.status_code == 400
    assert "writer" in exc_info.value.detail


def test_model_config_normalizes_api_key_env_var():
    config = make_model(
        "writer",
        provider="openai_compatible",
        extra_config={"api_key_env_var": "  CUSTOM_WRITER_KEY  "},
    )

    assert config.extra_config.api_key_env_var == "CUSTOM_WRITER_KEY"


def test_model_config_rejects_invalid_api_key_env_var():
    with pytest.raises(ValidationError, match="API Key Env Var"):
        make_model(
            "writer",
            provider="openai_compatible",
            extra_config={"api_key_env_var": "writer-key"},
        )


def test_model_config_normalizes_api_key():
    config = make_model(
        "writer",
        provider="openai_compatible",
        extra_config={"api_key": "  sk-test-secret  "},
    )

    assert config.extra_config.api_key == "sk-test-secret"


def test_model_config_normalizes_blank_api_key_to_none():
    config = make_model(
        "writer",
        provider="openai_compatible",
        extra_config={"api_key": "   ", "clear_api_key": True},
    )

    assert config.extra_config.api_key is None
    assert config.extra_config.clear_api_key is True


def test_model_config_allows_null_temperature_and_max_tokens():
    config = make_model("writer", temperature=None, max_tokens=None)

    assert config.temperature is None
    assert config.max_tokens is None


def test_merge_extra_config_defaults_critic_schema_flag_when_missing_from_payload():
    merged = merge_extra_config_for_storage(
        existing={"api_key_env_var": "CRITIC_API_KEY"},
        incoming={"api_key_env_var": "CRITIC_API_KEY"},
        role="critic",
        scope_label="项目级",
    )

    assert merged["supports_json_schema_output"] is True


def test_merge_extra_config_does_not_enable_writer_schema_flag_by_default():
    merged = merge_extra_config_for_storage(
        existing={"api_key_env_var": "WRITER_API_KEY"},
        incoming={"api_key_env_var": "WRITER_API_KEY"},
        role="writer",
        scope_label="项目级",
    )

    assert "supports_json_schema_output" not in merged


def test_merge_extra_config_persists_explicit_false_for_critic_schema_flag():
    merged = merge_extra_config_for_storage(
        existing={"api_key_env_var": "CRITIC_API_KEY", "supports_json_schema_output": True},
        incoming={"api_key_env_var": "CRITIC_API_KEY", "supports_json_schema_output": False},
        role="critic",
        scope_label="项目级",
    )

    assert merged["supports_json_schema_output"] is False


def test_model_config_can_omit_critic_schema_flag() -> None:
    config = make_model(
        "critic",
        provider="openai_compatible",
        extra_config={"api_key_env_var": "CRITIC_API_KEY"},
    )

    assert config.extra_config.supports_json_schema_output is None
