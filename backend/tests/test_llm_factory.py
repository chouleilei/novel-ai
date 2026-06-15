import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from backend.llm.json_schemas import CRITIC_REVIEW_SCHEMA
from backend.llm.factory import MockLLMClient, build_llm_client
from backend.llm.openai_compatible import LLMEmptyResponseError, LLMJSONDecodeError, OpenAICompatibleClient


def test_build_llm_client_allows_mock_without_api_key():
    config = SimpleNamespace(
        provider="mock",
        role="writer",
        base_url="http://mock.local",
        model_name="mock-writer",
        temperature=0.8,
        max_tokens=4000,
    )

    client = build_llm_client(config)  # type: ignore[arg-type]

    assert isinstance(client, MockLLMClient)


def test_build_llm_client_reports_missing_named_api_key():
    config = SimpleNamespace(
        provider="openai_compatible",
        role="writer",
        base_url="https://api.example.com/v1",
        model_name="writer-model",
        temperature=0.8,
        max_tokens=4000,
    )

    with pytest.raises(ValueError, match="CUSTOM_WRITER_KEY"):
        build_llm_client(config, api_key=None, api_key_name="CUSTOM_WRITER_KEY")  # type: ignore[arg-type]


def test_build_llm_client_uses_configured_max_tokens_limit():
    config = SimpleNamespace(
        provider="openai_compatible",
        role="writer",
        base_url="https://api.example.com/v1",
        model_name="writer-model",
        temperature=0.8,
        max_tokens=4000,
    )

    client = build_llm_client(config, api_key="secret")  # type: ignore[arg-type]

    assert isinstance(client, OpenAICompatibleClient)
    assert client.max_tokens == 4000


def test_openai_compatible_payload_omits_temperature_when_unset():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=None,
        max_tokens=None,
    )

    payload = client._build_payload("system", "user")

    assert "temperature" not in payload


def test_openai_compatible_payload_omits_max_tokens_when_unset():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    payload = client._build_payload("system", "user")

    assert "max_tokens" not in payload


def test_openai_compatible_stream_delta_ignores_empty_choices():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    delta = client._extract_stream_delta({"choices": []})

    assert delta == ""


def test_openai_compatible_stream_delta_extracts_string_content():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    delta = client._extract_stream_delta({"choices": [{"delta": {"content": "第一段正文"}}]})

    assert delta == "第一段正文"


def test_openai_compatible_stream_delta_extracts_structured_content():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    delta = client._extract_stream_delta(
        {
            "choices": [
                {
                    "delta": {
                        "content": [
                            {"type": "text", "text": "第一句"},
                            {"type": "text", "text": "第二句"},
                        ]
                    }
                }
            ]
        }
    )

    assert delta == "第一句第二句"


def test_openai_compatible_stream_activity_detects_non_text_delta():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    active = client._has_stream_activity({"choices": [{"delta": {"role": "assistant"}}]})

    assert active is True


def test_openai_compatible_stream_summary_includes_delta_keys_and_reasoning_preview():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    summary = client._summarize_stream_payload(
        {
            "choices": [
                {
                    "delta": {
                        "reasoning_content": "这是推理片段",
                        "content": [{"type": "text", "text": "正文片段"}],
                    },
                    "finish_reason": None,
                }
            ],
            "usage": {"prompt_tokens": 1},
        }
    )

    assert summary["has_usage"] is True
    assert summary["delta_keys"] == ["content", "reasoning_content"]
    assert summary["reasoning_preview"] == "这是推理片段"
    assert summary["content_preview"]["type"] == "list"


