from dataclasses import dataclass
from typing import Any

from .assistant_base import Assistant
from .identity import AssistantExecutionIdentity
from .model_factory import AssistantModelProvider
from .prompt_registry import (
    AssistantRole,
    PromptArtifact,
)


@dataclass(frozen=True, slots=True)
class AssistantDefinition:
    assistant: Assistant
    safe_tools: tuple[Any, ...]
    identity: AssistantExecutionIdentity
    sensitive_tools: tuple[Any, ...] = ()

    @property
    def all_tools(self) -> tuple[Any, ...]:
        return (*self.safe_tools, *self.sensitive_tools)

    @property
    def prompt_id(self) -> str:
        return self.identity.prompt_id

    @property
    def prompt_sha256(self) -> str:
        return self.identity.prompt_sha256


def build_assistant_definition(
    *,
    prompt: PromptArtifact,
    models: AssistantModelProvider,
    name: AssistantRole,
    safe_tools: tuple[Any, ...],
    sensitive_tools: tuple[Any, ...] = (),
    control_tools: tuple[Any, ...] = (),
) -> AssistantDefinition:
    if name != prompt.role:
        raise ValueError(
            f"Assistant role {name!r} cannot use prompt role {prompt.role!r}."
        )
    identity = AssistantExecutionIdentity(
        role=name,
        prompt_id=prompt.prompt_id,
        prompt_sha256=prompt.sha256,
        model_provider_id=models.provider_id,
        primary_model_id=models.primary_model_id,
        backup_model_id=models.backup_model_id,
    )
    runnable = (
        prompt.template
        | models.bind_tools(
            [*safe_tools, *sensitive_tools, *control_tools],
            parallel_tool_calls=False,
        )
    ).with_config(metadata=identity.to_metadata())
    return AssistantDefinition(
        assistant=Assistant(
            runnable,
            name=name,
            retry_executor=models.retry_executor,
            default_provider=models.provider_id,
        ),
        safe_tools=safe_tools,
        identity=identity,
        sensitive_tools=sensitive_tools,
    )
