from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Novel AI System"
    app_env: str = "development"
    debug: bool = True
    log_level: str = "INFO"

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/novel_ai"
    )
    worker_poll_interval_seconds: float = 2.0
    worker_lease_seconds: int = 60
    worker_name: str = "local-worker"
    llm_connect_timeout_seconds: float = 15.0
    llm_read_timeout_seconds: float = 120.0
    llm_write_timeout_seconds: float = 30.0
    llm_pool_timeout_seconds: float = 30.0
    llm_max_retries: int = 10
    llm_retry_backoff_seconds: float = 1.5
    llm_stage_timeout_seconds: float = 180.0
    review_overall_score_threshold: float = 8.0
    review_outline_score_threshold: float = 8.0
    review_instruction_score_threshold: float = 8.0
    memory_auto_apply_confidence_threshold: float = 0.75
    writer_target_input_tokens: int = 64000
    writer_hard_limit_tokens: int = 96000
    critic_target_input_tokens: int = 32000
    critic_hard_limit_tokens: int = 48000
    model_secret_encryption_key: str | None = None

    default_writer_temperature: float | None = 1.2
    default_writer_provider: str = "openai_compatible"
    default_writer_base_url: str = "http://43.173.121.145:4000/v1"
    default_writer_model_name: str = "deepseek-v3.2"
    default_writer_max_tokens: int | None = None
    default_writer_api_key_env_var: str = "WRITER_API_KEY"

    default_critic_provider: str = "openai_compatible"
    default_critic_base_url: str = ""
    default_critic_model_name: str = "gpt-5.4(high)"
    default_critic_temperature: float | None = None
    default_critic_max_tokens: int | None = None
    default_critic_api_key_env_var: str = "CRITIC_API_KEY"

    default_memory_provider: str = "openai_compatible"
    default_memory_base_url: str = "https://cli.746525006.xyz/v1"
    default_memory_model_name: str = "gpt-5.4-mini"
    default_memory_temperature: float | None = None
    default_memory_max_tokens: int | None = None
    default_memory_api_key_env_var: str = "MEMORY_API_KEY"

    default_prompt_builder_provider: str = "openai_compatible"
    default_prompt_builder_base_url: str = "http://43.173.121.145:4000/v1"
    default_prompt_builder_model_name: str = "deepseek-v3.2"
    default_prompt_builder_temperature: float | None = None
    default_prompt_builder_max_tokens: int | None = None
    default_prompt_builder_api_key_env_var: str = "WRITER_API_KEY"


    @field_validator(
        "default_writer_temperature",
        "default_writer_max_tokens",
        "default_critic_temperature",
        "default_critic_max_tokens",
        "default_memory_temperature",
        "default_memory_max_tokens",
        "default_prompt_builder_temperature",
        "default_prompt_builder_max_tokens",
        mode="before",
    )
    @classmethod
    def normalize_optional_numeric_settings(cls, value):
        if value == "":
            return None
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="NOVEL_AI_",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
