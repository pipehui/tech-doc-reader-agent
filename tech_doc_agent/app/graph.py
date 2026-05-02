from typing import Literal
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import tools_condition
from langchain_core.runnables import RunnableConfig
from tech_doc_agent.app.core.state import State
from tech_doc_agent.app.core.tenant import tenant_from_values
from tech_doc_agent.app.services.utils import (
    create_tool_node_with_fallback,
    create_entry_node,
    create_exit_node,
    create_finish_node,
    store_plan,
)
from tech_doc_agent.app.services.message_scope import build_scoped_state, should_route_to_examination
from tech_doc_agent.app.services.assistants.assistant_base import (
    CompleteOrEscalate,
)
from tech_doc_agent.app.services.assistants.primary_assistant import (
    primary_assistant,
    primary_assistant_tools,
    primary_assistant_sensitive_tools,
    PlanWorkflow,
    ToDocParserAssistant,
    ToExplanationAssistant,
    ToRelationAssistant,
    ToExaminationAssistant,
    ToSummaryAssistant,
)
from tech_doc_agent.app.services.assistants.parser_assistant import (
    parser_assistant,
    parser_assistant_safe_tools,
    parser_assistant_sensitive_tools,
)
from tech_doc_agent.app.services.assistants.explanation_assistant import (
    explanation_assistant,
    explanation_assistant_safe_tools,
)
from tech_doc_agent.app.services.assistants.relation_assistant import (
    relation_assistant,
    relation_assistant_safe_tools,
)
from tech_doc_agent.app.services.assistants.examination_assistant import (
    examination_assistant,
    examination_assistant_safe_tools,
    examination_assistant_sensitive_tools,
)
from tech_doc_agent.app.services.assistants.summary_assistant import (
    summary_assistant,
    summary_assistant_safe_tools,
    summary_assistant_sensitive_tools,
)
from tech_doc_agent.app.services.user_profile import get_user_context_summary

def assistant_node(assistant, scoped_messages: bool = False):
    def invoke(state: State, config: RunnableConfig | None = None):
        assistant_state = build_scoped_state(state, assistant.name) if scoped_messages else state
        return assistant(assistant_state, config)

    async def ainvoke(state: State, config: RunnableConfig | None = None):
        assistant_state = build_scoped_state(state, assistant.name) if scoped_messages else state
        return await assistant.ainvoke(assistant_state, config)

    return RunnableLambda(invoke, afunc=ainvoke, name=assistant.name)

def route_next_step(state: State) -> Literal[
    "enter_parser",
    "enter_relation",
    "enter_explanation",
    "enter_examination",
    "enter_summary",
    "__end__",
]:
    plan = state.get("workflow_plan", [])
    index = state.get("plan_index", 0)

    if index >= len(plan):
        return END

    step = plan[index]

    if step == "parser":
        return "enter_parser"
    if step == "relation":
        return "enter_relation"
    if step == "explanation":
        return "enter_explanation"
    if step == "examination":
        return "enter_examination"
    if step == "summary":
        return "enter_summary"

    return END

def route_after_user_info(state: State) -> Literal[
    "enter_examination",
    "primary_assistant",
]:
    if should_route_to_examination(state):
        return "enter_examination"
    return "primary_assistant"

# Initialize the graph
builder = StateGraph(State)

def user_info(state: State, config: RunnableConfig):
    metadata = (config or {}).get("metadata", {}) if isinstance(config, dict) else {}
    tenant = tenant_from_values(
        state.get("user_id") or metadata.get("user_id"),
        state.get("namespace") or metadata.get("namespace"),
    )
    info_str = get_user_context_summary(
        user_id=tenant.user_id,
        namespace=tenant.namespace,
        memory_query=state.get("learning_target", ""),
    )
    update = {
        "user_info": info_str,
        "user_id": tenant.user_id,
        "namespace": tenant.namespace,
        "learning_target": state.get("learning_target", ""),
    }

    if state.get("examination_context") and not _last_ai_was_examination(state):
        update["examination_context"] = ""

    return update


