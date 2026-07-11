from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tech_doc_agent.app.core.structured_outputs import ResultKind

from .state import WorkflowStep


@dataclass(frozen=True)
class ToolPolicy:
    safe: tuple[Any, ...] = field(default_factory=tuple)
    sensitive: tuple[Any, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CompletionPolicy:
    result_key: str | None = None
    structured_kind: ResultKind | None = None


@dataclass(frozen=True)
class AgentSpec:
    key: WorkflowStep
    display_name: str
    assistant: Any
    tools: ToolPolicy
    completion: CompletionPolicy = field(default_factory=CompletionPolicy)
    scoped_messages: bool = True

    @property
    def entry_node(self) -> str:
        return f"enter_{self.key}"

    @property
    def safe_tool_node(self) -> str:
        return f"{self.key}_assistant_safe_tools"

    @property
    def sensitive_tool_node(self) -> str:
        return f"{self.key}_assistant_sensitive_tools"

    @property
    def leave_node(self) -> str:
        return f"leave_{self.key}"

    @property
    def finish_node(self) -> str:
        return f"finish_{self.key}"


@dataclass(frozen=True)
class PrimarySpec:
    assistant: Any
    tools: ToolPolicy


@dataclass(frozen=True)
class GraphSpec:
    primary: PrimarySpec
    subagents: tuple[AgentSpec, ...]
    user_info_node: Any

    @property
    def interrupt_nodes(self) -> tuple[str, ...]:
        return (
            *(
                spec.sensitive_tool_node
                for spec in self.subagents
                if spec.tools.sensitive
            ),
            "primary_assistant_sensitive_tools",
        )
