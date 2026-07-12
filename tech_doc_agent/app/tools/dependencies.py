from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from tech_doc_agent.app.application.learning_state import (
    UpdateLearningStateCommand,
    UpdateLearningStateResult,
)
from tech_doc_agent.app.application.learning_models import LearningRecord, MemoryFragment
from tech_doc_agent.app.application.profile_models import (
    UserProfile,
    UserProfileUpdateResult,
)
from tech_doc_agent.app.application.retrieval import DocumentRetrieverPort


class DocumentStorePort(Protocol):
    def add_documents(self, documents: list[dict[str, Any]]) -> dict[str, Any]: ...

    def save(self) -> Any: ...


class WebSearchPort(Protocol):
    def search(self, query: str) -> list[dict[str, Any]]: ...


class LearningStorePort(Protocol):
    def query_records(
        self,
        query: str,
        *,
        user_id: str,
        namespace: str,
    ) -> list[LearningRecord]: ...

    def list_records(self, *, user_id: str, namespace: str) -> list[LearningRecord]: ...


class MemoryStorePort(Protocol):
    def query_memories(
        self,
        query: str,
        *,
        user_id: str,
        namespace: str,
        limit: int,
    ) -> list[MemoryFragment]: ...

    def recent_memories(
        self,
        *,
        user_id: str,
        namespace: str,
        limit: int,
    ) -> list[MemoryFragment]: ...


class LearningStateServicePort(Protocol):
    def update(
        self,
        command: UpdateLearningStateCommand,
    ) -> UpdateLearningStateResult: ...


class UserProfilePort(Protocol):
    def get_profile(self, *, user_id: str, namespace: str) -> UserProfile: ...

    def update_profile(
        self,
        *,
        user_id: str,
        namespace: str,
        experience_level: str | None = None,
        explanation_style: str | None = None,
        depth: str | None = None,
        language: str | None = None,
        known_topics: list[str] | None = None,
        weak_topics: list[str] | None = None,
        resolved_weak_topics: list[str] | None = None,
        notes: str | None = None,
        evidence: str | None = None,
    ) -> UserProfileUpdateResult: ...

    def context_summary(
        self,
        *,
        user_id: str,
        namespace: str,
        memory_query: str = "",
        memory_limit: int = 5,
    ) -> str: ...


class ToolResourceContainer(Protocol):
    @property
    def faiss_store(self) -> DocumentStorePort: ...

    @property
    def hybrid_retriever(self) -> DocumentRetrieverPort: ...

    @property
    def learning_store(self) -> LearningStorePort: ...

    @property
    def memory_store(self) -> MemoryStorePort: ...

    @property
    def learning_state_service(self) -> LearningStateServicePort: ...

    @property
    def profile_service(self) -> UserProfilePort: ...

    @property
    def web_search_backend(self) -> WebSearchPort: ...


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
    def from_container(cls, resources: ToolResourceContainer) -> ToolDependencies:
        return cls(
            document_store=resources.faiss_store,
            document_retriever=resources.hybrid_retriever,
            learning_store=resources.learning_store,
            memory_store=resources.memory_store,
            learning_state_service=resources.learning_state_service,
            profile_service=resources.profile_service,
            web_search=resources.web_search_backend,
        )
