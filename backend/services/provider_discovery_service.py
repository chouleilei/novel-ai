"""Provider discovery service for fetching available models from LLM providers."""

import asyncio
from typing import Any

import httpx


class ProviderDiscoveryError(Exception):
    """Raised when provider discovery fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class ProviderDiscoveryService:
    """Service for discovering available models from LLM providers."""

    async def test_connection(
        self,
        provider: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Test connection to a provider.

        Args:
            provider: Provider type ('openai_compatible' or 'mock')
            base_url: Base URL for the provider API
            api_key: API key for authentication (optional)
            timeout: Request timeout in seconds

        Returns:
            Dict with connection status and metadata

        Raises:
            ProviderDiscoveryError: If connection fails
        """
        if provider == "mock":
            return {
                "success": True,
                "message": "Mock provider always available",
                "provider": provider,
            }

        if provider == "openai_compatible":
            return await self._test_openai_connection(base_url, api_key, timeout)

        raise ProviderDiscoveryError(f"Unsupported provider: {provider}")

    async def fetch_available_models(
        self,
        provider: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        """Fetch available models from a provider.

        Args:
            provider: Provider type ('openai_compatible' or 'mock')
            base_url: Base URL for the provider API
            api_key: API key for authentication (optional)
            timeout: Request timeout in seconds

        Returns:
            List of model info dicts with standardized fields:
            - model_name: str (the identifier to use)
            - display_name: str | None (human readable name)
            - provider_model_id: str | None (original provider ID)
            - owned_by: str | None (organization/owner)
            - raw_payload: dict | None (full provider response)

        Raises:
            ProviderDiscoveryError: If fetching fails
        """
        if provider == "mock":
            return [
                {
                    "model_name": "mock-model",
                    "display_name": "Mock Model",
                    "provider_model_id": "mock-model",
                    "owned_by": "local",
                    "raw_payload": {"id": "mock-model", "object": "model"},
                }
            ]

        if provider == "openai_compatible":
            return await self._fetch_openai_models(base_url, api_key, timeout)

        raise ProviderDiscoveryError(f"Unsupported provider: {provider}")

    async def discover_channel(
        self,
        provider: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Complete discovery: test connection and fetch models.

        Args:
            provider: Provider type
            base_url: Base URL for the provider API
            api_key: API key for authentication (optional)
            timeout: Request timeout in seconds

        Returns:
            Dict with:
            - success: bool
            - message: str
            - connection_status: dict
            - models: list of model info dicts
            - default_model_name: str | None (suggested default)
        """
        # First test connection
        try:
            connection_result = await self.test_connection(
                provider, base_url, api_key, timeout
            )
        except ProviderDiscoveryError as e:
            return {
                "success": False,
                "message": f"Connection failed: {e!s}",
                "connection_status": {"connected": False, "error": str(e), "details": e.details},
                "models": [],
                "default_model_name": None,
            }

        # Then fetch models
        try:
            models = await self.fetch_available_models(
                provider, base_url, api_key, timeout
            )
        except ProviderDiscoveryError as e:
            return {
                "success": False,
                "message": f"Connected but failed to fetch models: {e!s}",
                "connection_status": {**connection_result, "connected": True},
                "models": [],
                "default_model_name": None,
            }

        # Determine default model (first one or a common default)
        default_model_name = None
        if models:
            # Prefer common defaults if available
            common_defaults = ["gpt-4o", "gpt-4", "claude-3-5-sonnet", "deepseek-chat"]
            for default in common_defaults:
                if any(m["model_name"] == default for m in models):
                    default_model_name = default
                    break
            # Otherwise use first model
            if default_model_name is None:
                default_model_name = models[0]["model_name"]

        return {
            "success": True,
            "message": f"Successfully discovered {len(models)} models",
            "connection_status": {**connection_result, "connected": True},
            "models": models,
            "default_model_name": default_model_name,
        }

    async def _test_openai_connection(
        self,
        base_url: str,
        api_key: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        """Test connection to OpenAI-compatible API."""
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Normalize base URL
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        models_url = f"{base_url}/models"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(models_url, headers=headers)
                response.raise_for_status()
                data = response.json()

                if "data" not in data:
                    raise ProviderDiscoveryError(
                        "Invalid response format: missing 'data' field",
                        {"response_preview": str(data)[:500]},
                    )

                return {
                    "success": True,
                    "message": "Connection successful",
                    "provider": "openai_compatible",
                    "models_count": len(data.get("data", [])),
                }

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}: {e.response.reason_phrase}"
            try:
                error_data = e.response.json()
                if "error" in error_data:
                    error_msg = f"{error_msg} - {error_data['error']}"
            except Exception:
                pass
            raise ProviderDiscoveryError(
                error_msg,
                {"status_code": e.response.status_code},
            ) from e

        except httpx.ConnectError as e:
            raise ProviderDiscoveryError(
                f"Cannot connect to {models_url}: {e!s}",
                {"url": models_url},
            ) from e

        except httpx.TimeoutException as e:
            raise ProviderDiscoveryError(
                f"Connection timed out after {timeout}s",
                {"timeout": timeout},
            ) from e

        except Exception as e:
            raise ProviderDiscoveryError(f"Connection failed: {e!s}") from e

    async def _fetch_openai_models(
        self,
        base_url: str,
        api_key: str | None,
        timeout: float,
    ) -> list[dict[str, Any]]:
        """Fetch models from OpenAI-compatible API."""
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Normalize base URL
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        models_url = f"{base_url}/models"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(models_url, headers=headers)
                response.raise_for_status()
                data = response.json()

                if "data" not in data:
                    raise ProviderDiscoveryError(
                        "Invalid response format: missing 'data' field",
                        {"response_preview": str(data)[:500]},
                    )

                models = []
                for model_data in data.get("data", []):
                    model_id = model_data.get("id", "")
                    if not model_id:
                        continue

                    models.append({
                        "model_name": model_id,
                        "display_name": model_id.replace("-", " ").title(),
                        "provider_model_id": model_id,
                        "owned_by": model_data.get("owned_by") or model_data.get("owned_by", "unknown"),
                        "raw_payload": model_data,
                    })

                return models

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}: {e.response.reason_phrase}"
            try:
                error_data = e.response.json()
                if "error" in error_data:
                    error_msg = f"{error_msg} - {error_data['error']}"
            except Exception:
                pass
            raise ProviderDiscoveryError(
                error_msg,
                {"status_code": e.response.status_code},
            ) from e

        except httpx.ConnectError as e:
            raise ProviderDiscoveryError(
                f"Cannot connect to {models_url}: {e!s}",
                {"url": models_url},
            ) from e

        except httpx.TimeoutException as e:
            raise ProviderDiscoveryError(
                f"Request timed out after {timeout}s",
                {"timeout": timeout},
            ) from e

        except Exception as e:
            raise ProviderDiscoveryError(f"Failed to fetch models: {e!s}") from e
