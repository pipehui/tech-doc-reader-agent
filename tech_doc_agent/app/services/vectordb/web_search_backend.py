import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from duckduckgo_search import DDGS
from tavily import TavilyClient
from tech_doc_agent.app.core.errors import (
    ApplicationError,
    DependencyUnavailable,
    RateLimited,
    ValidationError,
    safe_error_fields,
)
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.retry import RetryExecutor, build_retry_executor
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.infrastructure.persistence import read_json, write_json_atomic


class WebSearchBackend:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        retry_executor: RetryExecutor | None = None,
    ) -> None:
        settings = settings or get_settings()
        self.store_dir = Path(settings.DATA_PATH) / "web_search"
        self.usage_path = self.store_dir / "tavily_usage.json"
        self.tavily_api_key = settings.TAVILY_API_KEY
        self.tavily_daily_limit = int(settings.TAVILY_DAILY_LIMIT)
        self.usage_state: dict[str, Any] = {
            "date": "",
            "tavily_calls": 0,
        }
        self.proxy_url = settings.PROXY_URL
        self.retry_executor = retry_executor or build_retry_executor(settings)
        self._usage_lock = threading.Lock()
        self.load_usage_state()
    
    def load_usage_state(self) -> bool:
        if not self.usage_path.exists():
            return False
        loaded = read_json(self.usage_path)
        date = loaded.get("date") if isinstance(loaded, dict) else None
        tavily_calls = loaded.get("tavily_calls") if isinstance(loaded, dict) else None
        if (
            not isinstance(date, str)
            or isinstance(tavily_calls, bool)
            or not isinstance(tavily_calls, int)
            or tavily_calls < 0
        ):
            raise ValidationError(
                "The web search usage state is invalid.",
                code="web_search_usage_state_invalid",
                dependency="file_repository",
                cause_type=type(loaded).__name__,
            )
        self.usage_state = {
            "date": date,
            "tavily_calls": tavily_calls,
        }
        return True
    
    def save_usage_state(self) -> bool:
        write_json_atomic(self.usage_path, self.usage_state)
        return True

    def sync_today_usage(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._usage_lock:
            if self.usage_state["date"] != today:
                previous = dict(self.usage_state)
                self.usage_state["date"] = today
                self.usage_state["tavily_calls"] = 0
                try:
                    self.save_usage_state()
                except Exception:
                    self.usage_state = previous
                    raise

    def can_use_tavily(self) -> bool:
        self.sync_today_usage()
        with self._usage_lock:
            return bool(
                self.tavily_api_key
                and self.usage_state["tavily_calls"] < self.tavily_daily_limit
            )

    def _reserve_tavily_quota(self, attempt: int) -> None:
        del attempt
        today = datetime.now().strftime("%Y-%m-%d")
        with self._usage_lock:
            if not self.tavily_api_key:
                raise ValidationError(
                    "The Tavily search provider is not configured.",
                    code="tavily_not_configured",
                    dependency="tavily",
                    tool="web_search",
                    cause_type="MissingConfiguration",
                )

            current_calls = (
                self.usage_state["tavily_calls"] if self.usage_state["date"] == today else 0
            )
            if current_calls >= self.tavily_daily_limit:
                raise RateLimited(
                    "The local Tavily daily limit was reached.",
                    code="tavily_daily_limit_reached",
                    retryable=False,
                    dependency="tavily",
                    tool="web_search",
                    cause_type="LocalDailyLimit",
                )

            previous = dict(self.usage_state)
            self.usage_state["date"] = today
            self.usage_state["tavily_calls"] = current_calls
            self.usage_state["tavily_calls"] += 1
            try:
                self.save_usage_state()
            except Exception:
                self.usage_state = previous
                raise

    def _clean_text(self, text: str, max_length: int = 300) -> str:
        if not text:
            return ""
        
        text = " ".join(text.split())
        if len(text) > max_length:
            text = text[:max_length].rstrip() + "..."

        return text
    
    def _clean_result(self, item: dict) -> dict:
        return {
            "title": self._clean_text(item.get("title", ""), max_length=120),
            "url": item.get("url", "").strip(),
            "snippet": self._clean_text(item.get("snippet", ""), max_length=1000),
            "provider": item.get("provider", "").strip(),
        }
    
    def _is_usable_result(self, item: dict) -> bool:
        title = item.get("title", "")
        url = item.get("url", "")
        snippet = item.get("snippet", "")

        if not title or not url or not snippet:
            return False

        if len(snippet) < 30:
            return False

        # 过滤明显像目录页/链接堆的内容
        if snippet.count("http") >= 3:
            return False
        if snippet.count("](") >= 3:
            return False
        if snippet.count("###") >= 2:
            return False

        return True

    def _postprocess_results(self, items: list[dict]) -> list[dict]:
        results = []
        seen_urls = set()

        for item in items:
            cleaned = self._clean_result(item)

            if not self._is_usable_result(cleaned):
                continue

            url = cleaned["url"]
            if url in seen_urls:
                continue

            seen_urls.add(url)
            results.append(cleaned)

        return results

    def _normalize_ddg_results(self, raw_results) -> list[dict]:
        normalized_results = []

        for item in raw_results:
            normalized_results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("href", ""),
                    "snippet": item.get("body", ""),
                    "provider": "duckduckgo",
                }
            )
        
        return self._postprocess_results(normalized_results)

    def _normalize_tavily_results(self, raw_results) -> list[dict]:
        normalized_results = []

        for item in raw_results:
            normalized_results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                    "provider": "tavily",
                }
            )

        return self._postprocess_results(normalized_results)
        

    def search_with_ddg(self, query: str, max_results: int = 5) -> list[dict]:
        def request():
            with DDGS(proxy=self.proxy_url, timeout=20) as ddgs:
                return list(ddgs.text(query, max_results=max_results))

        raw_results = self.retry_executor.run(
            request,
            operation_name="web_search.duckduckgo",
            dependency="duckduckgo",
            tool="web_search",
            idempotent=True,
        )
        try:
            return self._normalize_ddg_results(raw_results)
        except (AttributeError, TypeError) as exc:
            raise ValidationError(
                "The DuckDuckGo dependency returned an invalid response.",
                code="duckduckgo_response_invalid",
                dependency="duckduckgo",
                tool="web_search",
                cause=exc,
            ) from exc

    def search_with_tavily(self, query: str, max_results: int = 5) -> list[dict]:
        def request():
            client = TavilyClient(self.tavily_api_key)
            return client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
            )

        response = self.retry_executor.run(
            request,
            operation_name="web_search.tavily",
            dependency="tavily",
            tool="web_search",
            idempotent=True,
            before_attempt=self._reserve_tavily_quota,
        )
        try:
            return self._normalize_tavily_results(response.get("results", []))
        except (AttributeError, TypeError) as exc:
            raise ValidationError(
                "The Tavily dependency returned an invalid response.",
                code="tavily_response_invalid",
                dependency="tavily",
                tool="web_search",
                cause=exc,
            ) from exc

    def search(self, query: str) -> list[dict]:
        tavily_error: ApplicationError | None = None
        if self.can_use_tavily():
            try:
                results = self.search_with_tavily(query)
            except ApplicationError as exc:
                tavily_error = exc
                log_event(
                    "web_search.provider.degraded",
                    provider="tavily",
                    fallback="duckduckgo",
                    **safe_error_fields(exc),
                )
            else:
                if results:
                    return results

        try:
            return self.search_with_ddg(query)
        except ApplicationError as exc:
            if tavily_error is None:
                raise
            raise DependencyUnavailable(
                "All configured web search providers are unavailable.",
                code="web_search_unavailable",
                dependency="web_search",
                tool="web_search",
                cause_type=exc.cause_type,
            ) from exc
    
