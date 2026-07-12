from dataclasses import dataclass
from typing import Any

from tech_doc_agent.app.services.assistants.assistant_base import Assistant
from tech_doc_agent.app.services.assistants.model_factory import AssistantModelProvider
from tech_doc_agent.app.services.assistants.prompt_registry import PromptArtifact


@dataclass(frozen=True, slots=True)
class AssistantDefinition:
    assistant: Assistant
    safe_tools: tuple[Any, ...]
    prompt_id: str
    prompt_sha256: str
    sensitive_tools: tuple[Any, ...] = ()

    @property
    def all_tools(self) -> tuple[Any, ...]:
        return (*self.safe_tools, *self.sensitive_tools)


def build_assistant_definition(
    *,
    prompt: PromptArtifact,
    models: AssistantModelProvider,
    name: str,
    safe_tools: tuple[Any, ...],
    sensitive_tools: tuple[Any, ...] = (),
    control_tools: tuple[Any, ...] = (),
) -> AssistantDefinition:
    runnable = (
        prompt.template
        | models.bind_tools(
            [*safe_tools, *sensitive_tools, *control_tools],
            parallel_tool_calls=False,
        )
    ).with_config(
        metadata={
            "assistant_role": name,
            "prompt_id": prompt.prompt_id,
            "prompt_sha256": prompt.sha256,
        }
    )
    return AssistantDefinition(
        assistant=Assistant(
            runnable,
            name=name,
            retry_executor=models.retry_executor,
            default_provider=models.provider_id,
        ),
        safe_tools=safe_tools,
        prompt_id=prompt.prompt_id,
        prompt_sha256=prompt.sha256,
        sensitive_tools=sensitive_tools,
    )
