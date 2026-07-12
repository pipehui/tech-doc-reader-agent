from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tech_doc_agent.app.core.structured_outputs import ResultKind
from tech_doc_agent.app.graph.budgeting import WorkflowBudgetTracker

from .state import WorkflowStep


@dataclass(frozen=True)
class ToolPolicy:
    safe: tuple[Any, ...] = field(default_factory=tuple)
    sensitive: tuple[Any, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ToolExecutionPolicy:
    max_identical_repeats: int
    parser_max_retrieval_calls: int

    def __post_init__(self) -> None:
        if self.max_identical_repeats < 0:
            raise ValueError("max_identical_repeats must be non-negative.")
        if self.parser_max_retrieval_calls < 0:
            raise ValueError("parser_max_retrieval_calls must be non-negative.")


@dataclass(frozen=True)
class ReflectionPolicy:
    max_rounds: int = 1
    repairable_error_codes: frozenset[str] = field(
        default_factory=lambda: frozenset({"validation_error"})
    )

    def __post_init__(self) -> None:
        if self.max_rounds < 0:
            raise ValueError("max_rounds must be non-negative.")
        if not self.repairable_error_codes or any(
            not isinstance(code, str) or not code.strip() or code != code.strip()
            for code in self.repairable_error_codes
        ):
            raise ValueError("repairable_error_codes must contain non-empty strings.")


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
    tool_execution_policy: ToolExecutionPolicy
    reflection_policy: ReflectionPolicy
    budget_tracker: WorkflowBudgetTracker

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
