'''
read_learning_history -> 从学习记录中读取与查询相关的历史学习记录
read_all_learning_history -> 获取用户的所有学习记录概览
upsert_learning_history -> 将新的学习记录保存到本地，或者更新已有的学习记录
'''
import json
from typing import Optional

from langchain_core.tools import tool

from tech_doc_agent.app.services.vectordb.learning_store_backend import LearningStore

_seed_learning_history = [
    {
        "knowledge": "LangGraph StateGraph",
        "timestamp": "2024-01-01T10:00:00Z",
        "score": 0.8,
        "reviewtimes": 1,
    },
    {
        "knowledge": "FastAPI 依赖注入",
        "timestamp": "2024-01-02T11:00:00Z",
        "score": 0.9,
        "reviewtimes": 2,
    },
]

_learning_store = LearningStore()
if not _learning_store.load():
    _learning_store.records = [dict(record) for record in _seed_learning_history]
    _learning_store.save()

@tool
def read_learning_history(query: str) -> str:
    """
    读取学习记录中与查询相关的历史学习记录。
    这个工具返回的是轻量记录，只包含 knowledge、timestamp、score、reviewtimes 等信息，
    用来判断用户是否学过某个知识点、掌握情况如何、复习过几次。
    它不包含该知识点的详细技术内容、完整定义、机制说明或代码示例。
    如果需要详细内容，应从文档工具中读取，而不是依赖学习记录。
    """
    history = _learning_store.read_by_query(query)
    return json.dumps(history, ensure_ascii=False)

@tool
def read_all_learning_history() -> str:
    """
    读取所有的学习记录概览，供 relation 助手评估用户整体学过哪些知识点。
    返回的仍然只是轻量记录，不是详细知识正文。
    如果需要理解某个知识点的具体内容，应再去读取文档。
    """
    all_history = _learning_store.read_overview()
    return json.dumps(all_history, ensure_ascii=False)

@tool
def upsert_learning_history(knowledge: str, timestamp: str, score: Optional[float] = None) -> str:
    """
    将学习记录写入本地存储中，如果该知识点已经存在则更新其时间戳和评分，并将复习次数加一。
    这个工具保存的是学习记录，不保存详细的学习内容正文、完整总结或文档内容。
    """
    message = _learning_store.upsert_record(knowledge, timestamp, score)
    _learning_store.save()
    return message
