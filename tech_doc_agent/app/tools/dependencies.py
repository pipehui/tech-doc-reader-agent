from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from tech_doc_agent.app.application.learning_state import (
    UpdateLearningStateCommand,
    UpdateLearningStateResult,
)
from tech_doc_agent.app.services.retrieval.models import SearchQuery, SearchResult


class DocumentStorePort(Protocol):
    def add_documents(self, documents: list[dict[str, Any]]) -> dict[str, Any]: ...

    def save(self) -> Any: ...


class DocumentRetrieverPort(Protocol):
    def retrieve(self, request: SearchQuery) -> list[SearchResult]: ...

    def refresh(self) -> None: ...


class WebSearchPort(Protocol):
    def search(self, query: str) -> list[dict[str, Any]]: ...


class LearningStorePort(Protocol):
    def read_by_query(
        self,
        query: str,
        *,
        user_id: str,
        namespace: str,
    ) -> list[dict[str, Any]]: ...

    def read_overview(self, *, user_id: str, namespace: str) -> list[dict[str, Any]]: ...


class MemoryStorePort(Protocol):
    def read_by_query(
        self,
        query: str,
        *,
        user_id: str,
        namespace: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def read_recent(
        self,
        *,
        user_id: str,
        namespace: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...


class LearningStateServicePort(Protocol):
    def update(
        self,
        command: UpdateLearningStateCommand,
    ) -> UpdateLearningStateResult: ...


class UserProfilePort(Protocol):
    def get_profile(self, *, user_id: str, namespace: str) -> dict[str, Any]: ...

    def update_profile(
        self,
        *,
        user_id: str,
        namespace: str,
        **updates: Any,
    ) -> dict[str, Any]: ...


class ResourceContainer(Protocol):
    faiss_store: DocumentStorePort
    hybrid_retriever: DocumentRetrieverPort
    learning_store: LearningStorePort
    memory_store: MemoryStorePort
    learning_state_service: LearningStateServicePort
    profile_service: UserProfilePort
    web_search_backend: WebSearchPort


@dataclass(frozen=True, slots=True)
class ToolDependencies:
    document_store: DocumentStorePort
    document_retriever: DocumentRetrieverPort
    learning_store: LearningStorePort
    memory_store: MemoryStorePort
    learning_state_service: LearningStateServicePort
    profile_service: UserProfilePort
    web_search: WebSearchPort

    @classmethod
    def from_container(cls, resources: ResourceContainer) -> ToolDependencies:
        return cls(
            document_store=resources.faiss_store,
            document_retriever=resources.hybrid_retriever,
            learning_store=resources.learning_store,
            memory_store=resources.memory_store,
            learning_state_service=resources.learning_state_service,
            profile_service=resources.profile_service,
            web_search=resources.web_search_backend,
        )
