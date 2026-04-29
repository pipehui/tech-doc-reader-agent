from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".dev.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    PRIMARY_MODEL: str = ""

    DATA_PATH: str = "./tech_doc_agent/data"
    LOG_LEVEL: str = "DEBUG"

    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_BASE: str = ""
    EMBEDDING_MODEL: str = ""

    TAVILY_API_KEY: str = ""
    TAVILY_DAILY_LIMIT: int = 10

    PROXY_URL: str = ""

    BACKUP_API_BASE: str = ""
    BACKUP_API_KEY: str = ""
    BACKUP_MODEL: str = ""

    REDIS_URL: str = "redis://localhost:6379"
    REDIS_SETUP_MAX_ATTEMPTS: int = 60
    REDIS_SETUP_RETRY_SECONDS: float = 1.0
    LANGGRAPH_RECURSION_LIMIT: int = 80

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

    ALLOWED_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