@pytest.mark.asyncio
async def test_openai_compatible_generate_retries_with_exponential_backoff(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )
    client.max_retries = 2
    client.retry_backoff_seconds = 1.5

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    post = AsyncMock(side_effect=[httpx.ReadTimeout("timeout-1"), httpx.ReadTimeout("timeout-2"), response])

    class FakeClient:
        async def __aenter__(self):
            return SimpleNamespace(post=post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    sleep = AsyncMock()

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(asyncio, "sleep", sleep)

    result = await client.generate("system", "user")

    assert result == "ok"
    assert post.await_count == 3
    sleep.assert_any_await(1.5)
    sleep.assert_any_await(3.0)


@pytest.mark.asyncio
async def test_openai_compatible_generate_raises_after_retry_limit(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )
    client.max_retries = 1

    post = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))

    class FakeClient:
        async def __aenter__(self):
            return SimpleNamespace(post=post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with pytest.raises(httpx.ReadTimeout):
        await client.generate("system", "user")


@pytest.mark.asyncio
async def test_openai_compatible_generate_does_not_retry_401_or_403(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )
    client.max_retries = 5

    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(status_code=401, request=request)
    auth_error = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    post = AsyncMock(side_effect=auth_error)

    class FakeClient:
        async def __aenter__(self):
            return SimpleNamespace(post=post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    sleep = AsyncMock()

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.generate("system", "user")

    assert exc_info.value.response.status_code == 401
    assert post.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_retries_after_invalid_json(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    calls = iter(["not-json", json.dumps({"ok": True}, ensure_ascii=False)])

    async def fake_generate(system_prompt: str, user_message: str) -> str:
        return next(calls)

    monkeypatch.setattr(client, "generate", fake_generate)

    client.max_retries = 1
    client.retry_backoff_seconds = 0
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    raw = await client.generate_json("system", "user")

    assert raw == {"ok": True}


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_exposes_raw_preview_after_retry_limit(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    async def fake_generate(system_prompt: str, user_message: str) -> str:
        return "```json\n{\"ok\":\n```"

    monkeypatch.setattr(client, "generate", fake_generate)
    client.max_retries = 0

    with pytest.raises(LLMJSONDecodeError) as exc_info:
        await client.generate_json("system", "user")


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_can_return_top_level_list(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    async def fake_generate(system_prompt: str, user_message: str) -> str:
        return json.dumps([{"name": "沈夜"}], ensure_ascii=False)

    monkeypatch.setattr(client, "generate", fake_generate)

    raw = await client.generate_json("system", "user")

    assert raw == [{"name": "沈夜"}]


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_recovers_json_wrapped_in_plain_text(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    async def fake_generate(system_prompt: str, user_message: str) -> str:
        return "好的，以下是结果：\n{\"ok\": true, \"summary\": \"保留成功路径\"}\n请查收。"

    monkeypatch.setattr(client, "generate", fake_generate)

    raw = await client.generate_json("system", "user")

    assert raw == {"ok": True, "summary": "保留成功路径"}



def test_openai_compatible_payload_can_override_temperature_to_none():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=1.2,
        max_tokens=None,
    )

    payload = client._build_payload("system", "user", temperature=None)

    assert "temperature" not in payload


def test_openai_compatible_payload_includes_response_format_when_schema_requested():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
        extra_config={"supports_json_schema_output": True},
    )

    payload = client._build_payload(
        "system",
        "user",
        response_schema=CRITIC_REVIEW_SCHEMA,
        schema_name="critic_review",
    )

    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "critic_review"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["schema"] == CRITIC_REVIEW_SCHEMA


def test_critic_review_schema_declares_required_for_all_object_properties():
    schema = CRITIC_REVIEW_SCHEMA

    assert schema["required"] == [
        "overall_score",
        "passed",
        "dimensions",
        "blocking_issues",
        "uncovered_outline_points",
        "violated_instructions",
        "improvement_suggestions",
        "non_scoring_notes",
    ]

    dimensions = schema["properties"]["dimensions"]
    assert dimensions["required"] == [
        "outline_adherence",
        "instruction_adherence",
        "continuity_consistency",
        "character_consistency",
        "writing_quality",
    ]

    for key in [
        "outline_adherence",
        "instruction_adherence",
        "continuity_consistency",
        "character_consistency",
        "writing_quality",
    ]:
        item = dimensions["properties"][key]
        assert item["required"] == ["score", "comment", "reason"]


@pytest.mark.asyncio
async def test_openai_compatible_generate_text_fallback_omits_temperature_and_retries_once(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=1.2,
        max_tokens=None,
    )
    client.read_timeout_seconds = 120.0
    client.retry_backoff_seconds = 0

    observed_timeouts: list[httpx.Timeout | None] = []
    observed_payloads: list[dict[str, object]] = []

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

    post = AsyncMock(side_effect=[httpx.ReadTimeout("timeout"), response])

    class FakeClient:
        def __init__(self, timeout):
            observed_timeouts.append(timeout)

        async def __aenter__(self):
            return SimpleNamespace(post=self._post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def _post(self, url, headers, json):
            observed_payloads.append(json)
            return await post(url, headers, json)

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient(timeout))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    result = await client.generate_text_fallback("system", "user")

    assert result == "ok"
    assert post.await_count == 2
    assert observed_payloads
    assert all("temperature" not in payload for payload in observed_payloads)
    assert observed_timeouts
    assert all(timeout is not None and timeout.read == 180.0 for timeout in observed_timeouts)


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_uses_schema_path_when_enabled(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
        extra_config={"supports_json_schema_output": True},
    )

    observed_payloads: list[dict[str, object]] = []

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"overall_score": 8.8, "passed": True}, ensure_ascii=False)
                }
            }
        ]
    }

    post = AsyncMock(return_value=response)

    class FakeClient:
        async def __aenter__(self):
            return SimpleNamespace(post=self._post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def _post(self, url, headers, json):
            observed_payloads.append(json)
            return await post(url, headers, json)

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient())

    result = await client.generate_json(
        "system",
        "user",
        response_schema=CRITIC_REVIEW_SCHEMA,
        schema_name="critic_review",
    )

    assert result == {"overall_score": 8.8, "passed": True}
    response_format = observed_payloads[0].get("response_format")
    assert isinstance(response_format, dict)
    json_schema = response_format.get("json_schema")
    assert isinstance(json_schema, dict)
    assert json_schema["name"] == "critic_review"


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_accepts_message_parsed_payload(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
        extra_config={"supports_json_schema_output": True},
    )

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "parsed": {"overall_score": 8.9, "passed": True},
                }
            }
        ]
    }

    post = AsyncMock(return_value=response)

    class FakeClient:
        async def __aenter__(self):
            return SimpleNamespace(post=post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient())

    result = await client.generate_json(
        "system",
        "user",
        response_schema=CRITIC_REVIEW_SCHEMA,
        schema_name="critic_review",
    )

    assert result == {"overall_score": 8.9, "passed": True}


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_ignores_null_parsed_and_uses_content(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
        extra_config={"supports_json_schema_output": True},
    )

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"overall_score": 8.4, "passed": False}, ensure_ascii=False),
                    "parsed": None,
                }
            }
        ]
    }

    post = AsyncMock(return_value=response)

    class FakeClient:
        async def __aenter__(self):
            return SimpleNamespace(post=post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient())

    result = await client.generate_json(
        "system",
        "user",
        response_schema=CRITIC_REVIEW_SCHEMA,
        schema_name="critic_review",
    )

    assert result == {"overall_score": 8.4, "passed": False}


