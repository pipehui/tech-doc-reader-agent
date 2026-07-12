"""Learning examination assistant tool composition."""

from tech_doc_agent.app.graph.commands import CompleteOrEscalate
from .definition import (
    AssistantDefinition,
    build_assistant_definition,
)
from .model_factory import AssistantModelProvider
from .prompt_registry import PromptArtifact
from tech_doc_agent.app.tools import ToolBundle


def build_examination_assistant(
    models: AssistantModelProvider,
    tools: ToolBundle,
    prompt: PromptArtifact,
) -> AssistantDefinition:
    return build_assistant_definition(
        prompt=prompt,
        models=models,
        name="examination",
        safe_tools=(tools.read_learning_history, tools.read_docs),
        sensitive_tools=(tools.upsert_learning_history,),
        control_tools=(CompleteOrEscalate,),
    )
