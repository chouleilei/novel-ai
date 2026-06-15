from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.services.system_runtime_settings_service import SystemRuntimeSettingsService


RUNTIME_PAYLOAD = {
    "review_policy": {
        "overall_score_threshold": 7.5,
        "outline_score_threshold": 7.0,
        "instruction_score_threshold": 6.5,
    },
    "memory_policy": {
        "auto_apply_confidence_threshold": 0.82,
    },
    "context_budget": {
        "writer_target_input_tokens": 12000,
        "writer_hard_limit_tokens": 18000,
        "critic_target_input_tokens": 8000,
        "critic_hard_limit_tokens": 12000,
    },
    "execution": {
        "llm_stage_timeout_seconds": 95.0,
    },
}


@pytest.mark.asyncio
async def test_get_runtime_settings_falls_back_to_env_defaults_when_missing():
    session = SimpleNamespace(get=AsyncMock(return_value=None))
    service = SystemRuntimeSettingsService(session)  # type: ignore[arg-type]

    settings = await service.get_runtime_settings()

    assert settings.id == 1
    assert float(settings.review_overall_score_threshold) == 8.0
    assert float(settings.review_outline_score_threshold) == 8.0
    assert float(settings.review_instruction_score_threshold) == 8.0
    assert float(settings.memory_auto_apply_confidence_threshold) == 0.75
    assert settings.writer_target_input_tokens == 64000
    assert settings.writer_hard_limit_tokens == 96000
    assert settings.critic_target_input_tokens == 32000
    assert settings.critic_hard_limit_tokens == 48000
    assert float(settings.llm_stage_timeout_seconds) == 180.0


@pytest.mark.asyncio
async def test_save_runtime_settings_creates_singleton_row_when_missing():
    session = SimpleNamespace(get=AsyncMock(return_value=None), add=Mock(), flush=AsyncMock())
    service = SystemRuntimeSettingsService(session)  # type: ignore[arg-type]

    saved = await service.save_runtime_settings(
        review_overall_score_threshold=7.5,
        review_outline_score_threshold=7.0,
        review_instruction_score_threshold=6.5,
        memory_auto_apply_confidence_threshold=0.82,
        writer_target_input_tokens=12000,
        writer_hard_limit_tokens=18000,
        critic_target_input_tokens=8000,
        critic_hard_limit_tokens=12000,
        llm_stage_timeout_seconds=95.0,
    )

    assert saved.id == 1
    assert float(saved.review_overall_score_threshold) == 7.5
    assert float(saved.review_outline_score_threshold) == 7.0
    assert float(saved.review_instruction_score_threshold) == 6.5
    assert float(saved.memory_auto_apply_confidence_threshold) == 0.82
    assert saved.writer_target_input_tokens == 12000
    assert saved.writer_hard_limit_tokens == 18000
    assert saved.critic_target_input_tokens == 8000
    assert saved.critic_hard_limit_tokens == 12000
    assert float(saved.llm_stage_timeout_seconds) == 95.0
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


def test_get_runtime_snapshot_from_payload_flattens_nested_groups():
    service = SystemRuntimeSettingsService(SimpleNamespace())  # type: ignore[arg-type]

    snapshot = service.get_runtime_snapshot_from_payload(RUNTIME_PAYLOAD)

    assert snapshot == {
        "review_overall_score_threshold": 7.5,
        "review_outline_score_threshold": 7.0,
        "review_instruction_score_threshold": 6.5,
        "memory_auto_apply_confidence_threshold": 0.82,
        "writer_target_input_tokens": 12000,
        "writer_hard_limit_tokens": 18000,
        "critic_target_input_tokens": 8000,
        "critic_hard_limit_tokens": 12000,
        "llm_stage_timeout_seconds": 95.0,
    }


def test_build_runtime_snapshot_uses_env_defaults_for_bootstrap():
    service = SystemRuntimeSettingsService(SimpleNamespace())  # type: ignore[arg-type]

    snapshot = service.build_runtime_snapshot()

    assert snapshot == {
        "review_overall_score_threshold": 8.0,
        "review_outline_score_threshold": 8.0,
        "review_instruction_score_threshold": 8.0,
        "memory_auto_apply_confidence_threshold": 0.75,
        "writer_target_input_tokens": 64000,
        "writer_hard_limit_tokens": 96000,
        "critic_target_input_tokens": 32000,
        "critic_hard_limit_tokens": 48000,
        "llm_stage_timeout_seconds": 180.0,
    }
