import json
import faiss
import numpy as np
from pathlib import Path
from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.services.embedding import generate_embedding
from tech_doc_agent.app.services.vectordb.chunkenizer import recursive_character_splitting


class FaissStore:
    def __init__(self, chunk_size: int = 300, chunk_overlap: int = 20):
        settings = get_settings()
        self.store_dir = Path(settings.DATA_PATH) / "faiss_store"
        self.index_path = self.store_dir / "index.faiss"
        self.documents_path = self.store_dir / "documents.json"
        self.metadata_path = self.store_dir / "chunk_metadata.json"

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.index = None
        self.dimension = None
        self.chunk_metadata = []
        self.documents = []

    def _prepare_chunks(self, docs: list[dict]) -> tuple[list[str], list[dict]]:
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

            chunks = recursive_character_splitting(
                content,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )

            for i, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue

                all_chunks.append(chunk)
                all_metadata.append({
                    "doc_id": doc_id,
                    "title": title,
                    "source": source,
                    "chunk_text": chunk,
                    "chunk_index": i,
                })
        
        return all_chunks, all_metadata
    
    def _ensure_index(self, dimension) -> None:
        if self.index is not None:
            return
        self.dimension = dimension
        self.index = faiss.IndexFlatL2(self.dimension)
    
    def add_documents(self, docs: list[dict]) -> dict:
        new_docs = []
        for doc in docs:
            normalized_doc = {
                "id": len(self.documents) + len(new_docs) + 1,
                "title": doc["title"],
                "content": doc["content"],
                "source": doc.get("source", "")
            }
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


    def build_index(self, docs: list[dict]) -> dict:
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
        if self.index is None:
            return False
        
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
        self.index = faiss.read_index(str(self.index_path))
        self.dimension = self.index.d
        with open(self.documents_path, "r", encoding="utf-8") as f:
            self.documents = json.load(f)
        with open(self.metadata_path, "r", encoding="utf-8") as f:
            self.chunk_metadata = json.load(f)
        return True



if __name__ == "__main__":
    docs = [
        {"title": "LangGraph StateGraph", "content": "StateGraph 是 LangGraph 的核心类，用于构建状态驱动工作流。", "source": "demo"},
        {"title": "LangChain Chain", "content": "Chain 更适合线性流程。", "source": "demo"},
    ]

    store = FaissStore()
    store.build_index(docs)
    print(store.search_related("StateGraph 是做什么的", k=2))
