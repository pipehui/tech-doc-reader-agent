"""Learner-facing explanation assistant tool composition."""

from tech_doc_agent.app.graph.commands import CompleteOrEscalate
from .definition import (
    AssistantDefinition,
    build_assistant_definition,
)
from .model_factory import AssistantModelProvider
from .prompt_registry import PromptArtifact
from tech_doc_agent.app.tools import ToolBundle


def build_explanation_assistant(
    models: AssistantModelProvider,
    tools: ToolBundle,
    prompt: PromptArtifact,
) -> AssistantDefinition:
    return build_assistant_definition(
        prompt=prompt,
        models=models,
        name="explanation",
        safe_tools=(tools.read_docs,),
        control_tools=(CompleteOrEscalate,),
    )
