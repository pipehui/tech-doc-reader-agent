from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool

from tech_doc_agent.app.graph.commands import (
    CompleteOrEscalate,
    PlanWorkflow,
    ToDocParserAssistant,
    ToExaminationAssistant,
    ToExplanationAssistant,
    ToRelationAssistant,
    ToSummaryAssistant,
)
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.assistants import model_factory
from tech_doc_agent.app.services.assistants.model_factory import AssistantModelProvider
from tech_doc_agent.app.services.assistants.prompt_registry import build_prompt_registry
from tech_doc_agent.app.services.assistants.registry import build_assistant_registry
from tech_doc_agent.app.tools import ToolBundle


class RecordingBindableModel:
    def __init__(self):
        self.calls = []

    def bind_tools(self, tools, *, parallel_tool_calls):
        self.calls.append(
            {
                "names": [_tool_name(tool) for tool in tools],
                "parallel_tool_calls": parallel_tool_calls,
            }
        )
        return RunnableLambda(lambda value: AIMessage(content="stub"))


def _named_tool(name: str) -> StructuredTool:
    def invoke(**kwargs):
        return kwargs

    return StructuredTool.from_function(
        invoke,
        name=name,
        description=f"Test tool named {name}.",
    )


def _tool_bundle() -> ToolBundle:
    names = (
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
    return ToolBundle(**{name: _named_tool(name) for name in names})


def test_assistant_registry_binds_each_role_to_declared_tools():
    model = RecordingBindableModel()
    registry = build_assistant_registry(
        AssistantModelProvider(
            primary=model,
            provider_id="provider-a",
            primary_model_id="primary-model",
        ),
        _tool_bundle(),
        build_prompt_registry(),
    )

    assert [tool.name for tool in registry.parser.safe_tools] == ["read_docs", "web_search"]
    assert [tool.name for tool in registry.parser.sensitive_tools] == ["save_docs"]
    assert [tool.name for tool in registry.summary.safe_tools] == [
        "read_learning_history",
        "read_user_memory",
    ]
    assert [tool.name for tool in registry.summary.sensitive_tools] == ["upsert_learning_state"]
    assert [_tool_name(tool) for tool in registry.primary.safe_tools] == [
        "read_user_profile",
        "read_learning_history",
        "read_all_learning_history",
        "read_user_memory",
        "PlanWorkflow",
        "ToDocParserAssistant",
        "ToExplanationAssistant",
        "ToRelationAssistant",
        "ToExaminationAssistant",
        "ToSummaryAssistant",
    ]
    assert [tool.name for tool in registry.primary.sensitive_tools] == [
        "upsert_learning_history",
        "update_user_profile",
    ]
    assert [call["names"] for call in model.calls] == [
        [
            "read_user_profile",
            "read_learning_history",
            "read_all_learning_history",
            "read_user_memory",
            "PlanWorkflow",
            "ToDocParserAssistant",
            "ToExplanationAssistant",
            "ToRelationAssistant",
            "ToExaminationAssistant",
            "ToSummaryAssistant",
            "upsert_learning_history",
            "update_user_profile",
        ],
        ["read_docs", "web_search", "save_docs", "CompleteOrEscalate"],
        [
            "read_all_learning_history",
            "search_related_docs",
            "read_docs",
            "CompleteOrEscalate",
        ],
        ["read_docs", "CompleteOrEscalate"],
        [
            "read_learning_history",
            "read_docs",
            "upsert_learning_history",
            "CompleteOrEscalate",
        ],
        [
            "read_learning_history",
            "read_user_memory",
            "upsert_learning_state",
            "CompleteOrEscalate",
        ],
    ]
    assert all(call["parallel_tool_calls"] is False for call in model.calls)
    assert registry.primary.prompt_id == "tech-doc-reader.primary.v1"
    assert registry.primary.prompt_sha256 == (
        "034f2970a0d7fead2f8efdca693dbb6c59585c2400b839a0dc3dc9e9609ba9a3"
    )
    assert registry.primary.assistant.runnable.config["metadata"] == {
        "assistant_role": "primary",
        "prompt_id": "tech-doc-reader.primary.v1",
        "prompt_sha256": "034f2970a0d7fead2f8efdca693dbb6c59585c2400b839a0dc3dc9e9609ba9a3",
        "model_provider_id": "provider-a",
        "primary_model_id": "primary-model",
    }
    assert [identity.role for identity in registry.identities()] == [
        "primary",
        "parser",
        "relation",
        "explanation",
        "examination",
        "summary",
    ]


def test_model_provider_binds_primary_and_backup_before_adding_fallback():
    primary = RecordingBindableModel()
    backup = RecordingBindableModel()
    provider = AssistantModelProvider(primary=primary, backup=backup)
    tools = [_named_tool("read_docs")]

    runnable = provider.bind_tools(tools, parallel_tool_calls=False)

    assert runnable is not None
    assert primary.calls == backup.calls == [
        {"names": ["read_docs"], "parallel_tool_calls": False}
    ]


def test_model_provider_disables_sdk_retries_and_owns_shared_transport_policy(monkeypatch):
    created = []

    class FakeChatModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

    monkeypatch.setattr(model_factory, "ChatOpenAI", FakeChatModel)

    provider = model_factory.build_assistant_model_provider(
        Settings(
            OPENAI_API_KEY="primary-key",
            PRIMARY_MODEL="primary-model",
            BACKUP_API_KEY="backup-key",
            BACKUP_MODEL="backup-model",
            MODEL_PROVIDER_ID="provider-a",
            TRANSPORT_RETRY_MAX_ATTEMPTS=4,
        )
    )

    assert len(created) == 2
    assert created[0].kwargs["max_retries"] == 0
    assert created[1].kwargs["max_retries"] == 0
    assert provider.retry_executor is not None
    assert provider.retry_executor.policy.max_attempts == 4
    assert provider.provider_id == "provider-a"
    assert provider.primary_model_id == "primary-model"
    assert provider.backup_model_id == "backup-model"
    assert created[0].kwargs["model"] == provider.primary_model_id
    assert created[1].kwargs["model"] == provider.backup_model_id


def test_model_visible_command_names_and_required_fields_are_stable():
    expected_required = {
        CompleteOrEscalate: ["reason"],
        PlanWorkflow: ["steps", "goal", "learning_target"],
        ToDocParserAssistant: ["content", "request"],
        ToExplanationAssistant: ["concept", "request"],
        ToRelationAssistant: ["entity", "request"],
        ToExaminationAssistant: ["topic", "request"],
        ToSummaryAssistant: ["request"],
    }

    for command, required in expected_required.items():
        schema = command.model_json_schema()
        assert schema["title"] == command.__name__
        assert schema["required"] == required

    plan_steps = PlanWorkflow.model_json_schema()["properties"]["steps"]
    assert plan_steps["items"]["enum"] == [
        "parser",
        "relation",
        "explanation",
        "examination",
        "summary",
    ]
    assert ToSummaryAssistant.model_json_schema()["description"] == (
        "Transfers work to a specialized assistant to summarize the user's learning process."
    )


def _tool_name(tool) -> str:
    return getattr(tool, "name", getattr(tool, "__name__", ""))