def test_openai_compatible_extract_stream_text_supports_nested_value_payloads():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="writer-model",
        temperature=0.8,
        max_tokens=None,
    )

    content = [
        {"type": "output_text", "text": {"value": "第一句"}},
        {"type": "output_text", "value": "第二句"},
    ]

    assert client._extract_stream_text(content) == "第一句第二句"


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_falls_back_when_schema_request_fails(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
        extra_config={"supports_json_schema_output": True},
    )

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"ok": True}, ensure_ascii=False)}}]
    }

    post = AsyncMock(side_effect=[httpx.HTTPStatusError("bad request", request=Mock(), response=Mock()), response])

    class FakeClient:
        async def __aenter__(self):
            return SimpleNamespace(post=post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient())

    result = await client.generate_json(
        "system",
        "user",
        response_schema=CRITIC_REVIEW_SCHEMA,
        schema_name="critic_review",
    )

    assert result == {"ok": True}
    assert post.await_count == 2


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_falls_back_immediately_when_schema_response_is_empty(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
        extra_config={"supports_json_schema_output": True},
    )
    client.max_retries = 3

    observed_payloads: list[dict[str, object]] = []

    schema_response = Mock()
    schema_response.raise_for_status.return_value = None
    schema_response.json.return_value = {
        "choices": [
            {
                "message": {"content": None, "reasoning_content": None, "tool_calls": None},
                "finish_reason": "stop",
            }
        ]
    }

    fallback_response = Mock()
    fallback_response.raise_for_status.return_value = None
    fallback_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"ok": True}, ensure_ascii=False)}}]
    }

    post = AsyncMock(side_effect=[schema_response, fallback_response])

    class FakeClient:
        async def __aenter__(self):
            return SimpleNamespace(post=self._post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def _post(self, url, headers, json):
            observed_payloads.append(json)
            return await post(url, headers, json)

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient())

    result = await client.generate_json(
        "system",
        "user",
        response_schema=CRITIC_REVIEW_SCHEMA,
        schema_name="critic_review",
    )

    assert result == {"ok": True}
    assert post.await_count == 2
    first_payload = observed_payloads[0]
    second_payload = observed_payloads[1]
    assert "response_format" in first_payload
    assert "response_format" not in second_payload


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_uses_stream_fallback_after_empty_non_stream(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
    )

    async def fake_generate(system_prompt: str, user_message: str) -> str:
        raise LLMEmptyResponseError("llm response contained no usable assistant content", raw_preview='{"content":null}')

    async def fake_stream_generate(system_prompt: str, user_message: str):
        yield '{"overall_score": 8.7, '
        yield '"passed": true}'

    monkeypatch.setattr(client, "generate", fake_generate)
    monkeypatch.setattr(client, "stream_generate", fake_stream_generate)

    result = await client.generate_json("system", "user")

    assert result == {"overall_score": 8.7, "passed": True}


