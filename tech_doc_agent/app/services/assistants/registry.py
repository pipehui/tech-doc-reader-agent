from dataclasses import dataclass

from tech_doc_agent.app.services.assistants.definition import AssistantDefinition
from tech_doc_agent.app.services.assistants.examination_assistant import build_examination_assistant
from tech_doc_agent.app.services.assistants.explanation_assistant import build_explanation_assistant
from tech_doc_agent.app.services.assistants.model_factory import AssistantModelProvider
from tech_doc_agent.app.services.assistants.parser_assistant import build_parser_assistant
from tech_doc_agent.app.services.assistants.primary_assistant import build_primary_assistant
from tech_doc_agent.app.services.assistants.relation_assistant import build_relation_assistant
from tech_doc_agent.app.services.assistants.summary_assistant import build_summary_assistant
from tech_doc_agent.app.tools import ToolBundle


@dataclass(frozen=True, slots=True)
class AssistantRegistry:
    primary: AssistantDefinition
    parser: AssistantDefinition
    relation: AssistantDefinition
    explanation: AssistantDefinition
    examination: AssistantDefinition
    summary: AssistantDefinition


def build_assistant_registry(
    models: AssistantModelProvider,
    tools: ToolBundle,
) -> AssistantRegistry:
    return AssistantRegistry(
        primary=build_primary_assistant(models, tools),
        parser=build_parser_assistant(models, tools),
        relation=build_relation_assistant(models, tools),
        explanation=build_explanation_assistant(models, tools),
        examination=build_examination_assistant(models, tools),
        summary=build_summary_assistant(models, tools),
    )
