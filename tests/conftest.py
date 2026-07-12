import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from tech_doc_agent.app.graph.commands import (
    PlanWorkflow,
    ToDocParserAssistant,
    ToExaminationAssistant,
    ToExplanationAssistant,
    ToRelationAssistant,
    ToSummaryAssistant,
)
from tech_doc_agent.app.core.model_pricing import ModelPriceTable
from tech_doc_agent.app.graph.budgeting import WorkflowBudgetTracker
from tech_doc_agent.app.graph.specs import (
    AgentSpec,
    CompletionPolicy,
    GraphSpec,
    PrimarySpec,
    ReflectionPolicy,
    ToolExecutionPolicy,
    ToolPolicy,
)


class StubAssistant:
    def __init__(self, name: str):
        self.name = name

    def __call__(self, state, config=None):
        return {"messages": AIMessage(content="stub", name=self.name)}

    async def ainvoke(self, state, config=None):
        return self(state, config)


def _named_tool(name: str) -> StructuredTool:
    def invoke(**kwargs):
        return {"tool": name, "args": kwargs}

    return StructuredTool.from_function(
        invoke,
        name=name,
        description=f"Test tool named {name}.",
    )


@pytest.fixture
def graph_spec() -> GraphSpec:
    tools = {
        name: _named_tool(name)
        for name in (
            "web_search",
            "read_docs",
            "save_docs",
            "search_related_docs",
            "read_learning_history",
            "read_all_learning_history",
            "read_user_memory",
            "upsert_learning_history",
            "upsert_learning_state",
            "read_user_profile",
            "update_user_profile",
        )
    }
    return GraphSpec(
        primary=PrimarySpec(
            assistant=StubAssistant("primary"),
            tools=ToolPolicy(
                safe=(
                    tools["read_user_profile"],
                    tools["read_learning_history"],
                    tools["read_all_learning_history"],
                    tools["read_user_memory"],
                    PlanWorkflow,
                    ToDocParserAssistant,
                    ToExplanationAssistant,
                    ToRelationAssistant,
                    ToExaminationAssistant,
                    ToSummaryAssistant,
                ),
                sensitive=(
                    tools["upsert_learning_history"],
                    tools["update_user_profile"],
                ),
            ),
        ),
        subagents=(
            AgentSpec(
                key="parser",
                display_name="Parser Assistant",
                assistant=StubAssistant("parser"),
                tools=ToolPolicy(
                    safe=(tools["read_docs"], tools["web_search"]),
                    sensitive=(tools["save_docs"],),
                ),
                completion=CompletionPolicy(result_key="parser_result", structured_kind="parser"),
            ),
            AgentSpec(
                key="explanation",
                display_name="Explanation Assistant",
                assistant=StubAssistant("explanation"),
                tools=ToolPolicy(safe=(tools["read_docs"],)),
            ),
            AgentSpec(
                key="relation",
                display_name="Relation Assistant",
                assistant=StubAssistant("relation"),
                tools=ToolPolicy(
                    safe=(
                        tools["read_all_learning_history"],
                        tools["search_related_docs"],
                        tools["read_docs"],
                    )
                ),
                completion=CompletionPolicy(result_key="relation_result", structured_kind="relation"),
            ),
            AgentSpec(
                key="examination",
                display_name="Examination Assistant",
                assistant=StubAssistant("examination"),
                tools=ToolPolicy(
                    safe=(tools["read_learning_history"], tools["read_docs"]),
                    sensitive=(tools["upsert_learning_history"],),
                ),
                completion=CompletionPolicy(result_key="examination_context"),
            ),
            AgentSpec(
                key="summary",
                display_name="Summary Assistant",
                assistant=StubAssistant("summary"),
                tools=ToolPolicy(
                    safe=(tools["read_learning_history"], tools["read_user_memory"]),
                    sensitive=(tools["upsert_learning_state"],),
                ),
                scoped_messages=False,
            ),
        ),
        user_info_node=lambda state, config: {
            "user_info": "stub",
            "user_id": state.get("user_id", "default"),
            "namespace": state.get("namespace", "tech_docs"),
            "learning_target": state.get("learning_target", ""),
        },
        tool_execution_policy=ToolExecutionPolicy(
            max_identical_repeats=2,
            parser_max_retrieval_calls=6,
        ),
        reflection_policy=ReflectionPolicy(max_rounds=1),
        budget_tracker=WorkflowBudgetTracker(ModelPriceTable.empty()),
    )
