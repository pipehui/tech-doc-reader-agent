import pytest

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
        TRANSPORT_RETRY_MAX_ATTEMPTS="4",
        TRANSPORT_RETRY_INITIAL_DELAY_SECONDS="0.1",
        TRANSPORT_RETRY_MAX_DELAY_SECONDS="1.5",
        TRANSPORT_RETRY_BACKOFF_MULTIPLIER="1.5",
        TRANSPORT_RETRY_JITTER_RATIO="0.1",
        TRANSPORT_RETRY_MAX_RETRY_AFTER_SECONDS="12",
        GUARDRAIL_APPROVAL_TTL_SECONDS="120",
        MAX_IDENTICAL_TOOL_REPEATS="4",
        PARSER_MAX_RETRIEVAL_CALLS="9",
        TELEMETRY_PSEUDONYM_KEY="controlled-key-with-32-random-bytes",
    )

    assert settings.TAVILY_DAILY_LIMIT == 7
    assert settings.HYBRID_RAG_TOP_K == 3
    assert settings.SEED_DOC_STORE_ON_EMPTY is True
    assert settings.REDIS_SETUP_MAX_ATTEMPTS == 3
    assert settings.REDIS_SETUP_RETRY_SECONDS == 0.5
    assert settings.TRANSPORT_RETRY_MAX_ATTEMPTS == 4
    assert settings.TRANSPORT_RETRY_INITIAL_DELAY_SECONDS == 0.1
    assert settings.TRANSPORT_RETRY_MAX_DELAY_SECONDS == 1.5
    assert settings.TRANSPORT_RETRY_BACKOFF_MULTIPLIER == 1.5
    assert settings.TRANSPORT_RETRY_JITTER_RATIO == 0.1
    assert settings.TRANSPORT_RETRY_MAX_RETRY_AFTER_SECONDS == 12
    assert settings.GUARDRAIL_APPROVAL_TTL_SECONDS == 120
    assert settings.MAX_IDENTICAL_TOOL_REPEATS == 4
    assert settings.PARSER_MAX_RETRIEVAL_CALLS == 9
    assert settings.ALLOWED_ORIGINS == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    assert settings.LANGFUSE_ENABLED is True
    assert settings.LANGFUSE_FLUSH_ON_REQUEST is True
    assert settings.TELEMETRY_PSEUDONYM_KEY.get_secret_value() == "controlled-key-with-32-random-bytes"


def test_settings_rejects_weak_telemetry_pseudonym_key():
    with pytest.raises(ValueError, match="at least 16 bytes"):
        Settings(TELEMETRY_PSEUDONYM_KEY="short")


@pytest.mark.parametrize(
    "field",
    ["MAX_IDENTICAL_TOOL_REPEATS", "PARSER_MAX_RETRIEVAL_CALLS"],
)
def test_settings_rejects_negative_tool_policy_limits(field):
    with pytest.raises(ValueError):
        Settings(**{field: -1})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("TAVILY_DAILY_LIMIT", -1),
        ("TRANSPORT_RETRY_MAX_ATTEMPTS", 0),
        ("TRANSPORT_RETRY_INITIAL_DELAY_SECONDS", -1),
        ("TRANSPORT_RETRY_MAX_DELAY_SECONDS", -1),
        ("TRANSPORT_RETRY_BACKOFF_MULTIPLIER", 0.5),
        ("TRANSPORT_RETRY_JITTER_RATIO", 1.1),
        ("TRANSPORT_RETRY_MAX_RETRY_AFTER_SECONDS", -1),
    ],
)
def test_settings_rejects_invalid_transport_retry_policy(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})


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