def _last_ai_was_examination(state: State) -> bool:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", None) == "ai":
            return getattr(message, "name", None) == "examination"
    return False

builder.add_node("fetch_user_info", user_info)
builder.add_edge(START, "fetch_user_info")

# Parser Assitant
builder.add_node(
    "enter_parser",
    create_entry_node("Parser Assistant", "parser"),
)

builder.add_node("parser", assistant_node(parser_assistant, scoped_messages=True))
builder.add_edge("enter_parser", "parser")
builder.add_node(
    "parser_assistant_safe_tools",
    create_tool_node_with_fallback(parser_assistant_safe_tools),
)
builder.add_node(
    "parser_assistant_sensitive_tools",
    create_tool_node_with_fallback(parser_assistant_sensitive_tools),
)
builder.add_node("leave_parser", create_exit_node())
builder.add_edge("leave_parser", "primary_assistant")
builder.add_node("finish_parser", create_finish_node("parser_result", structured_kind="parser"))
builder.add_conditional_edges(
    "finish_parser",
    route_next_step,
    {
        "enter_parser": "enter_parser",
        "enter_relation": "enter_relation",
        "enter_explanation": "enter_explanation",
        "enter_examination": "enter_examination",
        "enter_summary": "enter_summary",
        END: END,
    },
)
def route_parser(state: State) -> Literal[
    "parser_assistant_safe_tools",
    "parser_assistant_sensitive_tools",
    "leave_parser",     # 任务异常中止，交给primary重新路由
    "finish_parser",    # 任务正常完成，路由进plan的下个agent
]:
    route = tools_condition(state)
    if route == END:
        return "finish_parser"
    tool_calls = state["messages"][-1].tool_calls
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel:
        return "leave_parser"
    safe_toolnames = [t.name for t in parser_assistant_safe_tools]
    if all(tc["name"] in safe_toolnames for tc in tool_calls):
        return "parser_assistant_safe_tools"
    return "parser_assistant_sensitive_tools"

builder.add_edge("parser_assistant_safe_tools", "parser")
builder.add_edge("parser_assistant_sensitive_tools", "parser")
builder.add_conditional_edges("parser", route_parser)

# Explanation Assistant
builder.add_node(
    "enter_explanation",
    create_entry_node("Explanation Assitant", "explanation"),
)

builder.add_node("explanation", assistant_node(explanation_assistant, scoped_messages=True))
builder.add_edge("enter_explanation", "explanation")
builder.add_node(
    "explanation_assistant_safe_tools",
    create_tool_node_with_fallback(explanation_assistant_safe_tools),
)
builder.add_node("leave_explanation", create_exit_node())
builder.add_edge("leave_explanation", "primary_assistant")

builder.add_node("finish_explanation", create_finish_node())
builder.add_conditional_edges(
    "finish_explanation",
    route_next_step,
    {
        "enter_parser": "enter_parser",
        "enter_relation": "enter_relation",
        "enter_explanation": "enter_explanation",
        "enter_examination": "enter_examination",
        "enter_summary": "enter_summary",
        END: END,
    },
)
def route_explanation(state: State) -> Literal[
    "explanation_assistant_safe_tools",
    "leave_explanation",
    "finish_explanation",
]:
    route = tools_condition(state)
    if route == END:
        return "finish_explanation"
    tool_calls = state["messages"][-1].tool_calls
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel:
        return "leave_explanation"
    return "explanation_assistant_safe_tools"

builder.add_edge("explanation_assistant_safe_tools", "explanation")
builder.add_conditional_edges("explanation", route_explanation)

# Relation Assitant
builder.add_node(
    "enter_relation",
    create_entry_node("Relation Assitant", "relation"),
)

