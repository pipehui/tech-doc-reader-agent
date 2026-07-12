import threading
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from tech_doc_agent.app.core.errors import (
    ApplicationError,
    DependencyUnavailable,
    ValidationError,
    classify_error,
)
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.infrastructure.persistence.faiss_snapshot import FaissSnapshotRepository
from tech_doc_agent.app.services.embedding import generate_embedding
from tech_doc_agent.app.infrastructure.retrieval.normalization import (
    normalize_chunk_metadata,
    normalize_document,
)
from tech_doc_agent.app.services.vectordb.chunkenizer import recursive_character_splitting


class FaissStore:
    def __init__(
        self,
        chunk_size: int = 300,
        chunk_overlap: int = 20,
        settings: Settings | None = None,
    ):
        settings = settings or get_settings()
        self.store_dir = Path(settings.DATA_PATH) / "faiss_store"
        self._snapshot_repository = FaissSnapshotRepository(self.store_dir)

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.index: Any | None = None
        self.dimension: int | None = None
        self.chunk_metadata: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self._state_lock = threading.Lock()

    def _prepare_chunks(self, docs: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
        """
        批量切块
        输入：文档列表
        输出：所有切块的字面量列表、所有切块的元数据列表
        """
        all_chunks = []
        all_metadata = []

        for doc in docs:
            doc_id = doc["id"]
            title = doc["title"]
            content = doc["content"]
            source = doc.get("source", "")
            doc_metadata = doc.get("metadata", {})

            chunks = recursive_character_splitting(
                content,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )

            for i, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue

                all_chunks.append(chunk)
                all_metadata.append(
                    normalize_chunk_metadata(
                        {
                            "doc_id": doc_id,
                            "title": title,
                            "source": source,
                            "chunk_text": chunk,
                            "chunk_index": i,
                            "metadata": doc_metadata,
                        },
                        doc,
                    )
                )

        return all_chunks, all_metadata

    def _next_doc_id(self) -> int:
        max_id = 0
        for doc in self.documents:
            doc_id = doc.get("id")
            if isinstance(doc_id, int):
                max_id = max(max_id, doc_id)
            elif isinstance(doc_id, str) and doc_id.isdigit():
                max_id = max(max_id, int(doc_id))
        return max_id + 1

    def _prepare_documents(
        self,
        docs: list[dict[str, Any]],
        *,
        first_id: int,
    ) -> list[dict[str, Any]]:
        new_docs: list[dict[str, Any]] = []
        for offset, doc in enumerate(docs):
            raw_doc = {
                "id": first_id + offset,
                "title": doc["title"],
                "content": doc["content"],
                "source": doc.get("source", ""),
                "metadata": doc.get("metadata", {}),
            }
            for key in ("user_id", "namespace", "category", "tags"):
                if doc.get(key) is not None:
                    raw_doc[key] = doc[key]
            new_docs.append(normalize_document(raw_doc))
        return new_docs

    def _index_with_chunks(
        self,
        chunks: list[str],
        *,
        existing_index: Any | None,
    ) -> tuple[Any, int]:
        try:
            embeddings = generate_embedding(chunks)
            vectors = np.ascontiguousarray(np.array(embeddings, dtype="float32"))
            if vectors.ndim != 2 or vectors.shape[0] != len(chunks) or vectors.shape[1] <= 0:
                raise ValidationError(
                    "The embedding response shape is invalid.",
                    code="embedding_shape_invalid",
                    dependency="embedding",
                    cause_type="EmbeddingShapeMismatch",
                )

            dimension = int(vectors.shape[1])
            candidate_index: Any
            if existing_index is None:
                candidate_index = faiss.IndexFlatL2(dimension)
            else:
                if int(existing_index.d) != dimension:
                    raise ValidationError(
                        "The vector dimension does not match the current index.",
                        code="vector_dimension_mismatch",
                        dependency="vector_index",
                        cause_type="VectorDimensionMismatch",
                    )
                # Mutate a clone so a failed append cannot corrupt the active index.
                candidate_index = faiss.clone_index(existing_index)

            candidate_index.add(vectors)
            return candidate_index, dimension
        except ApplicationError:
            raise
        except Exception as exc:
            raise classify_error(exc, dependency="vector_index") from exc

    def add_documents(self, docs: list[dict[str, Any]]) -> dict:
        with self._state_lock:
            new_docs = self._prepare_documents(docs, first_id=self._next_doc_id())
            chunks, metadata = self._prepare_chunks(new_docs)
            if not new_docs or not chunks:
                return {
                    "added_documents": 0,
                    "added_chunks": 0,
                }
            candidate_index, dimension = self._index_with_chunks(
                chunks,
                existing_index=self.index,
            )
            self.index = candidate_index
            self.dimension = dimension
            # Replace the paired collections together; readers can safely retain
            # the previous immutable-by-convention snapshot while this publishes.
            self.documents = [*self.documents, *new_docs]
            self.chunk_metadata = [*self.chunk_metadata, *metadata]
            return {
                "added_documents": len(new_docs),
                "added_chunks": len(metadata),
            }

    def add_document(self, title: str, content: str, source: str = "") -> dict:
        return self.add_documents([{"title": title, "content": content, "source": source}])

    def build_index(self, docs: list[dict[str, Any]]) -> dict:
        with self._state_lock:
            new_docs = self._prepare_documents(docs, first_id=1)
            chunks, metadata = self._prepare_chunks(new_docs)
            if not new_docs or not chunks:
                return {
                    "added_documents": 0,
                    "added_chunks": 0,
                }

            # Build an isolated candidate. The current state is replaced only after
            # embedding and FAISS construction have both completed successfully.
            candidate_index, dimension = self._index_with_chunks(
                chunks,
                existing_index=None,
            )
            self.index = candidate_index
            self.dimension = dimension
            self.documents = new_docs
            self.chunk_metadata = metadata
            return {
                "added_documents": len(new_docs),
                "added_chunks": len(metadata),
            }

    def search_related(self, query: str, k: int = 3) -> list[dict]:
        with self._state_lock:
            index = self.index
            chunk_metadata = self.chunk_metadata
            if index is None:
                raise DependencyUnavailable(
                    "The semantic search index is unavailable.",
                    code="vector_index_unavailable",
                    retryable=False,
                    dependency="vector_index",
                )

        try:
            query_embedding = generate_embedding(query)
            query_vector = np.ascontiguousarray(np.array([query_embedding], dtype="float32"))
            distances, indices = index.search(query_vector, k)
        except ApplicationError:
            raise
        except Exception as exc:
            raise classify_error(exc, dependency="vector_index") from exc

        results = []
        for distance, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue

            item = dict(chunk_metadata[idx])
            item["distance"] = float(distance)
            results.append(item)

        return results

    def read_documents(self, query: str) -> list[dict]:
        with self._state_lock:
            documents = self.documents
        res = []
        query_lower = query.lower()
        for doc in documents:
            if query_lower in doc["title"].lower() or query_lower in doc["content"].lower():
                res.append(doc)
        return res

    def save(self) -> bool:
        with self._state_lock:
            if self.index is None:
                return False
            documents, chunk_metadata = self._normalized_snapshot(
                self.documents,
                self.chunk_metadata,
            )
            self._snapshot_repository.save(
                self.index,
                documents,
                chunk_metadata,
            )
            self.documents = documents
            self.chunk_metadata = chunk_metadata
            return True

    def load(self) -> bool:
        with self._state_lock:
            snapshot = self._snapshot_repository.load()
            if snapshot is None:
                return False

            documents, chunk_metadata = self._normalized_snapshot(
                snapshot.documents,
                snapshot.chunk_metadata,
            )
            self.index = snapshot.index
            self.dimension = int(snapshot.index.d)
            self.documents = documents
            self.chunk_metadata = chunk_metadata
            return True

    def normalize_metadata(self) -> None:
        with self._state_lock:
            documents, chunk_metadata = self._normalized_snapshot(
                self.documents,
                self.chunk_metadata,
            )
            self.documents = documents
            self.chunk_metadata = chunk_metadata

    @staticmethod
    def _normalized_snapshot(
        documents: list[dict[str, Any]],
        chunk_metadata: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        normalized_documents = [normalize_document(doc) for doc in documents]
        documents_by_id = {str(doc.get("id")): doc for doc in normalized_documents}
        normalized_chunks = []
        for chunk in chunk_metadata:
            document = documents_by_id.get(str(chunk.get("doc_id")))
            normalized_chunks.append(normalize_chunk_metadata(chunk, document))
        return normalized_documents, normalized_chunks


if __name__ == "__main__":
    docs = [
        {
            "title": "LangGraph StateGraph",
            "content": "StateGraph 是 LangGraph 的核心类，用于构建状态驱动工作流。",
            "source": "demo",
        },
        {"title": "LangChain Chain", "content": "Chain 更适合线性流程。", "source": "demo"},
    ]

    store = FaissStore()
    store.build_index(docs)
    print(store.search_related("StateGraph 是做什么的", k=2))
