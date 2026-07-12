from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from tech_doc_agent.app.core.structured_outputs import ResultKind
from tech_doc_agent.app.core.execution_budget import ExecutionBudget
from tech_doc_agent.app.graph.budgeting import WorkflowBudgetTracker
from tech_doc_agent.app.graph.context_metrics import ContextMetricsTracker
from tech_doc_agent.app.graph.context_compaction import ContextCompactor
from tech_doc_agent.app.graph.provider_retries import ProviderRetryUsageTracker

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
class ExecutionPolicy:
    budget: ExecutionBudget
    tools: ToolExecutionPolicy
    reflection: ReflectionPolicy


CompletionResultKey = Literal[
    "parser_result",
    "relation_result",
    "examination_context",
]


@dataclass(frozen=True)
class CompletionPolicy:
    result_key: CompletionResultKey | None = None
    structured_kind: ResultKind | None = None

    def __post_init__(self) -> None:
        valid_combinations = {
            (None, None),
            ("parser_result", "parser"),
            ("relation_result", "relation"),
            ("examination_context", None),
        }
        if (self.result_key, self.structured_kind) not in valid_combinations:
            raise ValueError(
                "CompletionPolicy result_key and structured_kind must describe "
                "one supported state result."
            )


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
    execution_policy: ExecutionPolicy
    budget_tracker: WorkflowBudgetTracker
    context_tracker: ContextMetricsTracker
    context_compactor: ContextCompactor
    provider_retry_tracker: ProviderRetryUsageTracker = field(
        default_factory=ProviderRetryUsageTracker
    )

    def __post_init__(self) -> None:
        if self.budget_tracker.execution_budget != self.execution_policy.budget:
            raise ValueError(
                "Graph execution policy and budget tracker must share one budget."
            )

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
