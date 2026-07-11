from tech_doc_agent.app.core.settings import Settings


def test_settings_parses_typed_values():
    settings = Settings(
        TAVILY_DAILY_LIMIT="7",
        ALLOWED_ORIGINS="http://127.0.0.1:5173,http://localhost:5173",
        LANGFUSE_ENABLED="true",
        LANGFUSE_FLUSH_ON_REQUEST="true",
        HYBRID_RAG_TOP_K="3",
        SEED_DOC_STORE_ON_EMPTY="true",
        REDIS_SETUP_MAX_ATTEMPTS="3",
        REDIS_SETUP_RETRY_SECONDS="0.5",
        GUARDRAIL_APPROVAL_TTL_SECONDS="120",
    )

    assert settings.TAVILY_DAILY_LIMIT == 7
    assert settings.HYBRID_RAG_TOP_K == 3
    assert settings.SEED_DOC_STORE_ON_EMPTY is True
    assert settings.REDIS_SETUP_MAX_ATTEMPTS == 3
    assert settings.REDIS_SETUP_RETRY_SECONDS == 0.5
    assert settings.GUARDRAIL_APPROVAL_TTL_SECONDS == 120
    assert settings.ALLOWED_ORIGINS == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    assert settings.LANGFUSE_ENABLED is True
    assert settings.LANGFUSE_FLUSH_ON_REQUEST is True


def test_settings_uses_project_data_path_by_default():
    settings = Settings()

    assert settings.DATA_PATH == "./tech_doc_agent/data"


def test_settings_parses_allowed_origins_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173\n",
        encoding="utf-8",
    )

    settings = Settings()

    assert settings.ALLOWED_ORIGINS == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
