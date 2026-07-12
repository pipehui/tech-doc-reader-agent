from __future__ import annotations

from typing import Any

from tech_doc_agent.app.graph.builder import build_multi_agentic_graph
from tech_doc_agent.app.graph.budgeting import WorkflowBudgetTracker
from tech_doc_agent.app.graph.context_metrics import ContextMetricsTracker
from tech_doc_agent.app.graph.context_compaction import ContextCompactor
from tech_doc_agent.app.core.context_compaction import build_context_compaction_policy
from tech_doc_agent.app.core.execution_budget import build_execution_budget
from tech_doc_agent.app.graph.nodes import create_user_info_node
from tech_doc_agent.app.graph.specs import (
    AgentSpec,
    CompletionPolicy,
    ExecutionPolicy,
    GraphSpec,
    PrimarySpec,
    ReflectionPolicy,
    ToolExecutionPolicy,
    ToolPolicy,
)
from tech_doc_agent.app.services.assistants.model_factory import build_assistant_model_provider
from tech_doc_agent.app.services.assistants.prompt_registry import build_prompt_registry
from tech_doc_agent.app.services.assistants.registry import AssistantRegistry, build_assistant_registry
from tech_doc_agent.app.services.conversation_summarizer import ExtractiveConversationSummarizer
from tech_doc_agent.app.tools import ToolDependencies, build_tool_bundle


def build_graph_spec(resources: Any) -> GraphSpec:
    dependencies = ToolDependencies.from_container(resources)
    tools = build_tool_bundle(dependencies)
    models = build_assistant_model_provider(resources.settings)
    prompts = build_prompt_registry()
    assistants = build_assistant_registry(models, tools, prompts)
    return _graph_spec_from_registry(assistants, resources)


def _graph_spec_from_registry(assistants: AssistantRegistry, resources: Any) -> GraphSpec:
    execution_budget = build_execution_budget(resources.settings)
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
        execution_policy=ExecutionPolicy(
            budget=execution_budget,
            tools=ToolExecutionPolicy(
                max_identical_repeats=resources.settings.MAX_IDENTICAL_TOOL_REPEATS,
                parser_max_retrieval_calls=resources.settings.PARSER_MAX_RETRIEVAL_CALLS,
            ),
            reflection=ReflectionPolicy(
                max_rounds=resources.settings.MAX_REFLECTION_ROUNDS,
            ),
        ),
        budget_tracker=WorkflowBudgetTracker(
            resources.model_price_table,
            execution_budget=execution_budget,
        ),
        context_tracker=ContextMetricsTracker(),
        context_compactor=ContextCompactor(
            policy=build_context_compaction_policy(resources.settings),
            summarizer=ExtractiveConversationSummarizer(),
        ),
    )


def build_application_graph(checkpointer: Any, resources: Any):
    return build_multi_agentic_graph(checkpointer, build_graph_spec(resources))
