from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TypeAlias, Union

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = Union[JSONScalar, dict[str, 'JSONValue'], list['JSONValue']]


class BaseLLMClient(ABC):
    @abstractmethod
    async def generate(self, system_prompt: str, user_message: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def stream_generate(self, system_prompt: str, user_message: str) -> AsyncIterator[str]:
        raise NotImplementedError

    @abstractmethod
    async def generate_json(
        self,
        system_prompt: str,
        user_message: str,
        *,
        response_schema: dict[str, JSONValue] | None = None,
        schema_name: str = "response",
    ) -> JSONValue:
        raise NotImplementedError

    async def generate_text_fallback(self, system_prompt: str, user_message: str) -> str:
        return await self.generate(system_prompt, user_message)

    def supports_stream_text_fallback(self) -> bool:
        return False

    def prefers_non_stream_writer_generation(self) -> bool:
        return False
