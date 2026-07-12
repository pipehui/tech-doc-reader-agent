from tech_doc_agent.app.core.tenant import DEFAULT_NAMESPACE, DEFAULT_USER_ID
from tech_doc_agent.app.infrastructure.retrieval.filters import metadata_matches, normalize_filter
from tech_doc_agent.app.infrastructure.retrieval.inference import infer_category
from tech_doc_agent.app.infrastructure.retrieval.normalization import (
    normalize_chunk_metadata,
    normalize_document,
    normalize_metadata,
    normalize_tags,
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


def test_normalize_metadata_uses_nested_then_item_then_fallback_precedence():
    metadata = normalize_metadata(
        {
            "title": "FastAPI Depends",
            "category": "backend",
            "metadata": {"category": "fastapi", "tags": ["Depends"]},
        },
        fallback={
            "user_id": "fallback-user",
            "namespace": "fallback-ns",
            "category": "langgraph_core",
        },
    )

    assert metadata == {
        "user_id": "fallback-user",
        "namespace": "fallback-ns",
        "category": "fastapi",
        "tags": ["depends"],
    }


def test_normalize_filter_flattens_metadata_and_merges_normalized_tags():
    filters = normalize_filter(
        {
            "metadata": {"category": "RAG", "tags": ["Hybrid Search"]},
            "tags": {"BM25": True, "disabled": False},
            "source": "",
        }
    )

    assert filters == {"tags": ["bm25", "hybrid_search", "rag"]}


def test_category_inference_prioritizes_title_prefix_over_content_keywords():
    assert (
        infer_category(
            title="FastAPI Depends 依赖注入机制详解",
            content="This article also compares LangGraph StateGraph and reducer behavior.",
        )
        == "fastapi"
    )
    assert infer_category(title="Unknown", content="HNSW approximate nearest neighbor") == "vector_db"


def test_normalize_tags_accepts_mapping_and_deduplicates_casefolded_values():
    assert normalize_tags({"RAG": True, "rag": True, "Hybrid Search": True, "off": False}) == [
        "hybrid_search",
        "rag",
    ]


def test_metadata_matches_non_tag_filter_lists_as_allowed_values():
    document = normalize_document(
        {
            "title": "FastAPI Depends",
            "source": "seed",
            "metadata": {"category": "fastapi", "tags": ["depends", "dependency injection"]},
        }
    )

    assert metadata_matches(document, {"source": ["manual", "seed"]})
    assert metadata_matches(document, {"tags": ["depends", "dependency injection"]})
    assert not metadata_matches(document, {"tags": ["depends", "missing"]})


def test_legacy_metadata_facade_reexports_owning_helpers():
    from tech_doc_agent.app.infrastructure.retrieval import metadata

    assert metadata.normalize_document is normalize_document
    assert metadata.normalize_filter is normalize_filter
    assert metadata.infer_category is infer_category
