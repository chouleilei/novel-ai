from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.llm.json_schemas import CRITIC_REVIEW_SCHEMA
from backend.services.model_connectivity_service import ModelConnectivityService


@pytest.mark.asyncio
async def test_prompt_builder_validation_failure_exposes_raw_preview():
    service = ModelConnectivityService(session=SimpleNamespace())  # type: ignore[arg-type]
    client = SimpleNamespace(generate_json=AsyncMock(return_value={"draft_prompt": "not-used"}))

    with pytest.raises(ValueError, match="prompt_builder 返回缺少字段: system_prompt") as exc_info:
        await service._run_smoke_test("prompt_builder", client)

    assert getattr(exc_info.value, "raw_preview", None) == '{"draft_prompt": "not-used"}'


@pytest.mark.asyncio
async def test_critic_smoke_test_passes_schema_arguments() -> None:
    service = ModelConnectivityService(session=SimpleNamespace())  # type: ignore[arg-type]
    client = SimpleNamespace(
        generate_json=AsyncMock(
            return_value={
                "overall_score": 8.6,
                "passed": True,
                "dimensions": {"outline_adherence": {"score": 9}},
            }
        )
    )

    await service._run_smoke_test("critic", client)

    _, kwargs = client.generate_json.await_args
    assert kwargs["response_schema"] == CRITIC_REVIEW_SCHEMA
    assert kwargs["schema_name"] == "critic_review"


@pytest.mark.asyncio
async def test_detect_api_key_source_prefers_model_env_var_over_channel_secret() -> None:
    service = ModelConnectivityService(session=SimpleNamespace())  # type: ignore[arg-type]
    service.runtime._resolve_channel_api_key = AsyncMock(return_value=("channel-secret", None))
    config = SimpleNamespace(
        provider="openai_compatible",
        extra_config={"api_key_env_var": "WRITER_API_KEY"},
    )

    source = await service._detect_api_key_source(config)  # type: ignore[arg-type]

    assert source == "env_var"
