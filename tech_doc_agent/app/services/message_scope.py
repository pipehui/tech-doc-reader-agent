from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage

from tech_doc_agent.app.core.state import State


SCOPED_AGENT_NAMES = {"parser", "relation", "explanation", "examination"}

_HANDOFF_TOOL_BY_AGENT = {
    "parser": "ToDocParserAssistant",
    "relation": "ToRelationAssistant",
    "explanation": "ToExplanationAssistant",
    "examination": "ToExaminationAssistant",
}

_OPTION_ANSWER_RE = re.compile(
    r"(?:^|[\s,，;；])(?:\d{1,2}|[一二三四五六七八九十]+)\s*[-.:：、]?\s*[a-d](?=$|[\s,，;；])",
    re.IGNORECASE,
)


def build_scoped_state(state: State, agent_name: str | None) -> dict:
    if agent_name not in SCOPED_AGENT_NAMES:
        return state

    return {
        **state,
        "messages": build_scoped_messages(state, agent_name),
    }


def should_route_to_examination(state: State) -> bool:
    examination_context = str(state.get("examination_context", "")).strip()
    if not examination_context:
        return False
    user_query = _last_human_text(list(state.get("messages", [])))
    return _should_continue_examination(user_query)


def build_scoped_messages(state: State, agent_name: str) -> list:
    messages = list(state.get("messages", []))
    return [
        HumanMessage(content=_build_task_context(state, messages, agent_name)),
        *_current_agent_tool_history(messages, agent_name),
    ]


def _build_task_context(state: State, messages: list, agent_name: str) -> str:
    current_turn_start = _last_human_index(messages) + 1
    user_query = _last_human_text(messages)
    context: dict[str, Any] = {
        "current_agent": agent_name,
        "user_query": user_query,
        "learning_target": state.get("learning_target", ""),
        "workflow_plan": state.get("workflow_plan", []),
        "plan_index": state.get("plan_index", 0),
    }

    handoff_args = _latest_tool_args(
        messages,
        _HANDOFF_TOOL_BY_AGENT.get(agent_name, ""),
        start_index=current_turn_start,
    )
    if handoff_args:
        context["handoff_request"] = handoff_args

    plan_args = _latest_tool_args(messages, "PlanWorkflow", start_index=current_turn_start)
    if plan_args:
        context["workflow_request"] = plan_args

    user_info = str(state.get("user_info", "")).strip()
    if user_info:
        context["user_info"] = user_info

    parser_result = _clean_structured_result(state.get("parser_result"))
    if agent_name in {"relation", "explanation", "examination"} and parser_result:
        context["parser_result"] = parser_result

    relation_result = _clean_structured_result(state.get("relation_result"))
    if agent_name in {"explanation", "examination"} and relation_result:
        context["relation_result"] = relation_result

    examination_context = str(state.get("examination_context", "")).strip()
    if (
        agent_name == "examination"
        and examination_context
        and _should_include_examination_context(user_query, handoff_args)
    ):
        context["previous_examination_context"] = examination_context

    return (
        "你正在接收一个受控的任务视图，而不是完整对话历史。\n"
        "请只使用下面的结构化任务上下文、本步骤 state，以及当前消息列表中属于你自己的工具调用结果。\n"
        "不要依赖隐藏的 primary 消息、其他 agent 的原始聊天内容，或其他 agent 的工具结果。\n"
        "如果信息不足，请使用你当前可用的工具补充，或按你的角色规则退出。\n\n"
        f"{_to_json(context)}"
    )


def _current_agent_tool_history(messages: list, agent_name: str) -> list:
    start_index = _last_human_index(messages) + 1
    scoped: list = []
    allowed_tool_call_ids: set[str] = set()

    for message in messages[start_index:]:
        if _message_type(message) == "ai" and getattr(message, "name", None) == agent_name:
            tool_calls = list(getattr(message, "tool_calls", []) or [])
            if not tool_calls:
                continue

            scoped.append(message)
            allowed_tool_call_ids.update(
                str(tool_call.get("id"))
                for tool_call in tool_calls
                if tool_call.get("id")
            )
            continue

        if _message_type(message) == "tool":
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id and str(tool_call_id) in allowed_tool_call_ids:
                scoped.append(message)

    return scoped


def _latest_tool_args(messages: list, tool_name: str, start_index: int = 0) -> dict[str, Any]:
    if not tool_name:
        return {}

    for message in reversed(messages[start_index:]):
        if _message_type(message) != "ai":
            continue

        for tool_call in reversed(list(getattr(message, "tool_calls", []) or [])):
            if tool_call.get("name") == tool_name:
                args = tool_call.get("args", {})
                return args if isinstance(args, dict) else {"value": args}

    return {}


def _clean_structured_result(value: Any) -> Any:
    if not isinstance(value, dict) or not value:
        return value

    cleaned = dict(value)
    if cleaned.get("parsed") is True:
        cleaned.pop("raw_text", None)
    return cleaned


def _should_include_examination_context(user_query: str, handoff_args: dict[str, Any]) -> bool:
    handoff_text = _to_json(handoff_args).casefold() if handoff_args else ""
    if any(keyword in handoff_text for keyword in ("上一轮", "作答", "答案", "评分", "评估", "answer")):
        return True

    query = user_query.strip().casefold()
    if not query or _is_polite_closure(query):
        return False
    if _is_explicit_non_examination_request(query) or _is_new_examination_request(query):
        return False
    return True


def _should_continue_examination(user_query: str) -> bool:
    query = user_query.strip().casefold()
    if not query:
        return False

    if _is_polite_closure(query):
        return False

    if _is_explicit_non_examination_request(query):
        return False

    return True


def _is_polite_closure(query: str) -> bool:
    return query in {"谢谢", "谢谢你", "好的", "好", "ok", "先这样", "不用了"}


def _is_new_examination_request(query: str) -> bool:
    new_exam_keywords = (
        "出题",
        "出一道",
        "出几道",
        "再来一道",
        "再给我",
        "换一道",
        "新题",
        "下一题",
        "测一下",
        "测试一下",
        "考我",
        "练习题",
        "quiz",
    )
    return any(keyword in query for keyword in new_exam_keywords)


def _looks_like_option_answer(query: str) -> bool:
    return len(_OPTION_ANSWER_RE.findall(query)) >= 2


def _is_explicit_non_examination_request(query: str) -> bool:
    non_exam_keywords = (
        "帮我总结",
        "总结一下",
        "归档",
        "学习记录",
        "复习记录",
        "用户画像",
        "能力信息",
        "帮我解释",
        "解释一下",
        "帮我讲",
        "讲讲",
        "讲一下",
        "帮我理解",
        "我想理解",
        "解析一下",
        "请系统解析",
        "文档库",
        "沉淀到文档库",
        "写入文档库",
        "保存到文档库",
        "搜索",
        "查一下",
        "帮我学",
    )
    return any(keyword in query for keyword in non_exam_keywords)


def _last_human_index(messages: list) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if _message_type(messages[index]) == "human":
            return index
    return -1


def _last_human_text(messages: list) -> str:
    index = _last_human_index(messages)
    if index < 0:
        return ""
    return _message_text(messages[index])


def _message_type(message) -> str | None:
    if isinstance(message, tuple) and message:
        role = message[0]
        return {
            "user": "human",
            "human": "human",
            "assistant": "ai",
            "ai": "ai",
            "tool": "tool",
        }.get(role, role)
    return getattr(message, "type", None)


def _message_text(message) -> str:
    if isinstance(message, tuple):
        return str(message[1]) if len(message) > 1 else ""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()

    return str(content)


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
