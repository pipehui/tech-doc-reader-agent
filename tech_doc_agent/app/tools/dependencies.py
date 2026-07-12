from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from tech_doc_agent.app.application.learning_ports import (
    LearningRecordReaderPort,
    LearningStateCommandPort,
    MemoryReaderPort,
)
from tech_doc_agent.app.application.profile_service import UserProfileServicePort
from tech_doc_agent.app.application.retrieval import DocumentRetrieverPort


class DocumentStorePort(Protocol):
    def add_documents(self, documents: list[dict[str, Any]]) -> dict[str, Any]: ...

    def save(self) -> Any: ...


class WebSearchPort(Protocol):
    def search(self, query: str) -> list[dict[str, Any]]: ...


class ToolResourceContainer(Protocol):
    @property
    def faiss_store(self) -> DocumentStorePort: ...

    @property
    def hybrid_retriever(self) -> DocumentRetrieverPort: ...

    @property
    def learning_store(self) -> LearningRecordReaderPort: ...

    @property
    def memory_store(self) -> MemoryReaderPort: ...

    @property
    def learning_state_service(self) -> LearningStateCommandPort: ...

    @property
    def profile_service(self) -> UserProfileServicePort: ...

    @property
    def web_search_backend(self) -> WebSearchPort: ...


@dataclass(frozen=True, slots=True)
class ToolDependencies:
    document_store: DocumentStorePort
    document_retriever: DocumentRetrieverPort
    learning_store: LearningRecordReaderPort
    memory_store: MemoryReaderPort
    learning_state_service: LearningStateCommandPort
    profile_service: UserProfileServicePort
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
