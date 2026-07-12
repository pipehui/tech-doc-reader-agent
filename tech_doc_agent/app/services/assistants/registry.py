from dataclasses import dataclass

from tech_doc_agent.app.services.assistants.definition import AssistantDefinition
from tech_doc_agent.app.services.assistants.examination_assistant import build_examination_assistant
from tech_doc_agent.app.services.assistants.explanation_assistant import build_explanation_assistant
from tech_doc_agent.app.services.assistants.identity import AssistantExecutionIdentity
from tech_doc_agent.app.services.assistants.model_factory import AssistantModelProvider
from tech_doc_agent.app.services.assistants.parser_assistant import build_parser_assistant
from tech_doc_agent.app.services.assistants.primary_assistant import build_primary_assistant
from tech_doc_agent.app.services.assistants.prompt_registry import PromptRegistry
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

    def identities(self) -> tuple[AssistantExecutionIdentity, ...]:
        return tuple(
            getattr(self, role).identity
            for role in (
                "primary",
                "parser",
                "relation",
                "explanation",
                "examination",
                "summary",
            )
        )


def build_assistant_registry(
    models: AssistantModelProvider,
    tools: ToolBundle,
    prompts: PromptRegistry,
) -> AssistantRegistry:
    return AssistantRegistry(
        primary=build_primary_assistant(models, tools, prompts.require("primary")),
        parser=build_parser_assistant(models, tools, prompts.require("parser")),
        relation=build_relation_assistant(models, tools, prompts.require("relation")),
        explanation=build_explanation_assistant(
            models,
            tools,
            prompts.require("explanation"),
        ),
        examination=build_examination_assistant(
            models,
            tools,
            prompts.require("examination"),
        ),
        summary=build_summary_assistant(models, tools, prompts.require("summary")),
    )
