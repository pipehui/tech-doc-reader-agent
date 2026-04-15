'''
web_search -> 在外部网络上搜索相关信息
read_docs -> 从文档数据库中读取与查询相关的文档
save_docs -> 将新的文档保存到文档数据库中
search_related_docs -> 使用向量索引搜索与查询相关的文档
'''
import json
from customer_support_chat.app.core.settings import get_settings
from customer_support_chat.app.services.vectordb.faiss_store import FaissStore
from langchain_core.tools import tool
from typing import List, Dict, Optional, Union

settings = get_settings()

# 假设这是一个存储表
_seed_docs = [
    {"title": "LangGraph StateGraph", "content": "StateGraph 是 LangGraph 的核心类，用于构建状态驱动的多Agent工作流。它通过节点和边定义工作流图结构，支持条件分支和循环。", "source": "seed"},
    {"title": "FastAPI 依赖注入", "content": "FastAPI 通过 Depends() 实现依赖注入，支持嵌套依赖、生命周期管理和异步处理。", "source": "seed"},
    {"title": "RAG 检索增强生成", "content": "RAG 将检索系统与生成模型结合，先从知识库中检索相关文档片段，再将其作为上下文输入给LLM生成回答。", "source": "seed"},
]
_faiss_store = FaissStore()
if not _faiss_store.load():
    _faiss_store.build_index(_seed_docs)
    _faiss_store.save()

@tool
def web_search(query: str) -> str:
    """
    在外部网络上搜索与查询相关的信息，并返回搜索结果列表。
    例如用户问'最新的AI技术有哪些'时，就用这个查询在网络上搜索相关信息，并返回搜索结果。
    """
    # Implement your web search logic here
    results = [
        {"title": "Example Result 1", "url": "https://example.com/1", "snippet": "This is an example search result."},
        {"title": "Example Result 2", "url": "https://example.com/2", "snippet": "This is another example search result."},
    ]
    return json.dumps(results, ensure_ascii=False)

@tool
def read_docs(query: str) -> str:
    """当需要查找已存储的技术文档内容时，根据关键词从知识库中检索匹配的文档。"""
    documents = _faiss_store.read_documents(query)
    return json.dumps(documents, ensure_ascii=False)


@tool
def save_docs(title: str, content: str) -> str:
    """
    当需要将新的技术文档内容保存到知识库时，使用该工具将文档标题和内容存储起来。
    例如用户提供了一个新的文档标题和内容时，就调用这个工具进行保存。
    """
    # Implement your document saving logic here
    result = _faiss_store.add_document(title, content)
    _faiss_store.save()
    return f"Document '{title}' has been saved successfully. Added {result['added_chunks']} chunks."

@tool
def search_related_docs(query: str, k: int) -> str:
    """
    使用向量索引搜索与查询语义相关的文档。
    例如用户问'LangGraph是什么'时，用'LangGraph'作为query进行相似度计算，找出最多k个相关文档。
    """
    # Implement your related document search logic here
    results = _faiss_store.search_related(query, k)
    return json.dumps(results, ensure_ascii=False)
