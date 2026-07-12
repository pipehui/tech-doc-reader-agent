from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tech_doc_agent.app.application.learning_models import LearningRecord
from tech_doc_agent.app.application.learning_state import (
    LearningStateService,
    LearningStateUnitOfWork,
)
from tech_doc_agent.app.application.profile_service import UserProfileService
from tech_doc_agent.app.core.errors import ApplicationError, safe_error_fields
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.infrastructure.persistence.learning_state_repository import (
    LearningStateSnapshotRepository,
)
from tech_doc_agent.app.infrastructure.persistence.user_profile_repository import (
    JsonUserProfileRepository,
)
from tech_doc_agent.app.services.retrieval import HybridRetriever
from tech_doc_agent.app.services.vectordb.faiss_store import FaissStore
from tech_doc_agent.app.services.vectordb.learning_store_backend import LearningStore
from tech_doc_agent.app.services.vectordb.memory_store_backend import MemoryStore
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
    memory_store: MemoryStore
    learning_state_service: LearningStateService
    profile_service: UserProfileService
    web_search_backend: WebSearchBackend

    @classmethod
    def create(cls, settings: Settings | None = None) -> AppResources:
        settings = settings or get_settings()
        faiss_store = _initialize_faiss_store(settings)
        learning_store, memory_store, learning_state_service = _initialize_learning_state(settings)
        return cls(
            settings=settings,
            faiss_store=faiss_store,
            hybrid_retriever=HybridRetriever(faiss_store, settings=settings),
            learning_store=learning_store,
            memory_store=memory_store,
            learning_state_service=learning_state_service,
            profile_service=UserProfileService(
                repository=JsonUserProfileRepository(Path(settings.DATA_PATH)),
                memory_store=memory_store,
            ),
            web_search_backend=WebSearchBackend(settings=settings),
        )


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
    except ApplicationError as exc:
        _seed_documents_without_index(store)
        log_event(
            "resources.faiss.seeded_without_index",
            documents=len(store.documents),
            **safe_error_fields(exc),
        )

    return store


def _initialize_learning_state(
    settings: Settings,
) -> tuple[LearningStore, MemoryStore, LearningStateService]:
    repository = LearningStateSnapshotRepository(Path(settings.DATA_PATH))
    unit_of_work = LearningStateUnitOfWork(repository)
    learning_store = LearningStore(settings=settings, unit_of_work=unit_of_work)
    memory_store = MemoryStore(settings=settings, unit_of_work=unit_of_work)

    if unit_of_work.load():
        log_event(
            "resources.learning_store.loaded",
            records=len(learning_store.record_models),
        )
        log_event(
            "resources.memory_store.loaded",
            memories=len(memory_store.memory_models),
        )
    else:
        unit_of_work.replace_records(
            [LearningRecord.from_payload(record) for record in SEED_LEARNING_HISTORY]
        )
        unit_of_work.replace_memories(())
        unit_of_work.save()
        log_event(
            "resources.learning_store.seeded",
            records=len(learning_store.record_models),
        )
        log_event(
            "resources.memory_store.initialized",
            memories=len(memory_store.memory_models),
        )

    service = LearningStateService(
        unit_of_work,
        learning_store,
        memory_store,
    )
    return learning_store, memory_store, service