builder.add_node("relation", assistant_node(relation_assistant, scoped_messages=True))
builder.add_edge("enter_relation", "relation")
builder.add_node(
    "relation_assistant_safe_tools",
    create_tool_node_with_fallback(relation_assistant_safe_tools),
)
builder.add_node("leave_relation", create_exit_node())
builder.add_edge("leave_relation", "primary_assistant")

builder.add_node("finish_relation", create_finish_node("relation_result", structured_kind="relation"))
builder.add_conditional_edges(
    "finish_relation",
    route_next_step,
    {
        "enter_parser": "enter_parser",
        "enter_relation": "enter_relation",
        "enter_explanation": "enter_explanation",
        "enter_examination": "enter_examination",
        "enter_summary": "enter_summary",
        END: END,
    },
)
def route_relation(state: State) -> Literal[
    "relation_assistant_safe_tools",
    "leave_relation",
    "finish_relation"
]:
    route = tools_condition(state)
    if route == END:
        return "finish_relation"
    tool_calls = state["messages"][-1].tool_calls
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel:
        return "leave_relation"
    return "relation_assistant_safe_tools"

builder.add_edge("relation_assistant_safe_tools", "relation")
builder.add_conditional_edges("relation", route_relation)

# Examination Assitant
builder.add_node(
    "enter_examination",
    create_entry_node("Examination Assitant", "examination"),
)

builder.add_node("examination", assistant_node(examination_assistant, scoped_messages=True))
builder.add_edge("enter_examination", "examination")
builder.add_node(
    "examination_assistant_safe_tools",
    create_tool_node_with_fallback(examination_assistant_safe_tools),
)
builder.add_node(
    "examination_assistant_sensitive_tools",
    create_tool_node_with_fallback(examination_assistant_sensitive_tools),
)
builder.add_node("leave_examination", create_exit_node())
builder.add_edge("leave_examination", "primary_assistant")

builder.add_node("finish_examination", create_finish_node("examination_context"))
builder.add_conditional_edges(
    "finish_examination",
    route_next_step,
    {
        "enter_parser": "enter_parser",
        "enter_relation": "enter_relation",
        "enter_explanation": "enter_explanation",
        "enter_examination": "enter_examination",
        "enter_summary": "enter_summary",
        END: END,
    },
)
def route_examination(state: State) -> Literal[
    "examination_assistant_safe_tools",
    "examination_assistant_sensitive_tools",
    "leave_examination",
    "finish_examination",
]:
    route = tools_condition(state)
    if route == END:
        return "finish_examination"
    tool_calls = state["messages"][-1].tool_calls
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel:
        return "leave_examination"
    safe_toolnames = [t.name for t in examination_assistant_safe_tools]
    if all(tc["name"] in safe_toolnames for tc in tool_calls):
        return "examination_assistant_safe_tools"
    return "examination_assistant_sensitive_tools"

builder.add_edge("examination_assistant_safe_tools", "examination")
builder.add_edge("examination_assistant_sensitive_tools", "examination")
builder.add_conditional_edges("examination", route_examination)

# Summary Assitant
builder.add_node(
    "enter_summary",
    create_entry_node("Summary Assitant", "summary"),
)

builder.add_node("summary", assistant_node(summary_assistant))
builder.add_edge("enter_summary", "summary")
builder.add_node(
    "summary_assistant_safe_tools",
    create_tool_node_with_fallback(summary_assistant_safe_tools),
)
builder.add_node(
    "summary_assistant_sensitive_tools",
    create_tool_node_with_fallback(summary_assistant_sensitive_tools),
)
builder.add_node("leave_summary", create_exit_node())
builder.add_edge("leave_summary", "primary_assistant")

