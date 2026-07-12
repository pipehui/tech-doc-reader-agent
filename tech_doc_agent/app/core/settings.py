import json
from decimal import Decimal
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".dev.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    PRIMARY_MODEL: str = ""
    MODEL_PROVIDER_ID: str = "openai_compatible"
    MODEL_PRICE_TABLE_PATH: str = ""

    DATA_PATH: str = "./tech_doc_agent/data"
    LOG_LEVEL: str = "DEBUG"
    TELEMETRY_PSEUDONYM_KEY: SecretStr = SecretStr("")

    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_BASE: str = ""
    EMBEDDING_MODEL: str = ""

    TAVILY_API_KEY: str = ""
    TAVILY_DAILY_LIMIT: int = Field(default=10, ge=0)

    PROXY_URL: str = ""

    BACKUP_API_BASE: str = ""
    BACKUP_API_KEY: str = ""
    BACKUP_MODEL: str = ""

    REDIS_URL: str = "redis://localhost:6379"
    REDIS_SETUP_MAX_ATTEMPTS: int = 60
    REDIS_SETUP_RETRY_SECONDS: float = 1.0
    TRANSPORT_RETRY_MAX_ATTEMPTS: int = Field(default=3, ge=1)
    TRANSPORT_RETRY_INITIAL_DELAY_SECONDS: float = Field(default=0.25, ge=0)
    TRANSPORT_RETRY_MAX_DELAY_SECONDS: float = Field(default=2.0, ge=0)
    TRANSPORT_RETRY_BACKOFF_MULTIPLIER: float = Field(default=2.0, ge=1)
    TRANSPORT_RETRY_JITTER_RATIO: float = Field(default=0.2, ge=0, le=1)
    TRANSPORT_RETRY_MAX_RETRY_AFTER_SECONDS: float = Field(default=30.0, ge=0)
    GUARDRAIL_APPROVAL_TTL_SECONDS: int = Field(default=900, gt=0)
    LANGGRAPH_RECURSION_LIMIT: int = 80
    REQUEST_MAX_SECONDS: float = Field(default=300.0, ge=0)
    WORKFLOW_MAX_LLM_CALLS: int = Field(default=32, ge=0)
    WORKFLOW_MAX_TOOL_CALLS: int = Field(default=48, ge=0)
    WORKFLOW_MAX_TOTAL_TOKENS: int = Field(default=0, ge=0)
    WORKFLOW_MAX_ESTIMATED_COST_USD: Decimal = Field(default=Decimal("0"), ge=0)
    MAX_IDENTICAL_TOOL_REPEATS: int = Field(default=2, ge=0)
    PARSER_MAX_RETRIEVAL_CALLS: int = Field(default=6, ge=0)
    MAX_REFLECTION_ROUNDS: int = Field(default=1, ge=0)
    CONTEXT_COMPACTION_MAX_MESSAGES: int = Field(default=0, ge=0)
    CONTEXT_COMPACTION_MAX_SERIALIZED_BYTES: int = Field(default=0, ge=0)
    CONTEXT_COMPACTION_KEEP_RECENT_TURNS: int = Field(default=4, ge=1)
    CONTEXT_SUMMARY_MAX_CHARS: int = Field(default=12_000, ge=256)
    RUNTIME_IDENTITY_ENDPOINT_ENABLED: bool = False

    HYBRID_RAG_TOP_K: int = 5
    HYBRID_RAG_BM25_TOP_K: int = 8
    HYBRID_RAG_VECTOR_TOP_K: int = 8
    HYBRID_RAG_RRF_K: int = 60
    SEED_DOC_STORE_ON_EMPTY: bool = False

    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = ""
    LANGFUSE_HOST: str = ""
    LANGFUSE_FLUSH_ON_REQUEST: bool = False
    LANGFUSE_ENVIRONMENT: str = "local"
    LANGFUSE_RELEASE: str = ""

    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("TELEMETRY_PSEUDONYM_KEY")
    @classmethod
    def validate_telemetry_pseudonym_key(cls, value: SecretStr) -> SecretStr:
        raw_value = value.get_secret_value()
        if raw_value and len(raw_value.encode("utf-8")) < 16:
            raise ValueError("TELEMETRY_PSEUDONYM_KEY must contain at least 16 bytes when configured.")
        return value

    @field_validator("MODEL_PROVIDER_ID")
    @classmethod
    def validate_model_provider_id(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("MODEL_PROVIDER_ID must be a non-empty trimmed string.")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
