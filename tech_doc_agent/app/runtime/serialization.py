from typing import Any


class MessageSerializer:
    """Convert LangChain-style messages into stable API-facing dictionaries."""

    _ROLE_BY_MESSAGE_TYPE = {
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
        "system": "system",
    }

    def extract_text_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif "text" in item:
                        parts.append(item.get("text", ""))
            return "".join(parts)

        return ""

    def serialize(self, message: Any) -> dict[str, Any]:
        raw_type = getattr(message, "type", "unknown")

        return {
            "id": getattr(message, "id", None),
            "role": self._ROLE_BY_MESSAGE_TYPE.get(raw_type, raw_type),
            "raw_type": raw_type,
            "content": self.extract_text_content(getattr(message, "content", "")),
            "name": getattr(message, "name", None),
            "tool_call_id": getattr(message, "tool_call_id", None),
            "tool_calls": getattr(message, "tool_calls", []) or [],
        }

    def to_history_view_item(self, message: Any) -> dict[str, Any] | None:
        raw_type = getattr(message, "type", "unknown")
        content = self.extract_text_content(getattr(message, "content", ""))

        if raw_type == "human":
            return {
                "id": getattr(message, "id", None),
                "role": "user",
                "kind": "message",
                "content": content,
            }

        if raw_type == "ai":
            if not content.strip():
                return None
            return {
                "id": getattr(message, "id", None),
                "role": "assistant",
                "kind": "message",
                "content": content,
                "name": getattr(message, "name", None),
            }

        if raw_type == "tool":
            return {
                "id": getattr(message, "id", None),
                "role": "tool",
                "kind": "tool_result",
                "content": content,
                "tool_call_id": getattr(message, "tool_call_id", None),
                "name": getattr(message, "name", None),
            }

        return None
