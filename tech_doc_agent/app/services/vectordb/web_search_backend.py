import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from duckduckgo_search import DDGS
from tavily import TavilyClient
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.settings import get_settings

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
        with open(self.usage_path, 'r', encoding="utf-8") as f:
            self.usage_state = json.load(f)
        return True
    
    def save_usage_state(self) -> bool:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.usage_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.usage_state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.usage_path)
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
        except Exception:
            return []

    def search_with_tavily(self, query: str, max_results: int = 5) -> list[dict]:
        try:
            client = TavilyClient(self.tavily_api_key)
            response = client.search(
                query=query,
                search_depth="basic",
                max_results=max_results
            )
            return self._normalize_tavily_results(response.get("results", []))
        except Exception:
            return []

    def search(self, query: str) -> list[dict]:
        self.sync_today_usage()
        if self.can_use_tavily():
            self.consume_tavily_quota()
            results = self.search_with_tavily(query)
            if results:
                return results
        
        return self.search_with_ddg(query)
    
