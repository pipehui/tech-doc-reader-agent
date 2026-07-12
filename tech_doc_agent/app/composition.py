from __future__ import annotations

from typing import Any

from tech_doc_agent.app.graph.builder import build_multi_agentic_graph
from tech_doc_agent.app.graph.nodes import create_user_info_node
from tech_doc_agent.app.graph.specs import (
    AgentSpec,
    CompletionPolicy,
    GraphSpec,
    PrimarySpec,
    ToolExecutionPolicy,
    ToolPolicy,
)
from tech_doc_agent.app.services.assistants.model_factory import build_assistant_model_provider
from tech_doc_agent.app.services.assistants.registry import AssistantRegistry, build_assistant_registry
from tech_doc_agent.app.tools import ToolDependencies, build_tool_bundle


def build_graph_spec(resources: Any) -> GraphSpec:
    dependencies = ToolDependencies.from_container(resources)
    tools = build_tool_bundle(dependencies)
    models = build_assistant_model_provider(resources.settings)
    assistants = build_assistant_registry(models, tools)
    return _graph_spec_from_registry(assistants, resources)


def _graph_spec_from_registry(assistants: AssistantRegistry, resources: Any) -> GraphSpec:
    return GraphSpec(
        primary=PrimarySpec(
            assistant=assistants.primary.assistant,
            tools=ToolPolicy(
                safe=assistants.primary.safe_tools,
                sensitive=assistants.primary.sensitive_tools,
            ),
        ),
        subagents=(
            AgentSpec(
                key="parser",
                display_name="Parser Assistant",
                assistant=assistants.parser.assistant,
                tools=ToolPolicy(
                    safe=assistants.parser.safe_tools,
                    sensitive=assistants.parser.sensitive_tools,
                ),
                completion=CompletionPolicy(result_key="parser_result", structured_kind="parser"),
            ),
            AgentSpec(
                key="explanation",
                display_name="Explanation Assitant",
                assistant=assistants.explanation.assistant,
                tools=ToolPolicy(safe=assistants.explanation.safe_tools),
            ),
            AgentSpec(
                key="relation",
                display_name="Relation Assitant",
                assistant=assistants.relation.assistant,
                tools=ToolPolicy(safe=assistants.relation.safe_tools),
                completion=CompletionPolicy(result_key="relation_result", structured_kind="relation"),
            ),
            AgentSpec(
                key="examination",
                display_name="Examination Assitant",
                assistant=assistants.examination.assistant,
                tools=ToolPolicy(
                    safe=assistants.examination.safe_tools,
                    sensitive=assistants.examination.sensitive_tools,
                ),
                completion=CompletionPolicy(result_key="examination_context"),
            ),
            AgentSpec(
                key="summary",
                display_name="Summary Assitant",
                assistant=assistants.summary.assistant,
                tools=ToolPolicy(
                    safe=assistants.summary.safe_tools,
                    sensitive=assistants.summary.sensitive_tools,
                ),
                scoped_messages=False,
            ),
        ),
        user_info_node=create_user_info_node(resources.profile_service.context_summary),
        tool_execution_policy=ToolExecutionPolicy(
            max_identical_repeats=resources.settings.MAX_IDENTICAL_TOOL_REPEATS,
            parser_max_retrieval_calls=resources.settings.PARSER_MAX_RETRIEVAL_CALLS,
        ),
    )


def build_application_graph(checkpointer: Any, resources: Any):
    return build_multi_agentic_graph(checkpointer, build_graph_spec(resources))
