import asyncio
import json
import logging
from collections.abc import AsyncIterator
from collections.abc import Mapping
from typing import Any

import httpx

from backend.config import get_settings
from backend.llm.base import BaseLLMClient, JSONValue


logger = logging.getLogger(__name__)
_UNSET = object()


class LLMJSONDecodeError(ValueError):
    def __init__(self, message: str, *, raw_preview: str) -> None:
        super().__init__(message)
        self.raw_preview = raw_preview


class LLMEmptyResponseError(ValueError):
    def __init__(self, message: str, *, raw_preview: str) -> None:
        super().__init__(message)
        self.raw_preview = raw_preview


class OpenAICompatibleClient(BaseLLMClient):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        extra_config: Mapping[str, Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_config = dict(extra_config or {})
        settings = get_settings()
        self.connect_timeout_seconds = settings.llm_connect_timeout_seconds
        self.read_timeout_seconds = settings.llm_read_timeout_seconds
        self.write_timeout_seconds = settings.llm_write_timeout_seconds
        self.pool_timeout_seconds = settings.llm_pool_timeout_seconds
        self.request_timeout = self._build_timeout(read_timeout=self.read_timeout_seconds)
        self.max_retries = min(max(0, settings.llm_max_retries), 10)
        self.retry_backoff_seconds = max(0.0, settings.llm_retry_backoff_seconds)

    def _build_timeout(self, *, read_timeout: float | None) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=read_timeout,
            write=self.write_timeout_seconds,
            pool=self.pool_timeout_seconds,
        )

    @staticmethod
    def _scrub_base_url(url: str) -> str:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.hostname or url
        except Exception:
            return "***"

    def _build_payload(
        self,
        system_prompt: str,
        user_message: str,
        *,
        stream: bool = False,
        temperature: float | None | object = _UNSET,
        response_schema: dict[str, JSONValue] | None = None,
        schema_name: str = "response",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        resolved_temperature = self.temperature if temperature is _UNSET else temperature
        if resolved_temperature is not None:
            payload["temperature"] = resolved_temperature
        if stream:
            payload["stream"] = True
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            }
        custom_params = self.extra_config.get("custom_params")
        if isinstance(custom_params, dict):
            for k, v in custom_params.items():
                if isinstance(k, str) and k.strip() and v is not None and k not in payload:
                    payload[k] = v
            logger.debug("merged custom_params into payload: keys=%s", sorted(custom_params.keys()))
        return payload

    def _supports_json_schema_output(self) -> bool:
        return bool(self.extra_config.get("supports_json_schema_output"))

    def _should_retry_request_error(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in {401, 403}:
                return False
        return True

    async def _generate_with_request_options(
        self,
        system_prompt: str,
        user_message: str,
        *,
        timeout: httpx.Timeout | None,
        max_retries: int,
        retry_log_message: str,
        temperature: float | None | object = _UNSET,
        response_schema: dict[str, JSONValue] | None = None,
        schema_name: str = "response",
    ) -> str:
        payload = self._build_payload(
            system_prompt,
            user_message,
            temperature=temperature,
            response_schema=response_schema,
            schema_name=schema_name,
        )
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error: Exception | None = None
        for attempt_index in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                choices = data.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise KeyError("choices")
                return self._extract_choice_text(choices[0])
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                last_error = exc
                if attempt_index >= max_retries or not self._should_retry_request_error(exc):
                    break
                backoff_seconds = self.retry_backoff_seconds * (2**attempt_index)
                logger.warning(
                    retry_log_message,
                    extra={
                        "model": self.model,
                        "base_url_host": self._scrub_base_url(self.base_url),
                        "attempt": attempt_index + 1,
                        "max_retries": max_retries,
                        "backoff_seconds": backoff_seconds,
                        "error_type": type(exc).__name__,
                    },
                )
                await asyncio.sleep(backoff_seconds)
        assert last_error is not None
        raise last_error

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        *,
        response_schema: dict[str, JSONValue] | None = None,
        schema_name: str = "response",
    ) -> str:
        return await self._generate_with_request_options(
            system_prompt,
            user_message,
            timeout=self.request_timeout,
            max_retries=self.max_retries,
            retry_log_message="llm request failed, retrying",
            response_schema=response_schema,
            schema_name=schema_name,
        )

    async def generate_text_fallback(self, system_prompt: str, user_message: str) -> str:
        fallback_read_timeout = max(self.read_timeout_seconds, 180.0)
        return await self._generate_with_request_options(
            system_prompt,
            user_message,
            timeout=self._build_timeout(read_timeout=fallback_read_timeout),
            max_retries=1,
            retry_log_message="llm fallback request failed, retrying",
            temperature=None,
        )

    async def stream_generate(self, system_prompt: str, user_message: str) -> AsyncIterator[str]:
        payload = self._build_payload(system_prompt, user_message, stream=True)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        non_text_log_count = 0
        invalid_frame_log_count = 0
        text_chunk_count = 0
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    if not raw_line or not raw_line.startswith("data:"):
                        continue
                    data = raw_line[5:].lstrip()
                    if data == "[DONE]":
                        if text_chunk_count == 0:
                            logger.warning(
                                "llm stream completed without text chunks for model=%s base_url=%s",
                                self.model,
                                self.base_url,
                            )
                        break
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        if invalid_frame_log_count < 3:
                            logger.debug(
                                "llm stream received non-json frame for model=%s preview=%s",
                                self.model,
                                self._preview_text(data, limit=200),
                            )
                            invalid_frame_log_count += 1
                        continue
                    delta = self._extract_stream_delta(parsed)
                    if delta:
                        text_chunk_count += 1
                        yield delta
                        continue
                    if non_text_log_count < 6:
                        logger.debug(
                            "llm stream non-text frame model=%s summary=%s",
                            self.model,
                            json.dumps(self._summarize_stream_payload(parsed), ensure_ascii=False),
                        )
                        non_text_log_count += 1
                    if self._has_stream_activity(parsed):
                        yield ""

    def supports_stream_text_fallback(self) -> bool:
        return True

    def prefers_non_stream_writer_generation(self) -> bool:
        return True

    async def generate_json(
        self,
        system_prompt: str,
        user_message: str,
        *,
        response_schema: dict[str, JSONValue] | None = None,
        schema_name: str = "response",
    ) -> JSONValue:
        last_error: Exception | None = None
        if response_schema is not None and self._supports_json_schema_output():
            try:
                raw = await self._generate_with_request_options(
                    system_prompt,
                    user_message,
                    timeout=self.request_timeout,
                    max_retries=0,
                    retry_log_message="llm schema request failed, retrying",
                    response_schema=response_schema,
                    schema_name=schema_name,
                )
                return json.loads(self._extract_json_candidate(raw))
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError, LLMEmptyResponseError) as exc:
                logger.warning(
                    "llm schema json request failed, falling back to text json parsing",
                    extra={
                        "model": self.model,
                        "base_url_host": self._scrub_base_url(self.base_url),
                        "error_type": type(exc).__name__,
                        "schema_name": schema_name,
                    },
                )
                last_error = exc

        non_stream_parse_error: json.JSONDecodeError | None = None
        should_try_stream_fallback = False
        last_raw_preview = ""
        for attempt_index in range(self.max_retries + 1):
            try:
                raw = self._extract_json_candidate(await self.generate(system_prompt, user_message))
            except LLMEmptyResponseError as exc:
                last_error = exc
                should_try_stream_fallback = True
                break
            last_raw_preview = self._preview_text(raw)
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                non_stream_parse_error = exc
                last_error = exc
                if attempt_index >= self.max_retries:
                    break
                backoff_seconds = self.retry_backoff_seconds * (2**attempt_index)
                logger.warning(
                    "llm json parsing failed, retrying",
                    extra={
                        "model": self.model,
                        "base_url_host": self._scrub_base_url(self.base_url),
                        "attempt": attempt_index + 1,
                        "max_retries": self.max_retries,
                        "backoff_seconds": backoff_seconds,
                        "error_type": type(exc).__name__,
                    },
                )
                await asyncio.sleep(backoff_seconds)

        if should_try_stream_fallback and self.supports_stream_text_fallback():
            try:
                streamed_text = await self._collect_stream_text(system_prompt, user_message)
                raw = self._extract_json_candidate(streamed_text)
                last_raw_preview = self._preview_text(raw)
                return json.loads(raw)
            except (json.JSONDecodeError, LLMEmptyResponseError) as exc:
                last_error = exc
                logger.warning(
                    "llm stream json fallback failed",
                    extra={
                        "model": self.model,
                        "base_url_host": self._scrub_base_url(self.base_url),
                        "error_type": type(exc).__name__,
                        "schema_name": schema_name,
                    },
                )

        if isinstance(last_error, json.JSONDecodeError):
            raise LLMJSONDecodeError(str(last_error), raw_preview=last_raw_preview) from last_error
        if non_stream_parse_error is not None:
            raise LLMJSONDecodeError(str(non_stream_parse_error), raw_preview=last_raw_preview) from non_stream_parse_error
        assert last_error is not None
        raise last_error

    async def _collect_stream_text(self, system_prompt: str, user_message: str) -> str:
        parts: list[str] = []
        async for chunk in self.stream_generate(system_prompt, user_message):
            if chunk:
                parts.append(chunk)
        if parts:
            return "".join(parts)
        raise LLMEmptyResponseError(
            "llm stream response contained no usable assistant content",
            raw_preview="",
        )

    def _extract_json_candidate(self, raw: str) -> str:
        candidate = raw.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            candidate = "\n".join(line for line in lines if not line.startswith("```")).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate

        wrapped_candidate = self._extract_balanced_json_substring(candidate)
        if wrapped_candidate is not None:
            return wrapped_candidate
        return candidate

    def _extract_balanced_json_substring(self, raw: str) -> str | None:
        start_index = -1
        opening_char = ""
        closing_char = ""
        for index, char in enumerate(raw):
            if char == "{":
                start_index = index
                opening_char = "{"
                closing_char = "}"
                break
            if char == "[":
                start_index = index
                opening_char = "["
                closing_char = "]"
                break
        if start_index < 0:
            return None

        depth = 0
        in_string = False
        escape_next = False
        for index in range(start_index, len(raw)):
            char = raw[index]
            if in_string:
                if escape_next:
                    escape_next = False
                    continue
                if char == "\\":
                    escape_next = True
                    continue
                if char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char == opening_char:
                depth += 1
                continue
            if char == closing_char:
                depth -= 1
                if depth == 0:
                    return raw[start_index : index + 1].strip()
        return None

    def _extract_choice_text(self, choice: Any) -> str:
        if not isinstance(choice, dict):
            raise TypeError("choice must be dict")
        parsed_payload = self._extract_choice_structured_payload(choice)
        if parsed_payload is not _UNSET:
            return json.dumps(parsed_payload, ensure_ascii=False)
        for key in ("message", "delta"):
            container = choice.get(key)
            if isinstance(container, dict):
                text = self._extract_stream_text(container.get("content"))
                if text:
                    return text
        text = self._extract_stream_text(choice.get("text"))
        if text:
            return text
        raise LLMEmptyResponseError(
            "llm response contained no usable assistant content",
            raw_preview=self._preview_text(json.dumps(choice, ensure_ascii=False), limit=800),
        )

    def _extract_choice_structured_payload(self, choice: dict[str, Any]) -> JSONValue | object:
        for key in ("message", "delta"):
            container = choice.get(key)
            if not isinstance(container, dict):
                continue
            parsed_payload = self._extract_structured_payload_from_container(container)
            if parsed_payload is not _UNSET:
                return parsed_payload
        return self._extract_structured_payload_from_container(choice)

    def _extract_structured_payload_from_container(self, container: Mapping[str, Any]) -> JSONValue | object:
        parsed_payload = container.get("parsed", _UNSET)
        if parsed_payload is not _UNSET and parsed_payload is not None:
            return parsed_payload
        for key in ("json", "output", "data"):
            value = container.get(key, _UNSET)
            if value is not _UNSET and value is not None and self._looks_like_json_payload(value):
                return value
        return _UNSET

    def _looks_like_json_payload(self, value: Any) -> bool:
        if isinstance(value, (str, bool, int, float, list, dict)):
            return True
        return False

    def _has_stream_activity(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        if payload.get("usage"):
            return True
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return False
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return False
        if any(first_choice.get(key) is not None for key in ("delta", "message", "text", "finish_reason", "index", "logprobs")):
            return True
        return False

    def _summarize_stream_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"payload_type": type(payload).__name__}
        choices = payload.get("choices")
        first_choice = choices[0] if isinstance(choices, list) and choices else None
        summary: dict[str, Any] = {
            "top_level_keys": sorted(payload.keys()),
            "has_usage": bool(payload.get("usage")),
            "choice_count": len(choices) if isinstance(choices, list) else 0,
        }
        if isinstance(first_choice, dict):
            delta = first_choice.get("delta")
            message = first_choice.get("message")
            summary["choice_keys"] = sorted(first_choice.keys())
            summary["delta_keys"] = sorted(delta.keys()) if isinstance(delta, dict) else []
            summary["message_keys"] = sorted(message.keys()) if isinstance(message, dict) else []
            summary["finish_reason"] = first_choice.get("finish_reason")
            if isinstance(delta, dict):
                reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning.strip():
                    summary["reasoning_preview"] = self._preview_text(reasoning, limit=120)
            content = None
            if isinstance(delta, dict):
                content = delta.get("content")
            if content is None and isinstance(message, dict):
                content = message.get("content")
            if content is None:
                content = first_choice.get("text")
            summary["content_preview"] = self._summarize_stream_content(content)
        return summary

    def _summarize_stream_content(self, content: Any) -> Any:
        if isinstance(content, str):
            return self._preview_text(content, limit=120)
        if isinstance(content, dict):
            return {
                "type": "dict",
                "keys": sorted(content.keys()),
                "text_preview": self._preview_text(str(content.get("text") or content.get("content") or ""), limit=120),
            }
        if isinstance(content, list):
            items = []
            for item in content[:3]:
                if isinstance(item, dict):
                    items.append(
                        {
                            "type": item.get("type"),
                            "keys": sorted(item.keys()),
                            "text_preview": self._preview_text(str(item.get("text") or item.get("content") or ""), limit=80),
                        }
                    )
                else:
                    items.append({"type": type(item).__name__, "preview": self._preview_text(str(item), limit=80)})
            return {"type": "list", "items": items, "length": len(content)}

    def _extract_stream_delta(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return ""

        for key in ("delta", "message"):
            container = first_choice.get(key)
            if not isinstance(container, dict):
                continue
            text = self._extract_stream_text(container.get("content"))
            if text:
                return text

        return self._extract_stream_text(first_choice.get("text"))

    def _extract_stream_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            item_type = content.get("type")
            if isinstance(item_type, str) and any(token in item_type.lower() for token in ("reason", "think")):
                return ""
            return self._extract_text_value(content)
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item:
                    parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if isinstance(item_type, str) and any(token in item_type.lower() for token in ("reason", "think")):
                continue
            text = self._extract_text_value(item)
            if isinstance(text, str) and text:
                parts.append(text)
        return "".join(parts)

    def _extract_text_value(self, item: Mapping[str, Any]) -> str:
        for key in ("text", "content", "value"):
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
            if isinstance(candidate, Mapping):
                for nested_key in ("value", "text", "content"):
                    nested_value = candidate.get(nested_key)
                    if isinstance(nested_value, str) and nested_value:
                        return nested_value
        return ""

    def _preview_text(self, content: str, limit: int = 400) -> str:
        normalized = " ".join(content.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 1]}…"
