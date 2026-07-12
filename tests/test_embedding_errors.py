from types import SimpleNamespace

import pytest

from tech_doc_agent.app.core.errors import RateLimited, ValidationError
from tech_doc_agent.app.core.retry import RetryExecutor, RetryPolicy
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services import embedding


class RateLimitError(RuntimeError):
    status_code = 429


def test_embedding_requires_configuration_without_listing_secret_setting_names(monkeypatch):
    monkeypatch.setattr(
        embedding,
        "get_settings",
        lambda: Settings(EMBEDDING_API_KEY="", EMBEDDING_MODEL=""),
    )

    with pytest.raises(ValidationError) as exc_info:
        embedding.generate_embedding("StateGraph")

    assert exc_info.value.code == "embedding_not_configured"
    assert exc_info.value.dependency == "embedding"
    assert "API_KEY" not in str(exc_info.value)


def test_embedding_maps_provider_error_and_keeps_raw_text_out_of_safe_error(monkeypatch):
    class FailingEmbeddings:
        def create(self, **kwargs):
            raise RateLimitError("quota exhausted for sk-private-value")

    monkeypatch.setattr(
        embedding,
        "get_settings",
        lambda: Settings(
            EMBEDDING_API_KEY="test",
            EMBEDDING_MODEL="embedding-model",
            TRANSPORT_RETRY_MAX_ATTEMPTS=1,
        ),
    )
    monkeypatch.setattr(
        embedding,
        "_build_embedding_client",
        lambda settings: SimpleNamespace(embeddings=FailingEmbeddings()),
    )

    with pytest.raises(RateLimited) as exc_info:
        embedding.generate_embedding("StateGraph")

    assert exc_info.value.dependency == "embedding"
    assert exc_info.value.cause_type == "RateLimitError"
    assert "sk-private-value" not in str(exc_info.value)


def test_embedding_returns_single_and_batch_shapes(monkeypatch):
    class FakeEmbeddings:
        def create(self, *, model, input):
            assert model == "embedding-model"
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[float(index), 1.0]) for index, _ in enumerate(input)]
            )

    monkeypatch.setattr(
        embedding,
        "get_settings",
        lambda: Settings(EMBEDDING_API_KEY="test", EMBEDDING_MODEL="embedding-model"),
    )
    monkeypatch.setattr(
        embedding,
        "_build_embedding_client",
        lambda settings: SimpleNamespace(embeddings=FakeEmbeddings()),
    )

    assert embedding.generate_embedding("one") == [0.0, 1.0]
    assert embedding.generate_embedding(["one", "two"]) == [[0.0, 1.0], [1.0, 1.0]]


def test_embedding_retries_transient_provider_failure_before_parsing_response(monkeypatch):
    class FlakyEmbeddings:
        def __init__(self):
            self.calls = 0

        def create(self, *, model, input):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("private embedding endpoint")
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 2.0])])

    embeddings = FlakyEmbeddings()
    monkeypatch.setattr(
        embedding,
        "_build_embedding_client",
        lambda settings: SimpleNamespace(embeddings=embeddings),
    )
    retry_executor = RetryExecutor(
        RetryPolicy(
            max_attempts=2,
            initial_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
        sleeper=lambda delay: None,
        event_logger=lambda event, **fields: None,
    )

    result = embedding.generate_embedding(
        "StateGraph",
        settings=Settings(EMBEDDING_API_KEY="test", EMBEDDING_MODEL="embedding-model"),
        retry_executor=retry_executor,
    )

    assert result == [1.0, 2.0]
    assert embeddings.calls == 2


def test_embedding_client_disables_sdk_level_retries(monkeypatch):
    captured = {}
    sentinel = object()

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(embedding, "OpenAI", fake_openai)

    client = embedding._build_embedding_client(
        Settings(EMBEDDING_API_KEY="test", EMBEDDING_MODEL="embedding-model")
    )

    assert client is sentinel
    assert captured["max_retries"] == 0
