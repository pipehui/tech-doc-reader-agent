"""Learning-summary assistant tool composition."""

from tech_doc_agent.app.graph.commands import CompleteOrEscalate
from tech_doc_agent.app.services.assistants.definition import (
    AssistantDefinition,
    build_assistant_definition,
)
from tech_doc_agent.app.services.assistants.model_factory import AssistantModelProvider
from tech_doc_agent.app.services.assistants.prompt_registry import PromptArtifact
from tech_doc_agent.app.tools import ToolBundle


def build_summary_assistant(
    models: AssistantModelProvider,
    tools: ToolBundle,
    prompt: PromptArtifact,
) -> AssistantDefinition:
    return build_assistant_definition(
        prompt=prompt,
        models=models,
        name="summary",
        safe_tools=(tools.read_learning_history, tools.read_user_memory),
        sensitive_tools=(tools.upsert_learning_state,),
        control_tools=(CompleteOrEscalate,),
    )
