from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from tech_doc_agent.app.core.context_serialization import estimate_serialized_bytes
from tech_doc_agent.app.core.settings import Settings


CompactionSkipReason = Literal[
    "disabled",
    "threshold_not_exceeded",
    "serialization_unavailable",
    "no_current_user_message",
    "active_dialog",
    "active_workflow",
    "active_reflection",
    "insufficient_closed_turns",
    "open_tool_exchange",
]


@dataclass(frozen=True, slots=True)
class ContextCompactionPolicy:
    max_messages: int = 0
    max_serialized_bytes: int = 0
    keep_recent_turns: int = 4
    summary_max_chars: int = 12_000

    def __post_init__(self) -> None:
        if self.max_messages < 0 or self.max_serialized_bytes < 0:
            raise ValueError("Context compaction thresholds must be non-negative.")
        if self.keep_recent_turns < 1:
            raise ValueError("Context compaction must keep at least one recent turn.")
        if self.summary_max_chars < 256:
            raise ValueError("Conversation summaries must allow at least 256 characters.")

    @property
    def enabled(self) -> bool:
        return self.max_messages > 0 or self.max_serialized_bytes > 0


@dataclass(frozen=True, slots=True)
class ContextCompactionPlan:
    source_messages: tuple[Any, ...]
    retained_messages: tuple[Any, ...]
    before_message_count: int
    before_serialized_bytes: int | None


@dataclass(frozen=True, slots=True)
class ContextCompactionDecision:
    plan: ContextCompactionPlan | None
    skip_reason: CompactionSkipReason | None = None

    @property
    def should_compact(self) -> bool:
        return self.plan is not None


def build_context_compaction_policy(settings: Settings) -> ContextCompactionPolicy:
    return ContextCompactionPolicy(
        max_messages=settings.CONTEXT_COMPACTION_MAX_MESSAGES,
        max_serialized_bytes=settings.CONTEXT_COMPACTION_MAX_SERIALIZED_BYTES,
        keep_recent_turns=settings.CONTEXT_COMPACTION_KEEP_RECENT_TURNS,
        summary_max_chars=settings.CONTEXT_SUMMARY_MAX_CHARS,
    )


def plan_context_compaction(
    state: Mapping[str, Any],
    policy: ContextCompactionPolicy,
) -> ContextCompactionDecision:
    messages = tuple(state.get("messages", ()) or ())
    serialized_bytes = estimate_serialized_bytes(messages)

    if not policy.enabled:
        return _skip("disabled")

    count_exceeded = policy.max_messages > 0 and len(messages) > policy.max_messages
    bytes_exceeded = (
        policy.max_serialized_bytes > 0
        and serialized_bytes is not None
        and serialized_bytes > policy.max_serialized_bytes
    )
    if not count_exceeded and not bytes_exceeded:
        if policy.max_messages == 0 and serialized_bytes is None:
            return _skip("serialization_unavailable")
        return _skip("threshold_not_exceeded")

    if not messages or _message_type(messages[-1]) != "human":
        return _skip("no_current_user_message")
    if state.get("dialog_state"):
        return _skip("active_dialog")

    workflow_plan = state.get("workflow_plan")
    plan_index = state.get("plan_index", 0)
    if isinstance(workflow_plan, list) and workflow_plan:
        if (
            isinstance(plan_index, bool)
            or not isinstance(plan_index, int)
            or plan_index != len(workflow_plan)
        ):
            return _skip("active_workflow")
    if state.get("reflection_status") in {"repairing", "finalizing", "terminal"}:
        return _skip("active_reflection")

    human_indices = [
        index for index, message in enumerate(messages) if _message_type(message) == "human"
    ]
    if len(human_indices) <= policy.keep_recent_turns:
        return _skip("insufficient_closed_turns")

    retain_from = human_indices[-policy.keep_recent_turns]
    source_messages = messages[:retain_from]
    retained_messages = messages[retain_from:]
    if not source_messages:
        return _skip("insufficient_closed_turns")
    if not _tool_exchanges_are_closed(source_messages):
        return _skip("open_tool_exchange")

    return ContextCompactionDecision(
        plan=ContextCompactionPlan(
            source_messages=source_messages,
            retained_messages=retained_messages,
            before_message_count=len(messages),
            before_serialized_bytes=serialized_bytes,
        )
    )


def _tool_exchanges_are_closed(messages: Sequence[Any]) -> bool:
    tool_call_ids: set[str] = set()
    tool_result_ids: set[str] = set()

    for message in messages:
        message_type = _message_type(message)
        if message_type == "ai":
            for tool_call in list(getattr(message, "tool_calls", ()) or ()):
                if not isinstance(tool_call, dict):
                    return False
                tool_call_id = tool_call.get("id")
                if (
                    not isinstance(tool_call_id, str)
                    or not tool_call_id
                    or tool_call_id in tool_call_ids
                ):
                    return False
                tool_call_ids.add(tool_call_id)
        elif message_type == "tool":
            tool_call_id = getattr(message, "tool_call_id", None)
            if (
                not isinstance(tool_call_id, str)
                or not tool_call_id
                or tool_call_id in tool_result_ids
            ):
                return False
            tool_result_ids.add(tool_call_id)

    return tool_call_ids == tool_result_ids


def _message_type(message: Any) -> str | None:
    if isinstance(message, tuple) and message:
        return {
            "user": "human",
            "assistant": "ai",
        }.get(message[0], message[0])
    return getattr(message, "type", None)


def _skip(reason: CompactionSkipReason) -> ContextCompactionDecision:
    return ContextCompactionDecision(plan=None, skip_reason=reason)


__all__ = [
    "CompactionSkipReason",
    "ContextCompactionDecision",
    "ContextCompactionPlan",
    "ContextCompactionPolicy",
    "build_context_compaction_policy",
    "plan_context_compaction",
]
