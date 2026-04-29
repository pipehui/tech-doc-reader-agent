from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.services.retrieval import HybridRetriever
from tech_doc_agent.app.services.vectordb.faiss_store import FaissStore
from tech_doc_agent.app.services.vectordb.learning_store_backend import LearningStore
from tech_doc_agent.app.services.vectordb.web_search_backend import WebSearchBackend


SEED_DOCS = [
    {
        "title": "LangGraph StateGraph",
        "content": "StateGraph 是 LangGraph 的核心类，用于构建状态驱动的多Agent工作流。它通过节点和边定义工作流图结构，支持条件分支和循环。",
        "source": "seed",
    },
    {
        "title": "FastAPI 依赖注入",
        "content": "FastAPI 通过 Depends() 实现依赖注入，支持嵌套依赖、生命周期管理和异步处理。",
        "source": "seed",
    },
    {
        "title": "RAG 检索增强生成",
        "content": "RAG 将检索系统与生成模型结合，先从知识库中检索相关文档片段，再将其作为上下文输入给LLM生成回答。",
        "source": "seed",
    },
]

SEED_LEARNING_HISTORY = [
    {
        "knowledge": "LangGraph StateGraph",
        "timestamp": "2024-01-01T10:00:00Z",
        "score": 0.8,
        "reviewtimes": 1,
    },
    {
        "knowledge": "FastAPI 依赖注入",
        "timestamp": "2024-01-02T11:00:00Z",
        "score": 0.9,
        "reviewtimes": 2,
    },
]


@dataclass
class AppResources:
    settings: Settings
    faiss_store: FaissStore
    hybrid_retriever: HybridRetriever
    learning_store: LearningStore
    web_search_backend: WebSearchBackend

    @classmethod
    def create(cls, settings: Settings | None = None) -> AppResources:
        settings = settings or get_settings()
        faiss_store = _initialize_faiss_store(settings)
        return cls(
            settings=settings,
            faiss_store=faiss_store,
            hybrid_retriever=HybridRetriever(faiss_store, settings=settings),
            learning_store=_initialize_learning_store(settings),
            web_search_backend=WebSearchBackend(settings=settings),
        )


_current_resources: AppResources | None = None


def _seed_documents_without_index(store: FaissStore) -> None:
    store.documents = [
        {
            "id": index + 1,
            "title": doc["title"],
            "content": doc["content"],
            "source": doc.get("source", ""),
        }
        for index, doc in enumerate(SEED_DOCS)
    ]


def _initialize_faiss_store(settings: Settings) -> FaissStore:
    store = FaissStore(settings=settings)
    if store.load():
        log_event("resources.faiss.loaded", documents=len(store.documents))
        return store

    if not settings.SEED_DOC_STORE_ON_EMPTY:
        log_event("resources.faiss.empty", reason="seed_disabled")
        return store

    if not settings.EMBEDDING_API_KEY or not settings.EMBEDDING_MODEL:
        _seed_documents_without_index(store)
        log_event(
            "resources.faiss.seeded_without_index",
            documents=len(store.documents),
            reason="embedding_not_configured",
        )
        return store

    try:
        result = store.build_index(SEED_DOCS)
        store.save()
        log_event(
            "resources.faiss.seeded",
            documents=result["added_documents"],
            chunks=result["added_chunks"],
        )
    except Exception as exc:
        _seed_documents_without_index(store)
        log_event(
            "resources.faiss.seeded_without_index",
            documents=len(store.documents),
            error_type=type(exc).__name__,
            error=str(exc),
        )

    return store


def _initialize_learning_store(settings: Settings) -> LearningStore:
    store = LearningStore(settings=settings)
    if store.load():
        log_event("resources.learning_store.loaded", records=len(store.records))
        return store

    store.records = [dict(record) for record in SEED_LEARNING_HISTORY]
    store.save()
    log_event("resources.learning_store.seeded", records=len(store.records))
    return store


def set_app_resources(resources: AppResources) -> None:
    global _current_resources
    _current_resources = resources


def reset_app_resources() -> None:
    global _current_resources
    _current_resources = None


def get_app_resources() -> AppResources:
    global _current_resources
    if _current_resources is None:
        _current_resources = AppResources.create()
    return _current_resources


@contextmanager
def override_app_resources(resources: AppResources) -> Iterator[None]:
    previous = _current_resources
    set_app_resources(resources)

    try:
        yield
    finally:
        if previous is None:
            reset_app_resources()
        else:
            set_app_resources(previous)
