from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from tech_doc_agent.app.core.conversation_summary import ConversationSummary


@dataclass(frozen=True, slots=True)
class ExtractiveConversationSummarizer:
    """Build a bounded, auditable digest without copying raw tool payloads."""

    generator_id: str = "extractive-closed-turns-v1"
    max_entry_chars: int = 1_000

    def __post_init__(self) -> None:
        if self.max_entry_chars < 64:
            raise ValueError("Summary entries must allow at least 64 characters.")

    def summarize(
        self,
        *,
        previous: ConversationSummary | None,
        messages: Sequence[Any],
        max_chars: int,
    ) -> str:
        entries = [entry for message in messages if (entry := self._entry(message))]
        new_section = "\n".join(entries) or "[Closed turns contained only internal messages.]"
        if previous is None:
            return _bounded(new_section, max_chars)
        return _bounded_incremental(previous.content, new_section, max_chars)

    def _entry(self, message: Any) -> str:
        message_type = getattr(message, "type", None)
        if message_type == "human":
            return _bounded_entry(f"User: {_message_text(message)}", self.max_entry_chars)
        if message_type == "ai":
            agent = _safe_label(getattr(message, "name", None), "assistant")
            text = _message_text(message)
            tool_names = [
                name
                for tool_call in list(getattr(message, "tool_calls", ()) or ())
                if isinstance(tool_call, dict)
                and (name := _safe_label(tool_call.get("name"), ""))
            ]
            parts = []
            if text:
                parts.append(f"Assistant[{agent}]: {text}")
            if tool_names:
                parts.append(f"Assistant[{agent}] requested tools: {', '.join(tool_names)}.")
            return _bounded_entry(" ".join(parts), self.max_entry_chars)
        if message_type == "tool":
            tool_name = _safe_label(getattr(message, "name", None), "tool")
            status = _safe_label(getattr(message, "status", None), "completed")
            return f"Tool[{tool_name}] {status}."
        return ""


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(part.strip() for part in parts if part.strip())


def _safe_label(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    return value if value else fallback


def _bounded_entry(value: str, max_chars: int) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def _bounded(value: str, max_chars: int) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value

    marker = "\n[Earlier closed-turn summary was compacted.]\n"
    available = max_chars - len(marker)
    head_chars = max(1, available // 3)
    tail_chars = max(1, available - head_chars)
    return f"{value[:head_chars].rstrip()}{marker}{value[-tail_chars:].lstrip()}"


def _bounded_incremental(previous: str, new_section: str, max_chars: int) -> str:
    combined = f"{previous.strip()}\n{new_section.strip()}"
    if len(combined) <= max_chars:
        return combined

    marker = "\n[Earlier closed-turn summary was compacted.]\n"
    inner_marker = " … "
    available = max_chars - len(marker) - len(inner_marker)
    previous_chars = max(1, available // 3)
    new_chars = max(2, available - previous_chars)
    new_head_chars = max(1, new_chars // 2)
    new_tail_chars = max(1, new_chars - new_head_chars)
    return (
        f"{previous[:previous_chars].rstrip()}"
        f"{marker}"
        f"{new_section[:new_head_chars].rstrip()}"
        f"{inner_marker}"
        f"{new_section[-new_tail_chars:].lstrip()}"
    )


__all__ = ["ExtractiveConversationSummarizer"]
