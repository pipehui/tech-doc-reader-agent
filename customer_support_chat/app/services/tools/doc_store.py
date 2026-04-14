'''
web_search -> 在外部网络上搜索相关信息
read_docs -> 从文档数据库中读取与查询相关的文档
save_docs -> 将新的文档保存到文档数据库中
search_related_docs -> 使用向量索引搜索与查询相关的文档
'''
import json
from customer_support_chat.app.core.settings import get_settings
from langchain_core.tools import tool
from typing import List, Dict, Optional, Union

settings = get_settings()

# 假设这是一个存储表
_doc_store: List[Dict] = [
    {"id": 1, "title": "LangGraph StateGraph", "content": "StateGraph 是 LangGraph 的核心类..."},
    {"id": 2, "title": "FastAPI 依赖注入", "content": "FastAPI 通过 Depends() 实现依赖注入..."},
]

@tool
def web_search(query: str) -> List[Dict]:
    """
    在外部网络上搜索与查询相关的信息，并返回搜索结果列表。
    例如用户问'最新的AI技术有哪些'时，就用这个查询在网络上搜索相关信息，并返回搜索结果。
    """
    # Implement your web search logic here
    results = [
        {"title": "Example Result 1", "url": "https://example.com/1", "snippet": "This is an example search result."},
        {"title": "Example Result 2", "url": "https://example.com/2", "snippet": "This is another example search result."},
    ]
    return results

@tool
def read_docs(query: str) -> str:
    """当需要查找已存储的技术文档内容时，根据关键词从知识库中检索匹配的文档。"""
    documents = []
    for doc in _doc_store:
        if query.lower() in doc["title"].lower() or query.lower() in doc["content"].lower():
            documents.append(doc)
    return json.dumps(documents, ensure_ascii=False)


@tool
def save_docs(title: str, content: str) -> str:
    """
    当需要将新的技术文档内容保存到知识库时，使用该工具将文档标题和内容存储起来。
    例如用户提供了一个新的文档标题和内容时，就调用这个工具进行保存。
    """
    # Implement your document saving logic here
    new_doc = {"id": len(_doc_store) + 1, "title": title, "content": content}
    _doc_store.append(new_doc)
    return f"Document '{title}' has been saved successfully."

@tool
def search_related_docs(query: str, k: int) -> List[Dict]:
    """
    使用向量索引搜索与查询语义相关的文档。
    例如用户问'LangGraph是什么'时，用'LangGraph'作为query进行相似度计算，找出最多k个相关文档。
    """
    # Implement your related document search logic here
    k = min(k, len(_doc_store))  # Ensure k does not exceed the number of documents
    related_docs = _doc_store[:k]  # Placeholder: return the first k documents
    return related_docs