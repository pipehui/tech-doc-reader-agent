'''
web_search -> 在外部网络上搜索相关信息
read_docs -> 从文档数据库中读取与查询相关的文档
save_docs -> 将新的文档保存到文档数据库中
search_related_docs -> 使用向量索引搜索与查询相关的文档
'''
import json

from langchain_core.tools import tool

from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.services.retrieval import HybridRetriever
from tech_doc_agent.app.services.retrieval.metadata import normalize_filter
from tech_doc_agent.app.services.vectordb.faiss_store import FaissStore
from tech_doc_agent.app.services.vectordb.web_search_backend import WebSearchBackend
from tech_doc_agent.app.services.resources import get_app_resources


def get_faiss_store() -> FaissStore:
    return get_app_resources().faiss_store


def get_web_search_backend() -> WebSearchBackend:
    return get_app_resources().web_search_backend


def get_hybrid_retriever() -> HybridRetriever:
    resources = get_app_resources()
    retriever = getattr(resources, "hybrid_retriever", None)
    if retriever is not None:
        return retriever
    settings = getattr(resources, "settings", None) or get_settings()
    return HybridRetriever(resources.faiss_store, settings=settings)


@tool
def web_search(query: str) -> str:
    """
    在外部网络上搜索与查询相关的信息，并返回搜索结果列表。
    例如用户问'最新的AI技术有哪些'时，就用这个查询在网络上搜索相关信息，并返回搜索结果。
    """
    results = get_web_search_backend().search(query)
    return json.dumps(results, ensure_ascii=False)

def _build_filters(
    *,
    category: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
) -> dict:
    return normalize_filter(
        {
            "category": category,
            "tags": tags,
            "source": source,
        }
    )


@tool
def read_docs(
    query: str,
    category: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
) -> str:
    """
    当需要查找已存储的技术文档内容时，根据关键词从知识库中检索匹配的文档。
    文档库是共享知识库，不按当前用户隔离；可选传入 category、tags 或 source 来限制检索范围。
    category 只能使用内部标准分类；RAG、LangGraph 这类宽泛主题应使用 tags=["rag"] 或 tags=["langgraph"]，不要作为 category 传入。
    """
    filters = _build_filters(
        category=category,
        tags=tags,
        source=source,
    )
    documents = get_hybrid_retriever().search(query, filters=filters)
    return json.dumps(documents, ensure_ascii=False)


@tool
def save_docs(
    title: str,
    content: str,
    source: str = "",
    category: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """
    当需要将新的技术文档内容保存到知识库时，使用该工具将文档标题和内容存储起来。
    例如用户提供了一个新的文档标题和内容时，就调用这个工具进行保存。
    文档库是共享知识库，不按当前用户隔离；可选传入 category、tags 作为文档 metadata。
    """
    store = get_faiss_store()
    result = store.add_documents(
        [
            {
                "title": title,
                "content": content,
                "source": source,
                "category": category,
                "tags": tags,
            }
        ]
    )
    store.save()
    get_hybrid_retriever().refresh()
    return f"Document '{title}' has been saved successfully. Added {result['added_chunks']} chunks."

@tool
def search_related_docs(
    query: str,
    k: int,
    category: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
) -> str:
    """
    使用向量索引搜索与查询语义相关的文档。
    例如用户问'LangGraph是什么'时，用'LangGraph'作为query进行相似度计算，找出最多k个相关文档。
    文档库是共享知识库，不按当前用户隔离；可选传入 category、tags 或 source 来过滤结果。
    """
    try:
        filters = _build_filters(
            category=category,
            tags=tags,
            source=source,
        )
        results = get_hybrid_retriever().search(query, top_k=k, mode="vector", filters=filters)
    except Exception:
        results = []
    return json.dumps(results, ensure_ascii=False)