@pytest.mark.asyncio
async def test_openai_compatible_generate_json_uses_stream_fallback_after_schema_and_non_stream_fail(monkeypatch):
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
        extra_config={"supports_json_schema_output": True},
    )

    observed_payloads: list[dict[str, object]] = []

    schema_response = Mock()
    schema_response.raise_for_status.return_value = None
    schema_response.json.return_value = {
        "choices": [
            {
                "message": {"content": None, "reasoning_content": None, "tool_calls": None},
                "finish_reason": "stop",
            }
        ]
    }

    fallback_response = Mock()
    fallback_response.raise_for_status.return_value = None
    fallback_response.json.return_value = {
        "choices": [
            {
                "message": {"content": None, "reasoning_content": None, "tool_calls": None},
                "finish_reason": "stop",
            }
        ]
    }

    post = AsyncMock(side_effect=[schema_response, fallback_response])

    class FakeClient:
        async def __aenter__(self):
            return SimpleNamespace(post=self._post)

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def _post(self, url, headers, json):
            observed_payloads.append(json)
            return await post(url, headers, json)

    async def fake_stream_generate(system_prompt: str, user_message: str):
        yield '{"overall_score": 8.5, '
        yield '"passed": false}'

    monkeypatch.setattr("backend.llm.openai_compatible.httpx.AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(client, "stream_generate", fake_stream_generate)

    result = await client.generate_json(
        "system",
        "user",
        response_schema=CRITIC_REVIEW_SCHEMA,
        schema_name="critic_review",
    )

    assert result == {"overall_score": 8.5, "passed": False}
    assert post.await_count == 2
    assert "response_format" in observed_payloads[0]
    assert "response_format" not in observed_payloads[1]


@pytest.mark.asyncio
async def test_openai_compatible_collect_stream_text_raises_for_empty_stream():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
    )

    async def fake_stream_generate(system_prompt: str, user_message: str):
        if False:
            yield ""

    client.stream_generate = fake_stream_generate  # type: ignore[method-assign]

    with pytest.raises(LLMEmptyResponseError, match="stream response contained no usable assistant content"):
        await client._collect_stream_text("system", "user")


def test_openai_compatible_extract_choice_text_raises_empty_response_error_for_null_content():
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="critic-model",
        temperature=None,
        max_tokens=None,
    )

    with pytest.raises(LLMEmptyResponseError, match="no usable assistant content"):
        client._extract_choice_text(
            {
                "message": {"role": "assistant", "content": None, "reasoning_content": None, "tool_calls": None},
                "finish_reason": "stop",
            }
        )
