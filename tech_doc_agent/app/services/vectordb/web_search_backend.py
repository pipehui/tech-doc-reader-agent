import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from duckduckgo_search import DDGS
from tavily import TavilyClient
from tech_doc_agent.app.core.errors import (
    ApplicationError,
    DependencyUnavailable,
    ValidationError,
    classify_error,
    safe_error_fields,
)
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.infrastructure.persistence import read_json, write_json_atomic

class WebSearchBackend:
    def __init__(self, settings: Settings | None = None) -> None:
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
                self.usage_state["date"] = today
                self.usage_state["tavily_calls"] = 0
                self.save_usage_state()

    def can_use_tavily(self) -> bool:
        if self.tavily_api_key != "" and self.usage_state["tavily_calls"] < self.tavily_daily_limit:
            return True
        return False

    def consume_tavily_quota(self) -> bool:
        with self._usage_lock:
            self.usage_state["tavily_calls"] += 1
            return self.save_usage_state()

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
        try:
            with DDGS(proxy=self.proxy_url, timeout=20) as ddgs:
                raw_results = list(ddgs.text(query, max_results=max_results))
            return self._normalize_ddg_results(raw_results)
        except Exception as exc:
            raise classify_error(
                exc,
                dependency="duckduckgo",
                tool="web_search",
            ) from exc

    def search_with_tavily(self, query: str, max_results: int = 5) -> list[dict]:
        try:
            client = TavilyClient(self.tavily_api_key)
            response = client.search(
                query=query,
                search_depth="basic",
                max_results=max_results
            )
            return self._normalize_tavily_results(response.get("results", []))
        except Exception as exc:
            raise classify_error(
                exc,
                dependency="tavily",
                tool="web_search",
            ) from exc

    def search(self, query: str) -> list[dict]:
        self.sync_today_usage()
        tavily_error: ApplicationError | None = None
        if self.can_use_tavily():
            self.consume_tavily_quota()
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
    
