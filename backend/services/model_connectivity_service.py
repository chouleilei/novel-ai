import asyncio
import json
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ModelRole, ProjectModelConfig, SystemModelConfig
from backend.llm.factory import build_llm_client
from backend.llm.json_schemas import CRITIC_REVIEW_SCHEMA
from backend.model_config_utils import normalize_role
from backend.prompts.critic import CRITIC_SYSTEM_PROMPT
from backend.prompts.memory import CONTINUITY_CHECK_PROMPT
from backend.services.runtime_service import RuntimeService
from backend.services.system_settings_service import SystemSettingsService


class ModelConnectivityValidationError(ValueError):
    def __init__(self, message: str, *, raw_preview: str | None = None) -> None:
        super().__init__(message)
        self.raw_preview = raw_preview


class ModelConnectivityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runtime = RuntimeService(session)
        self.system_settings = SystemSettingsService(session)
        self.test_timeout_seconds = 20.0

    async def test_model(self, project_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        role = normalize_role(payload.get("role"))
        config = await self._build_effective_config(project_id, payload, role=role)
        api_key_source = await self._detect_api_key_source(config)

        started_at = time.perf_counter()
        try:
            api_key, env_key = await self.runtime.resolve_api_key(config)
            client = build_llm_client(config, api_key=api_key, api_key_name=env_key)
            preview = await asyncio.wait_for(self._run_smoke_test(role, client), timeout=self.test_timeout_seconds)
        except asyncio.TimeoutError:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            return self._build_result(
                config=config,
                success=False,
                message=f"连通性测试超时：{self.test_timeout_seconds:.0f}s 内未完成响应。",
                latency_ms=latency_ms,
                api_key_source=api_key_source,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            return self._build_result(
                config=config,
                success=False,
                message=f"连通性测试失败：{exc}",
                latency_ms=latency_ms,
                api_key_source=api_key_source,
                raw_preview=self._extract_raw_preview(exc),
            )

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        return self._build_result(
            config=config,
            success=True,
            message="模型接口可用，已完成轻量冒烟测试。",
            latency_ms=latency_ms,
            preview=preview,
            api_key_source=api_key_source,
        )

    async def test_system_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        role = normalize_role(payload.get("role"))
        config = await self._build_effective_system_config(payload, role=role)
        api_key_source = await self._detect_api_key_source(config)

        started_at = time.perf_counter()
        try:
            api_key, env_key = await self.runtime.resolve_api_key(config)
            client = build_llm_client(config, api_key=api_key, api_key_name=env_key)
            preview = await asyncio.wait_for(self._run_smoke_test(role, client), timeout=self.test_timeout_seconds)
        except asyncio.TimeoutError:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            return self._build_result(
                config=config,
                success=False,
                message=f"连通性测试超时：{self.test_timeout_seconds:.0f}s 内未完成响应。",
                latency_ms=latency_ms,
                api_key_source=api_key_source,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            return self._build_result(
                config=config,
                success=False,
                message=f"连通性测试失败：{exc}",
                latency_ms=latency_ms,
                api_key_source=api_key_source,
                raw_preview=self._extract_raw_preview(exc),
            )

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        return self._build_result(
            config=config,
            success=True,
            message="模型接口可用，已完成轻量冒烟测试。",
            latency_ms=latency_ms,
            preview=preview,
            api_key_source=api_key_source,
        )

    async def _build_effective_config(
        self,
        project_id: uuid.UUID,
        payload: dict[str, Any],
        *,
        role: str,
    ) -> ProjectModelConfig:
        existing = await self._get_existing_config(project_id, role)
        channel_id = payload.get("channel_id") or (existing.channel_id if existing is not None else None)
        merged_extra_config = self._merge_extra_config(
            existing.extra_config if existing is not None else None,
            payload.get("extra_config"),
        )

        return ProjectModelConfig(
            project_id=project_id,
            role=role,
            channel_id=self._normalize_channel_id(channel_id),
            provider=str(payload.get("provider") or ""),
            base_url=str(payload.get("base_url") or ""),
            model_name=str(payload.get("model_name") or ""),
            temperature=payload.get("temperature"),
            max_tokens=payload.get("max_tokens"),
            extra_config=merged_extra_config,
        )

    async def _build_effective_system_config(
        self,
        payload: dict[str, Any],
        *,
        role: str,
    ) -> SystemModelConfig:
        existing = await self._get_existing_system_config(role)
        channel_id = payload.get("channel_id") or (existing.channel_id if existing is not None else None)
        merged_extra_config = self._merge_extra_config(
            existing.extra_config if existing is not None else None,
            payload.get("extra_config"),
        )

        return SystemModelConfig(
            role=role,
            channel_id=self._normalize_channel_id(channel_id),
            provider=str(payload.get("provider") or ""),
            base_url=str(payload.get("base_url") or ""),
            model_name=str(payload.get("model_name") or ""),
            temperature=payload.get("temperature"),
            max_tokens=payload.get("max_tokens"),
            extra_config=merged_extra_config,
        )

    async def _get_existing_config(self, project_id: uuid.UUID, role: str) -> ProjectModelConfig | None:
        stmt = select(ProjectModelConfig).where(
            ProjectModelConfig.project_id == project_id,
            ProjectModelConfig.role == role,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_existing_system_config(self, role: str) -> SystemModelConfig | None:
        stmt = select(SystemModelConfig).where(SystemModelConfig.role == role)
        result = await self.session.execute(stmt)
        config = result.scalar_one_or_none()
        if config is not None:
            return config
        defaults = await self.system_settings.get_model_configs()
        return next((item for item in defaults if normalize_role(item.role) == role), None)

    async def _run_smoke_test(self, role: str, client) -> str:
        if role == ModelRole.WRITER.value:
            output = await client.generate(
                system_prompt="你是连通性测试助手。请只输出“连接成功”四个字，不要添加任何解释。",
                user_message="这是一次接口连通性测试，请直接返回结果。",
            )
            return self._normalize_preview(output)

        if role == ModelRole.CRITIC.value:
            payload = await client.generate_json(
                system_prompt=CRITIC_SYSTEM_PROMPT,
                user_message=(
                    "【global_prompt】\n保持叙事紧凑。\n\n"
                    "【章节大纲】\n主角在雨夜拿到关键线索，并决定继续追查。\n\n"
                    "【历史评审要点】\n无\n\n"
                    "【待评审正文】\n"
                    "雨夜里，主角在档案室门口拿到关键线索，并立刻决定继续追查。"
                ),
                response_schema=CRITIC_REVIEW_SCHEMA,
                schema_name="critic_review",
            )
            self._validate_critic_payload(payload)
            return self._normalize_preview(json.dumps(payload, ensure_ascii=False))

        if role == ModelRole.MEMORY.value:
            payload = await client.generate_json(
                system_prompt=CONTINUITY_CHECK_PROMPT,
                user_message=(
                    "【章节大纲】\n主角在雨夜拿到关键线索，并决定继续追查。\n\n"
                    "【前文摘要】\n主角上一章刚进入案发现场，准备寻找突破口。\n\n"
                    "【人物设定】\n主角谨慎但行动果断。\n\n"
                    "【世界观设定】\n案件发生在长期封锁的旧城区。"
                ),
            )
            self._ensure_keys(payload, ["issues", "continuity_notes", "suggested_focus"], role)
            return self._normalize_preview(json.dumps(payload, ensure_ascii=False))

        if role == ModelRole.PROMPT_BUILDER.value:
            payload = await client.generate_json(
                system_prompt="你是提示词构建模型连通性测试助手。请只返回 JSON：{\"system_prompt\":\"连接成功\"}，不要添加任何额外字段或解释。",
                user_message="这是一次 prompt_builder 连通性测试，请仅返回最小合法 JSON。",
            )
            self._ensure_keys(payload, ["system_prompt"], role)
            system_prompt = payload.get("system_prompt")
            if not isinstance(system_prompt, str) or not system_prompt.strip():
                self._raise_payload_validation_error(
                    "prompt_builder 返回的 system_prompt 不是有效字符串",
                    payload,
                )
            return self._normalize_preview(system_prompt)

        raise ValueError(f"不支持的模型角色: {role}")

    def _ensure_keys(self, payload: Any, required_keys: list[str], role: str) -> None:
        if not isinstance(payload, dict):
            self._raise_payload_validation_error(f"{role} 返回结果不是 JSON 对象", payload)
        missing = [key for key in required_keys if key not in payload]
        if missing:
            self._raise_payload_validation_error(
                f"{role} 返回缺少字段: {', '.join(missing)}",
                payload,
            )

    def _validate_critic_payload(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            self._raise_payload_validation_error("critic 返回结果不是 JSON 对象", payload)

        has_explicit_passed = isinstance(payload.get("passed"), bool)
        has_overall_score = self._coerce_score(payload.get("overall_score")) is not None
        has_dimensions = self._has_scored_dimensions(payload.get("dimensions")) or self._has_scored_dimensions(payload.get("scores"))
        has_flat_scores = any(
            self._coerce_score(payload.get(key)) is not None
            for key in (
                "outline_adherence",
                "instruction_adherence",
                "continuity_consistency",
                "character_consistency",
                "writing_quality",
            )
        )

        if not (has_explicit_passed or has_overall_score or has_dimensions or has_flat_scores):
            self._raise_payload_validation_error("critic 返回缺少可识别的评分字段", payload)

    def _has_scored_dimensions(self, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        return any(self._extract_dimension_score(item) is not None for item in value.values())

    def _extract_dimension_score(self, value: Any) -> float | None:
        if isinstance(value, dict):
            return self._coerce_score(value.get("score") or value.get("value"))
        return self._coerce_score(value)

    def _coerce_score(self, value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

    def _merge_extra_config(self, existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
        current = dict(existing or {})
        updates = dict(incoming or {})

        api_key_env_var = updates.get("api_key_env_var")
        if isinstance(api_key_env_var, str) and api_key_env_var.strip():
            current["api_key_env_var"] = api_key_env_var.strip()
        else:
            current.pop("api_key_env_var", None)

        if updates.get("clear_api_key"):
            current.pop("api_key_encrypted", None)

        api_key = updates.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            current["api_key"] = api_key.strip()
        else:
            current.pop("api_key", None)

        current.pop("has_api_key", None)
        current.pop("can_save_api_key", None)
        current.pop("can_save_project_api_key", None)
        current.pop("clear_api_key", None)
        if isinstance(updates.get("supports_json_schema_output"), bool):
            if updates["supports_json_schema_output"]:
                current["supports_json_schema_output"] = True
            else:
                current.pop("supports_json_schema_output", None)
        return current

    async def _detect_api_key_source(self, config: ProjectModelConfig | SystemModelConfig) -> str:
        extra_config = config.extra_config or {}
        if config.provider == "mock":
            return "not_required"
        if isinstance(extra_config.get("api_key"), str) and extra_config["api_key"].strip():
            return "inline"
        if isinstance(extra_config.get("api_key_encrypted"), str) and extra_config["api_key_encrypted"].strip():
            return "saved"
        if isinstance(extra_config.get("api_key_env_var"), str) and extra_config["api_key_env_var"].strip():
            return "env_var"
        channel_api_key, channel_env_key = await self.runtime._resolve_channel_api_key(config)
        if channel_api_key is not None:
            return "env_var" if channel_env_key else "saved"
        return "env_var"

    def _build_result(
        self,
        *,
        config: ProjectModelConfig | SystemModelConfig,
        success: bool,
        message: str,
        latency_ms: int,
        api_key_source: str,
        preview: str | None = None,
        raw_preview: str | None = None,
    ) -> dict[str, Any]:
        return {
            "success": success,
            "role": str(config.role),
            "provider": config.provider,
            "base_url": config.base_url,
            "model_name": config.model_name,
            "latency_ms": latency_ms,
            "message": message,
            "output_preview": preview,
            "raw_preview": raw_preview,
            "api_key_source": api_key_source,
        }

    def _normalize_preview(self, content: Any, limit: int = 160) -> str:
        text = str(content).strip()
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1]}…"

    def _extract_raw_preview(self, exc: Exception) -> str | None:
        raw_preview = getattr(exc, "raw_preview", None)
        if isinstance(raw_preview, str) and raw_preview.strip():
            return raw_preview.strip()
        return None

    def _raise_payload_validation_error(self, message: str, payload: Any) -> None:
        raise ModelConnectivityValidationError(message, raw_preview=self._serialize_payload_preview(payload))

    def _serialize_payload_preview(self, payload: Any) -> str | None:
        try:
            serialized = json.dumps(payload, ensure_ascii=False)
        except TypeError:
            serialized = str(payload)

        text = serialized.strip()
        if not text:
            return None
        return self._normalize_preview(text, limit=400)

    def _normalize_channel_id(self, value: Any) -> uuid.UUID | None:
        if isinstance(value, uuid.UUID):
            return value
        if isinstance(value, str) and value.strip():
            return uuid.UUID(value)
        return None
