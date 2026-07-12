from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages import message_to_dict
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph.message import add_messages

from tech_doc_agent.app.core.context_compaction import ContextCompactionPolicy
from tech_doc_agent.app.core.context_serialization import estimate_serialized_bytes
from tech_doc_agent.app.core.conversation_summary import read_conversation_summary
from tech_doc_agent.app.core.state import State
from tech_doc_agent.app.graph.context_compaction import ContextCompactor
from tech_doc_agent.app.services.conversation_summarizer import (
    ExtractiveConversationSummarizer,
)
from tech_doc_agent.app.services.message_scope import build_assistant_state


MarkerRole = Literal["human", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class MarkerPlacement:
    value: str
    turn: int
    role: MarkerRole


@dataclass(frozen=True, slots=True)
class ContextCompactionCase:
    id: str
    category: str
    turn_count: int
    filler_chars: int
    tool_every: int
    tool_payload_chars: int
    markers: tuple[MarkerPlacement, ...]
    expected_marker: str
    expected_compacted_marker: str | None

    @classmethod
    def from_payload(cls, payload: Any) -> ContextCompactionCase:
        if not isinstance(payload, dict):
            raise ValueError("Each context compaction eval case must be an object.")
        required = {
            "id",
            "category",
            "turn_count",
            "markers",
            "expected_marker",
            "expected_compacted_marker",
        }
        missing = required - set(payload)
        if missing:
            raise ValueError(
                f"Context compaction eval case missing required fields: {sorted(missing)}"
            )

        raw_markers = payload["markers"]
        if not isinstance(raw_markers, list) or not raw_markers:
            raise ValueError("Context compaction eval case markers must be a non-empty list.")
        markers = tuple(_marker_from_payload(item) for item in raw_markers)
        case = cls(
            id=_required_text(payload["id"], "id"),
            category=_required_text(payload["category"], "category"),
            turn_count=_non_negative_int(payload["turn_count"], "turn_count"),
            filler_chars=_non_negative_int(payload.get("filler_chars", 32), "filler_chars"),
            tool_every=_non_negative_int(payload.get("tool_every", 0), "tool_every"),
            tool_payload_chars=_non_negative_int(
                payload.get("tool_payload_chars", 0),
                "tool_payload_chars",
            ),
            markers=markers,
            expected_marker=_required_text(payload["expected_marker"], "expected_marker"),
            expected_compacted_marker=_optional_text(
                payload["expected_compacted_marker"],
                "expected_compacted_marker",
            ),
        )
        case.validate()
        return case

    def validate(self) -> None:
        if self.turn_count < 2:
            raise ValueError("Context compaction eval turn_count must be at least 2.")
        marker_values = {marker.value for marker in self.markers}
        if self.expected_marker not in marker_values:
            raise ValueError("expected_marker must reference a declared marker.")
        if (
            self.expected_compacted_marker is not None
            and self.expected_compacted_marker not in marker_values
        ):
            raise ValueError("expected_compacted_marker must reference a declared marker or null.")
        if any(marker.turn >= self.turn_count for marker in self.markers):
            raise ValueError("Marker turn must be smaller than turn_count.")


@dataclass(frozen=True, slots=True)
class SessionMeasurement:
    state: dict[str, Any]
    prompt_messages: tuple[BaseMessage, ...]
    checkpoint_bytes: int
    prompt_bytes: int
    approximate_input_tokens: int
    answer_marker: str | None
    compaction_elapsed_s: float
    compaction_events: int


def load_context_compaction_cases(path: Path) -> list[ContextCompactionCase]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array of context compaction cases.")
    cases = [ContextCompactionCase.from_payload(item) for item in payload]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Context compaction eval case IDs must be unique.")
    return cases


def evaluate_context_compaction_case(
    case: ContextCompactionCase,
    policy: ContextCompactionPolicy,
    *,
    iterations: int = 5,
) -> dict[str, Any]:
    if iterations < 1:
        raise ValueError("Context compaction eval iterations must be positive.")

    baseline = _simulate(case, policy=None)
    compacted_runs = [_simulate(case, policy=policy) for _ in range(iterations)]
    compacted = compacted_runs[-1]
    latency_samples = [run.compaction_elapsed_s for run in compacted_runs]
    summary = read_conversation_summary(compacted.state.get("conversation_summary"))

    return {
        "id": case.id,
        "category": case.category,
        "turn_count": case.turn_count,
        "status": "done",
        "error": None,
        "expected_marker": case.expected_marker,
        "expected_compacted_marker": case.expected_compacted_marker,
        "baseline": _measurement_payload(baseline),
        "compacted": _measurement_payload(compacted),
        "compaction": {
            "enabled": policy.enabled,
            "max_messages": policy.max_messages,
            "max_serialized_bytes": policy.max_serialized_bytes,
            "keep_recent_turns": policy.keep_recent_turns,
            "summary_max_chars": policy.summary_max_chars,
            "events": compacted.compaction_events,
            "covered_message_count": summary.covered_message_count if summary else 0,
            "source_ranges": len(summary.source_ranges) if summary else 0,
            "latency_p50_ms": _percentile(latency_samples, 50) * 1000,
            "latency_p95_ms": _percentile(latency_samples, 95) * 1000,
        },
        "scores": {
            "baseline_task_correct": _binary(
                baseline.answer_marker == case.expected_marker
            ),
            "compacted_task_correct": _binary(
                compacted.answer_marker == case.expected_marker
            ),
            "answer_consistent": _binary(
                compacted.answer_marker == baseline.answer_marker
            ),
            "policy_expectation_match": _binary(
                compacted.answer_marker == case.expected_compacted_marker
            ),
            "checkpoint_reduction_ratio": _reduction_ratio(
                baseline.checkpoint_bytes,
                compacted.checkpoint_bytes,
            ),
            "prompt_bytes_reduction_ratio": _reduction_ratio(
                baseline.prompt_bytes,
                compacted.prompt_bytes,
            ),
            "approximate_input_token_reduction_ratio": _reduction_ratio(
                baseline.approximate_input_tokens,
                compacted.approximate_input_tokens,
            ),
        },
        "limitations": {
            "answer_metric": "deterministic_marker_recall_proxy",
            "token_metric": "langchain_count_tokens_approximately",
            "provider_input_tokens": None,
            "model_answer_consistency": None,
        },
    }


def _simulate(
    case: ContextCompactionCase,
    *,
    policy: ContextCompactionPolicy | None,
) -> SessionMeasurement:
    state: dict[str, Any] = {"messages": []}
    events: list[tuple[str, dict[str, Any]]] = []
    compactor = (
        ContextCompactor(
            policy=policy,
            summarizer=ExtractiveConversationSummarizer(),
            event_logger=lambda event, **fields: events.append((event, fields)),
        )
        if policy is not None
        else None
    )
    compaction_elapsed_s = 0.0

    for turn in range(case.turn_count):
        messages = _turn_messages(case, turn)
        state["messages"] = add_messages(state["messages"], [messages[0]])
        if compactor is not None:
            started_at = time.perf_counter()
            update = compactor(cast(State, state))
            compaction_elapsed_s += time.perf_counter() - started_at
            state = _apply_graph_update(state, update)
        state["messages"] = add_messages(state["messages"], messages[1:])

    final_message = HumanMessage(
        id=f"{case.id}-h-final",
        content="Recall the most recent relevant marker from our earlier discussion.",
    )
    state["messages"] = add_messages(state["messages"], [final_message])
    if compactor is not None:
        started_at = time.perf_counter()
        update = compactor(cast(State, state))
        compaction_elapsed_s += time.perf_counter() - started_at
        state = _apply_graph_update(state, update)

    prompt_state = build_assistant_state(
        cast(State, state),
        "primary",
        scoped_messages=False,
    )
    prompt_messages = tuple(prompt_state.get("messages", []))
    checkpoint_bytes = estimate_serialized_bytes(state)
    prompt_bytes = estimate_serialized_bytes(prompt_messages)
    if checkpoint_bytes is None or prompt_bytes is None:
        raise ValueError("Context compaction eval could not serialize generated state.")

    return SessionMeasurement(
        state=state,
        prompt_messages=prompt_messages,
        checkpoint_bytes=checkpoint_bytes,
        prompt_bytes=prompt_bytes,
        approximate_input_tokens=count_tokens_approximately(prompt_messages),
        answer_marker=_recall_probe(prompt_messages, case.markers),
        compaction_elapsed_s=compaction_elapsed_s,
        compaction_events=sum(event == "context.compacted" for event, _ in events),
    )


def _turn_messages(case: ContextCompactionCase, turn: int) -> list[BaseMessage]:
    placements = [marker for marker in case.markers if marker.turn == turn]
    human_markers = [marker.value for marker in placements if marker.role == "human"]
    assistant_markers = [marker.value for marker in placements if marker.role == "assistant"]
    tool_markers = [marker.value for marker in placements if marker.role == "tool"]
    messages: list[BaseMessage] = [
        HumanMessage(
            id=f"{case.id}-h-{turn}",
            content=_content(f"User turn {turn}.", case.filler_chars, human_markers),
        )
    ]

    tool_turn = bool(tool_markers) or (
        case.tool_every > 0 and (turn + 1) % case.tool_every == 0
    )
    if tool_turn:
        tool_call_id = f"{case.id}-call-{turn}"
        messages.extend(
            [
                AIMessage(
                    id=f"{case.id}-a-tool-{turn}",
                    name="parser",
                    content="",
                    tool_calls=[
                        {
                            "id": tool_call_id,
                            "name": "read_docs",
                            "args": {"query": f"synthetic turn {turn}"},
                        }
                    ],
                ),
                ToolMessage(
                    id=f"{case.id}-t-{turn}",
                    name="read_docs",
                    tool_call_id=tool_call_id,
                    content=_content(
                        f"Synthetic tool result {turn}.",
                        case.tool_payload_chars,
                        tool_markers,
                    ),
                ),
            ]
        )

    messages.append(
        AIMessage(
            id=f"{case.id}-a-{turn}",
            name="primary",
            content=_content(
                f"Assistant turn {turn} completed.",
                case.filler_chars,
                assistant_markers,
            ),
        )
    )
    return messages


def _content(prefix: str, filler_chars: int, markers: list[str]) -> str:
    marker_text = " ".join(markers)
    filler = "x" * filler_chars
    return " ".join(part for part in (prefix, marker_text, filler) if part)


def _apply_graph_update(
    state: dict[str, Any],
    update: dict[str, Any],
) -> dict[str, Any]:
    if not update:
        return state
    next_state = dict(state)
    message_update = update.get("messages")
    if message_update is not None:
        next_state["messages"] = add_messages(state.get("messages", []), message_update)
    for key, value in update.items():
        if key != "messages":
            next_state[key] = value
    return next_state


def _recall_probe(
    messages: tuple[BaseMessage, ...],
    markers: tuple[MarkerPlacement, ...],
) -> str | None:
    values = tuple(dict.fromkeys(marker.value for marker in markers))
    winner: str | None = None
    winner_position = (-1, -1)
    for message_index, message in enumerate(messages):
        payload = json.dumps(message_to_dict(message), ensure_ascii=False, sort_keys=True)
        for value in values:
            char_index = payload.rfind(value)
            position = (message_index, char_index)
            if char_index >= 0 and position > winner_position:
                winner = value
                winner_position = position
    return winner


def _measurement_payload(measurement: SessionMeasurement) -> dict[str, Any]:
    return {
        "answer_marker": measurement.answer_marker,
        "checkpoint_message_count": len(measurement.state.get("messages", [])),
        "checkpoint_bytes": measurement.checkpoint_bytes,
        "prompt_message_count": len(measurement.prompt_messages),
        "prompt_bytes": measurement.prompt_bytes,
        "approximate_input_tokens": measurement.approximate_input_tokens,
        "provider_input_tokens": None,
    }


def _marker_from_payload(payload: Any) -> MarkerPlacement:
    if not isinstance(payload, dict):
        raise ValueError("Context compaction marker must be an object.")
    try:
        role = payload["role"]
        marker = MarkerPlacement(
            value=_required_text(payload["value"], "marker.value"),
            turn=_non_negative_int(payload["turn"], "marker.turn"),
            role=role,
        )
    except KeyError:
        raise ValueError("Context compaction marker requires value, turn and role.") from None
    if marker.role not in {"human", "assistant", "tool"}:
        raise ValueError("Context compaction marker role is unsupported.")
    return marker


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"Context compaction eval {field_name} must be non-empty and trimmed.")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Context compaction eval {field_name} must be non-negative.")
    return value


def _binary(value: bool) -> float:
    return 1.0 if value else 0.0


def _reduction_ratio(baseline: int, compacted: int) -> float | None:
    if baseline <= 0:
        return None
    return 1 - (compacted / baseline)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    values = sorted(values)
    rank = (len(values) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return values[lower] * (1 - weight) + values[upper] * weight


__all__ = [
    "ContextCompactionCase",
    "MarkerPlacement",
    "evaluate_context_compaction_case",
    "load_context_compaction_cases",
]
