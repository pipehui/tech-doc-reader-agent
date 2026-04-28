'''
web_search -> 在外部网络上搜索相关信息
read_docs -> 从文档数据库中读取与查询相关的文档
save_docs -> 将新的文档保存到文档数据库中
search_related_docs -> 使用向量索引搜索与查询相关的文档
'''
import json

from langchain_core.tools import tool

from tech_doc_agent.app.services.vectordb.faiss_store import FaissStore
from tech_doc_agent.app.services.vectordb.web_search_backend import WebSearchBackend
from tech_doc_agent.app.services.resources import get_app_resources


def get_faiss_store() -> FaissStore:
    return get_app_resources().faiss_store


def get_web_search_backend() -> WebSearchBackend:
    return get_app_resources().web_search_backend

@tool
def web_search(query: str) -> str:
    """
    在外部网络上搜索与查询相关的信息，并返回搜索结果列表。
    例如用户问'最新的AI技术有哪些'时，就用这个查询在网络上搜索相关信息，并返回搜索结果。
    """
    results = get_web_search_backend().search(query)
    return json.dumps(results, ensure_ascii=False)

@tool
def read_docs(query: str) -> str:
    """当需要查找已存储的技术文档内容时，根据关键词从知识库中检索匹配的文档。"""
    store = get_faiss_store()
    documents = store.read_documents(query)
    if documents:
        return json.dumps(documents, ensure_ascii=False)

    try:
        related_chunks = store.search_related(query, k=3)
    except Exception:
        related_chunks = []

    related_documents = [
        {
            "title": item.get("title", ""),
            "content": item.get("chunk_text", ""),
            "source": item.get("source", ""),
            "match_type": "semantic",
            "distance": item.get("distance"),
        }
        for item in related_chunks
    ]
    return json.dumps(related_documents, ensure_ascii=False)


@tool
def save_docs(title: str, content: str) -> str:
    """
    当需要将新的技术文档内容保存到知识库时，使用该工具将文档标题和内容存储起来。
    例如用户提供了一个新的文档标题和内容时，就调用这个工具进行保存。
    """
    store = get_faiss_store()
    result = store.add_document(title, content)
    store.save()
    return f"Document '{title}' has been saved successfully. Added {result['added_chunks']} chunks."

@tool
def search_related_docs(query: str, k: int) -> str:
    """
    使用向量索引搜索与查询语义相关的文档。
    例如用户问'LangGraph是什么'时，用'LangGraph'作为query进行相似度计算，找出最多k个相关文档。
    """
    try:
        results = get_faiss_store().search_related(query, k)
    except Exception:
        results = []
    return json.dumps(results, ensure_ascii=False)
