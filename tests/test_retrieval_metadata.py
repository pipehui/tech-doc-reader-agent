from tech_doc_agent.app.services.retrieval.metadata import (
    DEFAULT_NAMESPACE,
    DEFAULT_USER_ID,
    metadata_matches,
    normalize_chunk_metadata,
    normalize_document,
    normalize_filter,
)


def test_normalize_document_backfills_default_metadata_and_infers_category():
    document = normalize_document(
        {
            "id": 1,
            "title": "RAG 基础中的 Hybrid Search（混合搜索）机制详解",
            "content": "Hybrid Search combines BM25 and vector search.",
            "source": "seed",
        }
    )

    assert document["metadata"]["user_id"] == DEFAULT_USER_ID
    assert document["metadata"]["namespace"] == DEFAULT_NAMESPACE
    assert document["metadata"]["category"] == "rag_basic"
    assert "rag_basic" in document["metadata"]["tags"]


def test_normalize_chunk_metadata_inherits_document_metadata():
    document = normalize_document(
        {
            "id": 1,
            "title": "FastAPI Depends 依赖注入机制详解",
            "content": "Depends supports dependency injection.",
            "metadata": {"category": "fastapi", "tags": ["depends"]},
        }
    )

    chunk = normalize_chunk_metadata({"doc_id": 1, "title": document["title"], "chunk_text": "Depends"}, document)

    assert chunk["metadata"]["category"] == "fastapi"
    assert chunk["category"] == "fastapi"
    assert chunk["tags"] == ["depends"]


def test_metadata_matches_scalar_and_tag_filters():
    document = normalize_document(
        {
            "title": "LangGraph StateGraph 核心机制详解",
            "content": "StateGraph",
            "metadata": {"category": "langgraph_core", "tags": ["stategraph", "workflow"]},
        }
    )

    assert metadata_matches(document, {"category": "langgraph_core"})
    assert metadata_matches(document, {"tags": ["stategraph"]})
    assert not metadata_matches(document, {"category": "fastapi"})
    assert not metadata_matches(document, {"tags": ["checkpoint"]})


def test_normalize_filter_maps_broad_category_aliases_to_tags():
    assert normalize_filter({"category": "RAG"}) == {"tags": ["rag"]}
    assert normalize_filter({"category": "RAG 有关的内容"}) == {"tags": ["rag"]}
    assert normalize_filter({"category": "LangGraph"}) == {"tags": ["langgraph"]}
    assert normalize_filter({"category": "RAG 基础"}) == {"category": "rag_basic"}


def test_metadata_matches_broad_rag_category_alias_against_rag_tags():
    document = normalize_document(
        {
            "title": "RAG 进阶中的 Recall@K 评估指标详解",
            "content": "Recall@K evaluates retriever quality.",
            "metadata": {"category": "rag_advanced", "tags": ["rag", "rag_advanced"]},
        }
    )

    assert metadata_matches(document, {"category": "RAG"})
