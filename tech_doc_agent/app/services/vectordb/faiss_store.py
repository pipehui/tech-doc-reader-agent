import json
import threading

import faiss
import numpy as np
from pathlib import Path
from typing import Any

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.services.embedding import generate_embedding
from tech_doc_agent.app.services.retrieval.metadata import normalize_chunk_metadata, normalize_document
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
        self.index_path = self.store_dir / "index.faiss"
        self.documents_path = self.store_dir / "documents.json"
        self.metadata_path = self.store_dir / "chunk_metadata.json"

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.index: Any | None = None
        self.dimension: int | None = None
        self.chunk_metadata: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self._write_lock = threading.Lock()

    def _prepare_chunks(self, docs: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
        '''
        批量切块
        输入：文档列表
        输出：所有切块的字面量列表、所有切块的元数据列表
        '''
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
    
    def _ensure_index(self, dimension: int) -> None:
        if self.index is not None:
            return
        self.dimension = dimension
        self.index = faiss.IndexFlatL2(self.dimension)
    
    def _next_doc_id(self) -> int:
        max_id = 0
        for doc in self.documents:
            doc_id = doc.get("id")
            if isinstance(doc_id, int):
                max_id = max(max_id, doc_id)
            elif isinstance(doc_id, str) and doc_id.isdigit():
                max_id = max(max_id, int(doc_id))
        return max_id + 1

    def add_documents(self, docs: list[dict[str, Any]]) -> dict:
        with self._write_lock:
            new_docs: list[dict[str, Any]] = []
            next_id = self._next_doc_id()
            for offset, doc in enumerate(docs):
                raw_doc = {
                    "id": next_id + offset,
                    "title": doc["title"],
                    "content": doc["content"],
                    "source": doc.get("source", ""),
                    "metadata": doc.get("metadata", {}),
                }
                for key in ("user_id", "namespace", "category", "tags"):
                    if doc.get(key) is not None:
                        raw_doc[key] = doc[key]
                normalized_doc = normalize_document(raw_doc)
                new_docs.append(normalized_doc)
            chunks, metadata = self._prepare_chunks(new_docs)
            if not new_docs or not chunks:
                return {
                    "added_documents": 0,
                    "added_chunks": 0,
                }
            embeddings = generate_embedding(chunks)
            vectors = np.ascontiguousarray(np.array(embeddings, dtype="float32"))

            self._ensure_index(vectors.shape[1])
            assert self.index is not None
            self.index.add(vectors)
            self.documents.extend(new_docs)
            self.chunk_metadata.extend(metadata)
            return {
                "added_documents": len(new_docs),
                "added_chunks": len(metadata),
            }
    
    def add_document(self, title: str, content: str, source: str = "") -> dict:
        return self.add_documents([
            {"title": title, "content": content, "source": source}
        ])


    def build_index(self, docs: list[dict[str, Any]]) -> dict:
        self.index = None
        self.dimension = None
        self.documents = []
        self.chunk_metadata = []

        return self.add_documents(docs)


    def search_related(self, query: str, k: int = 3) -> list[dict]:
        if self.index is None:
            raise ValueError("FAISS index has not been built yet.")
        
        query_embedding = generate_embedding(query)
        query_vector = np.ascontiguousarray(np.array([query_embedding], dtype="float32"))

        distances, indices = self.index.search(query_vector, k)

        results = []
        for distance, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue

            item = dict(self.chunk_metadata[idx])
            item["distance"] = float(distance)
            results.append(item)

        return results
    
    def read_documents(self, query: str) -> list[dict]:
        res = []
        query_lower = query.lower()
        for doc in self.documents:
            if query_lower in doc["title"].lower() or query_lower in doc["content"].lower():
                res.append(doc)
        return res

    def save(self) -> bool:
        with self._write_lock:
            if self.index is None:
                return False
            self.normalize_metadata()

            self.store_dir.mkdir(parents=True, exist_ok=True)
            faiss.write_index(self.index, str(self.index_path))
            with open(self.documents_path, "w", encoding="utf-8") as f:
                json.dump(self.documents, f, ensure_ascii=False, indent=2)

            with open(self.metadata_path, "w", encoding="utf-8") as f:
                json.dump(self.chunk_metadata, f, ensure_ascii=False, indent=2)

            return True

    def load(self) -> bool:
        if not (
            self.index_path.exists()
            and self.documents_path.exists()
            and self.metadata_path.exists()
        ):
            return False
        index = faiss.read_index(str(self.index_path))
        self.index = index
        self.dimension = index.d
        with open(self.documents_path, "r", encoding="utf-8") as f:
            self.documents = json.load(f)
        with open(self.metadata_path, "r", encoding="utf-8") as f:
            self.chunk_metadata = json.load(f)
        self.normalize_metadata()
        return True

    def normalize_metadata(self) -> None:
        self.documents = [normalize_document(doc) for doc in self.documents]
        documents_by_id = {str(doc.get("id")): doc for doc in self.documents}
        normalized_chunks = []
        for chunk in self.chunk_metadata:
            document = documents_by_id.get(str(chunk.get("doc_id")))
            normalized_chunks.append(normalize_chunk_metadata(chunk, document))
        self.chunk_metadata = normalized_chunks



if __name__ == "__main__":
    docs = [
        {"title": "LangGraph StateGraph", "content": "StateGraph 是 LangGraph 的核心类，用于构建状态驱动工作流。", "source": "demo"},
        {"title": "LangChain Chain", "content": "Chain 更适合线性流程。", "source": "demo"},
    ]

    store = FaissStore()
    store.build_index(docs)
    print(store.search_related("StateGraph 是做什么的", k=2))
