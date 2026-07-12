import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from tech_doc_agent.app.application.learning_models import LearningRecord, MemoryFragment
from tech_doc_agent.app.application.learning_state import UpdateLearningStateCommand
from tech_doc_agent.app.core.tenant import session_id_from_config, tenant_from_config
from tech_doc_agent.app.tools.dependencies import ToolDependencies


@dataclass(frozen=True, slots=True)
class LearningTools:
    read_learning_history: BaseTool
    read_all_learning_history: BaseTool
    read_user_memory: BaseTool
    upsert_learning_history: BaseTool
    upsert_learning_state: BaseTool


def _serialize_records(records: Sequence[LearningRecord]) -> str:
    return json.dumps(
        [record.to_payload() for record in records],
        ensure_ascii=False,
    )


def _serialize_memories(memories: Sequence[MemoryFragment]) -> str:
    return json.dumps(
        [memory.to_payload() for memory in memories],
        ensure_ascii=False,
    )


def build_learning_tools(dependencies: ToolDependencies) -> LearningTools:
    @tool
    def read_learning_history(query: str, config: RunnableConfig) -> str:
        """
        读取学习记录中与查询相关的历史学习记录。
        这个工具返回的是轻量记录，只包含 knowledge、timestamp、score、reviewtimes 等信息，
        用来判断用户是否学过某个知识点、掌握情况如何、复习过几次。
        它不包含该知识点的详细技术内容、完整定义、机制说明或代码示例。
        如果需要详细内容，应从文档工具中读取，而不是依赖学习记录。
        """

        tenant = tenant_from_config(config)
        history = dependencies.learning_store.query_records(
            query,
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
        return _serialize_records(history)

    @tool
    def read_all_learning_history(config: RunnableConfig) -> str:
        """
        读取所有的学习记录概览，供 relation 助手评估用户整体学过哪些知识点。
        返回的仍然只是轻量记录，不是详细知识正文。
        如果需要理解某个知识点的具体内容，应再去读取文档。
        """

        tenant = tenant_from_config(config)
        history = dependencies.learning_store.list_records(
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
        return _serialize_records(history)

    @tool
    def read_user_memory(config: RunnableConfig, query: str = "", limit: int = 5) -> str:
        """
        读取当前用户长期学习轨迹记忆。
        记忆是对学习过程的轻量观察，例如曾经卡住的点、纠正过的误解、复习提示等。
        它不是稳定用户偏好，也不是用户画像；如果要更新长期偏好，必须由用户主动请求。
        """

        tenant = tenant_from_config(config)
        memories = dependencies.memory_store.query_memories(
            query,
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            limit=limit,
        )
        return _serialize_memories(memories)

    @tool
    def upsert_learning_history(
        knowledge: str,
        timestamp: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
        score: Optional[float] = None,
    ) -> str:
        """
        将学习记录写入本地存储中，如果该知识点已经存在则更新其时间戳和评分，并将复习次数加一。
        这个工具保存的是学习记录，不保存详细的学习内容正文、完整总结或文档内容。
        """

        tenant = tenant_from_config(config)
        result = dependencies.learning_state_service.update(
            UpdateLearningStateCommand(
                tenant=tenant,
                session_id=session_id_from_config(config) or "",
                tool_call_id=tool_call_id,
                knowledge=knowledge,
                timestamp=timestamp,
                score=score,
            )
        )
        return result.learning_message

    @tool
    def upsert_learning_state(
        knowledge: str,
        timestamp: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
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

        tenant = tenant_from_config(config)
        result = dependencies.learning_state_service.update(
            UpdateLearningStateCommand(
                tenant=tenant,
                session_id=session_id_from_config(config) or "",
                tool_call_id=tool_call_id,
                knowledge=knowledge,
                timestamp=timestamp,
                score=score,
                memory_kind=memory_kind,
                memory_topic=memory_topic,
                memory_content=memory_content,
                memory_confidence=memory_confidence,
            )
        )
        return result.message

    return LearningTools(
        read_learning_history=read_learning_history,
        read_all_learning_history=read_all_learning_history,
        read_user_memory=read_user_memory,
        upsert_learning_history=upsert_learning_history,
        upsert_learning_state=upsert_learning_state,
    )
