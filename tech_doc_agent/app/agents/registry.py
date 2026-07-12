from dataclasses import dataclass

from .definition import AssistantDefinition
from .examination_assistant import build_examination_assistant
from .explanation_assistant import build_explanation_assistant
from .identity import AssistantExecutionIdentity
from .model_factory import AssistantModelProvider
from .parser_assistant import build_parser_assistant
from .primary_assistant import build_primary_assistant
from .prompt_registry import PromptRegistry
from .relation_assistant import build_relation_assistant
from .summary_assistant import build_summary_assistant
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
