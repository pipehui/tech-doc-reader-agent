'''
read_learning_history -> 从学习记录中读取与查询相关的历史学习记录
read_all_learning_history -> 获取用户的所有学习记录概览
upsert_learning_history -> 将新的学习记录保存到本地，或者更新已有的学习记录
read_user_memory -> 读取长期学习轨迹记忆
upsert_learning_state -> 合并更新学习记录和长期学习轨迹记忆
'''
import json
from typing import Optional

from langchain_core.tools import tool

from tech_doc_agent.app.core.observability import get_trace_context
from tech_doc_agent.app.core.tenant import current_tenant
from tech_doc_agent.app.services.vectordb.learning_store_backend import LearningStore
from tech_doc_agent.app.services.vectordb.memory_store_backend import MemoryStore
from tech_doc_agent.app.services.resources import get_app_resources


def get_learning_store() -> LearningStore:
    return get_app_resources().learning_store


def get_memory_store() -> MemoryStore:
    return get_app_resources().memory_store

@tool
def read_learning_history(query: str) -> str:
    """
    读取学习记录中与查询相关的历史学习记录。
    这个工具返回的是轻量记录，只包含 knowledge、timestamp、score、reviewtimes 等信息，
    用来判断用户是否学过某个知识点、掌握情况如何、复习过几次。
    它不包含该知识点的详细技术内容、完整定义、机制说明或代码示例。
    如果需要详细内容，应从文档工具中读取，而不是依赖学习记录。
    """
    tenant = current_tenant()
    history = get_learning_store().read_by_query(
        query,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
    )
    return json.dumps(history, ensure_ascii=False)

@tool
def read_all_learning_history() -> str:
    """
    读取所有的学习记录概览，供 relation 助手评估用户整体学过哪些知识点。
    返回的仍然只是轻量记录，不是详细知识正文。
    如果需要理解某个知识点的具体内容，应再去读取文档。
    """
    tenant = current_tenant()
    all_history = get_learning_store().read_overview(
        user_id=tenant.user_id,
        namespace=tenant.namespace,
    )
    return json.dumps(all_history, ensure_ascii=False)


@tool
def read_user_memory(query: str = "", limit: int = 5) -> str:
    """
    读取当前用户长期学习轨迹记忆。
    记忆是对学习过程的轻量观察，例如曾经卡住的点、纠正过的误解、复习提示等。
    它不是稳定用户偏好，也不是用户画像；如果要更新长期偏好，必须由用户主动请求。
    """
    tenant = current_tenant()
    memories = get_memory_store().read_by_query(
        query,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
        limit=limit,
    )
    return json.dumps(memories, ensure_ascii=False)

@tool
def upsert_learning_history(knowledge: str, timestamp: str, score: Optional[float] = None) -> str:
    """
    将学习记录写入本地存储中，如果该知识点已经存在则更新其时间戳和评分，并将复习次数加一。
    这个工具保存的是学习记录，不保存详细的学习内容正文、完整总结或文档内容。
    """
    tenant = current_tenant()
    store = get_learning_store()
    message = store.upsert_record(
        knowledge,
        timestamp,
        score,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
    )
    store.save()
    return message


@tool
def upsert_learning_state(
    knowledge: str,
    timestamp: str,
    score: Optional[float] = None,
    memory_kind: Optional[str] = None,
    memory_topic: Optional[str] = None,
    memory_content: Optional[str] = None,
    memory_confidence: Optional[float] = None,
) -> str:
    """
    合并更新当前用户的学习状态。
    这个工具会更新轻量学习记录，并可选写入一条长期学习轨迹记忆。
    memory 只记录本轮学习观察，例如 learned、stuck_point、misconception、review_hint；
    不要用它更新用户长期偏好或能力画像。
    """
    tenant = current_tenant()
    learning_store = get_learning_store()
    learning_message = learning_store.upsert_record(
        knowledge,
        timestamp,
        score,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
    )
    learning_store.save()

    memory_message = "No memory fragment written."
    if memory_content and memory_content.strip():
        memory_store = get_memory_store()
        memory = memory_store.upsert_memory(
            kind=memory_kind or "learned",
            topic=memory_topic or knowledge,
            content=memory_content,
            confidence=memory_confidence,
            source_session_id=_current_session_id(),
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
        memory_store.save()
        memory_message = f"Memory '{memory['id']}' has been upserted."

    return f"{learning_message} {memory_message}"


def _current_session_id() -> str | None:
    session_id = get_trace_context().get("session_id")
    if session_id is None:
        return None
    text = str(session_id).strip()
    return text or None