builder.add_node("finish_summary", create_finish_node())
builder.add_conditional_edges(
    "finish_summary",
    route_next_step,
    {
        "enter_parser": "enter_parser",
        "enter_relation": "enter_relation",
        "enter_explanation": "enter_explanation",
        "enter_examination": "enter_examination",
        "enter_summary": "enter_summary",
        END: END,
    },
)
def route_summary(state: State) -> Literal[
    "summary_assistant_safe_tools",
    "summary_assistant_sensitive_tools",
    "leave_summary",
    "finish_summary",
]:
    route = tools_condition(state)
    if route == END:
        return "finish_summary"
    tool_calls = state["messages"][-1].tool_calls
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel:
        return "leave_summary"
    safe_toolnames = [t.name for t in summary_assistant_safe_tools]
    if all(tc["name"] in safe_toolnames for tc in tool_calls):
        return "summary_assistant_safe_tools"
    return "summary_assistant_sensitive_tools"

builder.add_edge("summary_assistant_safe_tools", "summary")
builder.add_edge("summary_assistant_sensitive_tools", "summary")
builder.add_conditional_edges("summary", route_summary)

# Primary Assistant
builder.add_node("primary_assistant", assistant_node(primary_assistant))
builder.add_node(
  "primary_assistant_tools", create_tool_node_with_fallback(primary_assistant_tools)
)
builder.add_node(
  "primary_assistant_sensitive_tools", create_tool_node_with_fallback(primary_assistant_sensitive_tools)
)
builder.add_node("store_plan", store_plan)
builder.add_conditional_edges(
    "fetch_user_info",
    route_after_user_info,
    {
        "enter_examination": "enter_examination",
        "primary_assistant": "primary_assistant",
    },
)

def route_primary_assistant(state: State) -> Literal[
    "store_plan",
    "primary_assistant_tools",
    "primary_assistant_sensitive_tools",
    "enter_parser",
    "enter_explanation",
    "enter_relation",
    "enter_examination",
    "enter_summary",
    "__end__",
]:
    route = tools_condition(state)
    if route == END:
        return END
    tool_calls = state["messages"][-1].tool_calls
    unsafe_toolnames = [t.name for t in primary_assistant_sensitive_tools]
    if tool_calls:
        tool_name = tool_calls[0]["name"]
        if tool_name == PlanWorkflow.__name__:
            return "store_plan"
        elif tool_name == ToDocParserAssistant.__name__:
            return "enter_parser"
        elif tool_name == ToExplanationAssistant.__name__:
            return "enter_explanation"
        elif tool_name == ToRelationAssistant.__name__:
            return "enter_relation"
        elif tool_name == ToExaminationAssistant.__name__:
            return "enter_examination"
        elif tool_name == ToSummaryAssistant.__name__:
            return "enter_summary"
        elif tool_name in unsafe_toolnames:
            return "primary_assistant_sensitive_tools"
        else:
            return "primary_assistant_tools"
    return END

builder.add_conditional_edges(
    "primary_assistant",
    route_primary_assistant,
    {
        "store_plan": "store_plan",
        "enter_parser": "enter_parser",
        "enter_explanation": "enter_explanation",
        "enter_relation": "enter_relation",
        "enter_examination": "enter_examination",
        "enter_summary": "enter_summary",
        "primary_assistant_tools": "primary_assistant_tools",
        "primary_assistant_sensitive_tools": "primary_assistant_sensitive_tools",
        END: END,
    },
)
builder.add_conditional_edges(
    "store_plan",
    route_next_step,
    {
        "enter_parser": "enter_parser",
        "enter_relation": "enter_relation",
        "enter_explanation": "enter_explanation",
        "enter_examination": "enter_examination",
        "enter_summary": "enter_summary",
        END: END,
    },
)
builder.add_edge("primary_assistant_tools", "primary_assistant")
builder.add_edge("primary_assistant_sensitive_tools", "primary_assistant")

# Compile the graph with interrupts
interrupt_nodes = [
    "parser_assistant_sensitive_tools",
    "examination_assistant_sensitive_tools",
    "summary_assistant_sensitive_tools",
    "primary_assistant_sensitive_tools",
]

# memory = MemorySaver()
# multi_agentic_graph = builder.compile(
#     checkpointer=memory,
#     interrupt_before=interrupt_nodes,
# )

def build_multi_agentic_graph(checkpointer):
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_nodes,
    )
