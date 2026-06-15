from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import SystemRuntimeSetting


class SystemRuntimeSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def get_runtime_settings(self) -> SystemRuntimeSetting:
        existing = await self.session.get(SystemRuntimeSetting, 1)
        if existing is not None:
            return existing
        return SystemRuntimeSetting(**self._defaults_from_env())

    async def get_runtime_snapshot(self) -> dict[str, Any]:
        runtime_settings = await self.get_runtime_settings()
        return self.build_runtime_snapshot(runtime_settings)

    async def save_runtime_settings(
        self,
        *,
        review_overall_score_threshold: float,
        review_outline_score_threshold: float,
        review_instruction_score_threshold: float,
        memory_auto_apply_confidence_threshold: float,
        writer_target_input_tokens: int,
        writer_hard_limit_tokens: int,
        critic_target_input_tokens: int,
        critic_hard_limit_tokens: int,
        llm_stage_timeout_seconds: float,
    ) -> SystemRuntimeSetting:
        current = await self.session.get(SystemRuntimeSetting, 1)
        if current is None:
            current = SystemRuntimeSetting(id=1)
            self.session.add(current)

        current.review_overall_score_threshold = review_overall_score_threshold
        current.review_outline_score_threshold = review_outline_score_threshold
        current.review_instruction_score_threshold = review_instruction_score_threshold
        current.memory_auto_apply_confidence_threshold = memory_auto_apply_confidence_threshold
        current.writer_target_input_tokens = writer_target_input_tokens
        current.writer_hard_limit_tokens = writer_hard_limit_tokens
        current.critic_target_input_tokens = critic_target_input_tokens
        current.critic_hard_limit_tokens = critic_hard_limit_tokens
        current.llm_stage_timeout_seconds = llm_stage_timeout_seconds
        await self.session.flush()
        return current

    async def get_settings_payload(self) -> dict[str, Any]:
        runtime_settings = await self.get_runtime_settings()
        return self.serialize_runtime_settings(runtime_settings)

    def get_runtime_snapshot_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        review_policy = payload["review_policy"]
        memory_policy = payload["memory_policy"]
        context_budget = payload["context_budget"]
        execution = payload["execution"]
        return {
            "review_overall_score_threshold": float(review_policy["overall_score_threshold"]),
            "review_outline_score_threshold": float(review_policy["outline_score_threshold"]),
            "review_instruction_score_threshold": float(review_policy["instruction_score_threshold"]),
            "memory_auto_apply_confidence_threshold": float(memory_policy["auto_apply_confidence_threshold"]),
            "writer_target_input_tokens": int(context_budget["writer_target_input_tokens"]),
            "writer_hard_limit_tokens": int(context_budget["writer_hard_limit_tokens"]),
            "critic_target_input_tokens": int(context_budget["critic_target_input_tokens"]),
            "critic_hard_limit_tokens": int(context_budget["critic_hard_limit_tokens"]),
            "llm_stage_timeout_seconds": float(execution["llm_stage_timeout_seconds"]),
        }

    def serialize_runtime_settings(self, runtime_settings: SystemRuntimeSetting) -> dict[str, Any]:
        return {
            "review_policy": {
                "overall_score_threshold": float(runtime_settings.review_overall_score_threshold),
                "outline_score_threshold": float(runtime_settings.review_outline_score_threshold),
                "instruction_score_threshold": float(runtime_settings.review_instruction_score_threshold),
            },
            "memory_policy": {
                "auto_apply_confidence_threshold": float(runtime_settings.memory_auto_apply_confidence_threshold),
            },
            "context_budget": {
                "writer_target_input_tokens": int(runtime_settings.writer_target_input_tokens),
                "writer_hard_limit_tokens": int(runtime_settings.writer_hard_limit_tokens),
                "critic_target_input_tokens": int(runtime_settings.critic_target_input_tokens),
                "critic_hard_limit_tokens": int(runtime_settings.critic_hard_limit_tokens),
            },
            "execution": {
                "llm_stage_timeout_seconds": float(runtime_settings.llm_stage_timeout_seconds),
            },
        }

    def build_runtime_snapshot(self, runtime_settings: SystemRuntimeSetting | None = None) -> dict[str, Any]:
        effective = runtime_settings or SystemRuntimeSetting(**self._defaults_from_env())
        return self.get_runtime_snapshot_from_payload(self.serialize_runtime_settings(effective))

    def _defaults_from_env(self) -> dict[str, Any]:
        return {
            "id": 1,
            "review_overall_score_threshold": self.settings.review_overall_score_threshold,
            "review_outline_score_threshold": self.settings.review_outline_score_threshold,
            "review_instruction_score_threshold": self.settings.review_instruction_score_threshold,
            "memory_auto_apply_confidence_threshold": self.settings.memory_auto_apply_confidence_threshold,
            "writer_target_input_tokens": self.settings.writer_target_input_tokens,
            "writer_hard_limit_tokens": self.settings.writer_hard_limit_tokens,
            "critic_target_input_tokens": self.settings.critic_target_input_tokens,
            "critic_hard_limit_tokens": self.settings.critic_hard_limit_tokens,
            "llm_stage_timeout_seconds": self.settings.llm_stage_timeout_seconds,
        }
