from decimal import Decimal

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
        REQUEST_MAX_SECONDS="45.5",
        WORKFLOW_MAX_LLM_CALLS="12",
        WORKFLOW_MAX_TOOL_CALLS="20",
        WORKFLOW_MAX_TOTAL_TOKENS="50000",
        WORKFLOW_MAX_ESTIMATED_COST_USD="1.25",
        MAX_IDENTICAL_TOOL_REPEATS="4",
        PARSER_MAX_RETRIEVAL_CALLS="9",
        MAX_REFLECTION_ROUNDS="1",
        CONTEXT_COMPACTION_MAX_MESSAGES="80",
        CONTEXT_COMPACTION_MAX_SERIALIZED_BYTES="262144",
        CONTEXT_COMPACTION_KEEP_RECENT_TURNS="5",
        CONTEXT_SUMMARY_MAX_CHARS="10000",
        TELEMETRY_PSEUDONYM_KEY="controlled-key-with-32-random-bytes",
        MODEL_PROVIDER_ID="provider-a",
        DEPLOYMENT_COMMIT_SHA="a" * 40,
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
    assert settings.REQUEST_MAX_SECONDS == 45.5
    assert settings.WORKFLOW_MAX_LLM_CALLS == 12
    assert settings.WORKFLOW_MAX_TOOL_CALLS == 20
    assert settings.WORKFLOW_MAX_TOTAL_TOKENS == 50000
    assert settings.WORKFLOW_MAX_ESTIMATED_COST_USD == Decimal("1.25")
    assert settings.MAX_IDENTICAL_TOOL_REPEATS == 4
    assert settings.PARSER_MAX_RETRIEVAL_CALLS == 9
    assert settings.MAX_REFLECTION_ROUNDS == 1
    assert settings.CONTEXT_COMPACTION_MAX_MESSAGES == 80
    assert settings.CONTEXT_COMPACTION_MAX_SERIALIZED_BYTES == 262144
    assert settings.CONTEXT_COMPACTION_KEEP_RECENT_TURNS == 5
    assert settings.CONTEXT_SUMMARY_MAX_CHARS == 10000
    assert settings.ALLOWED_ORIGINS == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    assert settings.LANGFUSE_ENABLED is True
    assert settings.LANGFUSE_FLUSH_ON_REQUEST is True
    assert settings.TELEMETRY_PSEUDONYM_KEY.get_secret_value() == "controlled-key-with-32-random-bytes"
    assert settings.MODEL_PROVIDER_ID == "provider-a"
    assert settings.DEPLOYMENT_COMMIT_SHA == "a" * 40


def test_settings_rejects_weak_telemetry_pseudonym_key():
    with pytest.raises(ValueError, match="at least 16 bytes"):
        Settings(TELEMETRY_PSEUDONYM_KEY="short")


@pytest.mark.parametrize("provider_id", ["", " provider "])
def test_settings_rejects_invalid_model_provider_id(provider_id):
    with pytest.raises(ValueError, match="MODEL_PROVIDER_ID"):
        Settings(MODEL_PROVIDER_ID=provider_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("DEPLOYMENT_COMMIT_SHA", "abc123"),
        ("DEPLOYMENT_COMMIT_SHA", "A" * 40),
        ("IMAGE_COMMIT_SHA", " " + "a" * 40),
    ],
)
def test_settings_rejects_invalid_deployment_commit_identity(field, value):
    with pytest.raises(ValueError, match="full lowercase Git commit SHA"):
        Settings(**{field: value})


def test_settings_rejects_conflicting_runtime_and_image_commit_identity():
    with pytest.raises(ValueError, match="must match"):
        Settings(
            DEPLOYMENT_COMMIT_SHA="a" * 40,
            IMAGE_COMMIT_SHA="b" * 40,
        )


@pytest.mark.parametrize(
    "field",
    [
        "MAX_IDENTICAL_TOOL_REPEATS",
        "PARSER_MAX_RETRIEVAL_CALLS",
        "MAX_REFLECTION_ROUNDS",
        "REQUEST_MAX_SECONDS",
        "WORKFLOW_MAX_LLM_CALLS",
        "WORKFLOW_MAX_TOOL_CALLS",
        "WORKFLOW_MAX_TOTAL_TOKENS",
        "WORKFLOW_MAX_ESTIMATED_COST_USD",
        "CONTEXT_COMPACTION_MAX_MESSAGES",
        "CONTEXT_COMPACTION_MAX_SERIALIZED_BYTES",
    ],
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("CONTEXT_COMPACTION_KEEP_RECENT_TURNS", 0),
        ("CONTEXT_SUMMARY_MAX_CHARS", 255),
    ],
)
def test_settings_rejects_invalid_context_compaction_policy(field, value):
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
