from tech_doc_agent.app.graph.commands import (
    PlanWorkflow,
    ToDocParserAssistant,
    ToExaminationAssistant,
    ToExplanationAssistant,
    ToRelationAssistant,
    ToSummaryAssistant,
)
from tech_doc_agent.app.services.assistants.definition import (
    AssistantDefinition,
    build_assistant_definition,
)
from tech_doc_agent.app.services.assistants.model_factory import AssistantModelProvider
from tech_doc_agent.app.services.assistants.prompt_registry import PromptArtifact
from tech_doc_agent.app.tools import ToolBundle


def build_primary_assistant(
    models: AssistantModelProvider,
    tools: ToolBundle,
    prompt: PromptArtifact,
) -> AssistantDefinition:
    return build_assistant_definition(
        prompt=prompt,
        models=models,
        name="primary",
        safe_tools=(
            tools.read_user_profile,
            tools.read_learning_history,
            tools.read_all_learning_history,
            tools.read_user_memory,
            PlanWorkflow,
            ToDocParserAssistant,
            ToExplanationAssistant,
            ToRelationAssistant,
            ToExaminationAssistant,
            ToSummaryAssistant,
        ),
        sensitive_tools=(tools.upsert_learning_history, tools.update_user_profile),
    )
