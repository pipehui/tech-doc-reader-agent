import json

import pytest

from tech_doc_agent.app.core.errors import DependencyUnavailable, RateLimited, ValidationError
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.vectordb import web_search_backend
from tech_doc_agent.app.services.vectordb.web_search_backend import WebSearchBackend


class ProviderRateLimit(RuntimeError):
    status_code = 429


class FailingTavilyClient:
    calls = 0

    def __init__(self, api_key):
        assert api_key == "tavily-key"

    def search(self, **kwargs):
        type(self).calls += 1
        raise ProviderRateLimit("quota for tavily-key was exhausted")


class SuccessfulDDGS:
    calls = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def text(self, query, max_results):
        type(self).calls += 1
        return [
            {
                "title": "LangGraph StateGraph",
                "href": "https://example.test/stategraph",
                "body": "A sufficiently detailed result about graph state and reducers for the fallback test.",
            }
        ]


class FailingDDGS(SuccessfulDDGS):
    def text(self, query, max_results):
        type(self).calls += 1
        raise ConnectionError("proxy password was private-value")


def _backend(tmp_path) -> WebSearchBackend:
    return WebSearchBackend(
        Settings(
            DATA_PATH=str(tmp_path),
            TAVILY_API_KEY="tavily-key",
            TAVILY_DAILY_LIMIT=10,
            TRANSPORT_RETRY_INITIAL_DELAY_SECONDS=0,
            TRANSPORT_RETRY_MAX_DELAY_SECONDS=0,
            TRANSPORT_RETRY_JITTER_RATIO=0,
        )
    )


def test_web_search_falls_back_after_typed_tavily_failure(tmp_path, monkeypatch):
    events = []
    FailingTavilyClient.calls = 0
    SuccessfulDDGS.calls = 0
    monkeypatch.setattr(web_search_backend, "TavilyClient", FailingTavilyClient)
    monkeypatch.setattr(web_search_backend, "DDGS", SuccessfulDDGS)
    monkeypatch.setattr(
        web_search_backend,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    backend = _backend(tmp_path)
    results = backend.search("StateGraph")

    assert [result["provider"] for result in results] == ["duckduckgo"]
    assert FailingTavilyClient.calls == 3
    assert SuccessfulDDGS.calls == 1
    assert backend.usage_state["tavily_calls"] == 3
    assert events[0][0] == "web_search.provider.degraded"
    assert events[0][1]["error_code"] == "rate_limited"
    assert events[0][1]["retryable"] is True
    assert "tavily-key" not in str(events)


def test_individual_provider_exposes_typed_safe_error_instead_of_empty_results(tmp_path, monkeypatch):
    FailingTavilyClient.calls = 0
    monkeypatch.setattr(web_search_backend, "TavilyClient", FailingTavilyClient)

    with pytest.raises(RateLimited) as exc_info:
        _backend(tmp_path).search_with_tavily("StateGraph")

    assert exc_info.value.dependency == "tavily"
    assert exc_info.value.tool == "web_search"
    assert "tavily-key" not in str(exc_info.value)
    assert FailingTavilyClient.calls == 3


def test_web_search_raises_one_stable_error_when_all_providers_fail(tmp_path, monkeypatch):
    FailingTavilyClient.calls = 0
    FailingDDGS.calls = 0
    monkeypatch.setattr(web_search_backend, "TavilyClient", FailingTavilyClient)
    monkeypatch.setattr(web_search_backend, "DDGS", FailingDDGS)

    with pytest.raises(DependencyUnavailable) as exc_info:
        _backend(tmp_path).search("StateGraph")

    assert exc_info.value.code == "web_search_unavailable"
    assert exc_info.value.dependency == "web_search"
    assert exc_info.value.tool == "web_search"
    assert "private-value" not in str(exc_info.value)
    assert FailingTavilyClient.calls == 3
    assert FailingDDGS.calls == 3


def test_web_search_rejects_invalid_usage_state_instead_of_failing_later(tmp_path):
    usage_path = tmp_path / "web_search" / "tavily_usage.json"
    usage_path.parent.mkdir(parents=True)
    usage_path.write_text('{"date":"2026-07-12","tavily_calls":"secret"}', encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        _backend(tmp_path)

    assert exc_info.value.code == "web_search_usage_state_invalid"
    assert exc_info.value.dependency == "file_repository"
    assert "secret" not in str(exc_info.value)


def test_tavily_retry_reserves_quota_per_actual_provider_attempt(tmp_path, monkeypatch):
    FailingTavilyClient.calls = 0
    monkeypatch.setattr(web_search_backend, "TavilyClient", FailingTavilyClient)
    monkeypatch.setattr(web_search_backend, "DDGS", SuccessfulDDGS)
    backend = WebSearchBackend(
        Settings(
            DATA_PATH=str(tmp_path),
            TAVILY_API_KEY="tavily-key",
            TAVILY_DAILY_LIMIT=2,
            TRANSPORT_RETRY_MAX_ATTEMPTS=3,
            TRANSPORT_RETRY_INITIAL_DELAY_SECONDS=0,
            TRANSPORT_RETRY_MAX_DELAY_SECONDS=0,
            TRANSPORT_RETRY_JITTER_RATIO=0,
        )
    )

    results = backend.search("StateGraph")

    assert [result["provider"] for result in results] == ["duckduckgo"]
    assert FailingTavilyClient.calls == 2
    assert backend.usage_state["tavily_calls"] == 2
    persisted = json.loads(backend.usage_path.read_text(encoding="utf-8"))
    assert persisted["tavily_calls"] == 2


def test_tavily_quota_persistence_failure_rolls_back_before_provider_call(tmp_path, monkeypatch):
    FailingTavilyClient.calls = 0
    monkeypatch.setattr(web_search_backend, "TavilyClient", FailingTavilyClient)
    backend = _backend(tmp_path)

    def fail_save():
        raise OSError("private filesystem location")

    monkeypatch.setattr(backend, "save_usage_state", fail_save)

    with pytest.raises(DependencyUnavailable):
        backend.search_with_tavily("StateGraph")

    assert FailingTavilyClient.calls == 0
    assert backend.usage_state == {"date": "", "tavily_calls": 0}
