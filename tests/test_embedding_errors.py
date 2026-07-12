from types import SimpleNamespace

import pytest

from tech_doc_agent.app.core.errors import RateLimited, ValidationError
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
        lambda: Settings(EMBEDDING_API_KEY="test", EMBEDDING_MODEL="embedding-model"),
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
