"""Document parsing assistant tool composition."""

from tech_doc_agent.app.graph.commands import CompleteOrEscalate
from tech_doc_agent.app.services.assistants.definition import (
    AssistantDefinition,
    build_assistant_definition,
)
from tech_doc_agent.app.services.assistants.model_factory import AssistantModelProvider
from tech_doc_agent.app.services.assistants.prompt_registry import PromptArtifact
from tech_doc_agent.app.tools import ToolBundle


def build_parser_assistant(
    models: AssistantModelProvider,
    tools: ToolBundle,
    prompt: PromptArtifact,
) -> AssistantDefinition:
    return build_assistant_definition(
        prompt=prompt,
        models=models,
        name="parser",
        safe_tools=(tools.read_docs, tools.web_search),
        sensitive_tools=(tools.save_docs,),
        control_tools=(CompleteOrEscalate,),
    )
