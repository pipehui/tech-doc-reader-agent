from dataclasses import dataclass
from typing import Any

from tech_doc_agent.app.services.assistants.assistant_base import Assistant
from tech_doc_agent.app.services.assistants.model_factory import AssistantModelProvider


@dataclass(frozen=True, slots=True)
class AssistantDefinition:
    assistant: Assistant
    safe_tools: tuple[Any, ...]
    sensitive_tools: tuple[Any, ...] = ()

    @property
    def all_tools(self) -> tuple[Any, ...]:
        return (*self.safe_tools, *self.sensitive_tools)


def build_assistant_definition(
    *,
    prompt: Any,
    models: AssistantModelProvider,
    name: str,
    safe_tools: tuple[Any, ...],
    sensitive_tools: tuple[Any, ...] = (),
    control_tools: tuple[Any, ...] = (),
) -> AssistantDefinition:
    runnable = prompt | models.bind_tools(
        [*safe_tools, *sensitive_tools, *control_tools],
        parallel_tool_calls=False,
    )
    return AssistantDefinition(
        assistant=Assistant(runnable, name=name),
        safe_tools=safe_tools,
        sensitive_tools=sensitive_tools,
    )
