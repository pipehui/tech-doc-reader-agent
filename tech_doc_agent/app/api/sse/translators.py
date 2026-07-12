import json
from collections.abc import Iterable

from fastapi.sse import ServerSentEvent

from .events import sse_event


AGENT_NODE_NAMES = {
    "primary",
    "primary_assistant",
    "parser",
    "relation",
    "explanation",
    "examination",
    "summary",
}
TRANSITION_PREFIXES = (
    ("enter_", "enter"),
    ("finish_", "finish"),
    ("leave_", "leave"),
)


def infer_agent_from_metadata(metadata: dict) -> str | None:
    node_name = metadata.get("langgraph_node")
    if node_name:
        return node_name

    for key in ("langgraph_checkpoint_ns", "checkpoint_ns"):
        checkpoint_ns = metadata.get(key)
        if isinstance(checkpoint_ns, str) and checkpoint_ns:
            candidate = checkpoint_ns.split(":", 1)[0]
            if candidate in AGENT_NODE_NAMES:
                return candidate

    path = metadata.get("langgraph_path")
    if isinstance(path, list):
        for item in reversed(path):
            if isinstance(item, str) and item in AGENT_NODE_NAMES:
                return item
            if isinstance(item, (list, tuple)):
                for part in reversed(item):
                    if isinstance(part, str) and part in AGENT_NODE_NAMES:
                        return part

    return None


def extract_text_from_chunk(msg_chunk) -> str:
    content = getattr(msg_chunk, "content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(item.get("text", ""))
        return "".join(parts)

    return ""


def extract_text_from_content(content) -> str:
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
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "".join(parts)

    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)

    return str(content) if content is not None else ""


def _tool_result_payload(message, node_name: str) -> dict:
    content = extract_text_from_content(getattr(message, "content", ""))
    raw_status = getattr(message, "status", "success")
    status = "error" if raw_status == "error" else "success"
    artifact = getattr(message, "artifact", None)
    raw_error = artifact.get("error") if isinstance(artifact, dict) else None
    error_payload = raw_error if isinstance(raw_error, dict) else {}

    if status == "error":
        structured_safe_message = error_payload.get("safe_message")
        if isinstance(structured_safe_message, str) and structured_safe_message:
            safe_message = structured_safe_message
        else:
            safe_message = "Tool execution failed."
            content = safe_message
        code = error_payload.get("code")
        if not isinstance(code, str) or not code:
            code = "tool_execution_failed"
        retryable = error_payload.get("retryable")
        retryable = retryable if isinstance(retryable, bool) else False
        dependency = _optional_string(error_payload.get("dependency"))
        cause_type = _optional_string(error_payload.get("cause_type"))
    else:
        safe_message = None
        code = None
        retryable = None
        dependency = None
        cause_type = None

    message_name = _optional_string(getattr(message, "name", None))
    error_tool = _optional_string(error_payload.get("tool"))

    return {
        "agent": node_name,
        "node": node_name,
        "tool": message_name or error_tool,
        "tool_call_id": getattr(message, "tool_call_id", None),
        "content": content,
        "status": status,
        "error": safe_message,
        "safe_message": safe_message,
        "code": code,
        "retryable": retryable,
        "dependency": dependency,
        "cause_type": cause_type,
    }


def _optional_string(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _agent_transition_payload(node_name: str) -> dict | None:
    for prefix, phase in TRANSITION_PREFIXES:
        if not node_name.startswith(prefix):
            continue

        agent = node_name[len(prefix) :]
        if agent not in AGENT_NODE_NAMES:
            return None

        return {
            "phase": phase,
            "agent": agent,
        }

    return None


def _plan_update_payload(node_name: str, node_update: dict) -> dict:
    if node_name != "store_plan" and not node_name.startswith("finish_"):
        return {}

    payload = {}
    if "workflow_plan" in node_update:
        payload["plan"] = node_update["workflow_plan"]
    if "plan_index" in node_update:
        payload["plan_index"] = node_update["plan_index"]
    if "learning_target" in node_update:
        payload["learning_target"] = node_update["learning_target"]

    return payload


def _structured_result_events(node_name: str, node_update: dict) -> Iterable[ServerSentEvent]:
    for result_key in ("parser_result", "relation_result"):
        result = node_update.get(result_key)
        if not isinstance(result, dict):
            continue

        yield sse_event(
            "structured_result",
            {
                "node": node_name,
                "result_key": result_key,
                "result": result,
                "parsed": bool(result.get("parsed")),
            },
        )


def _usage_update_event(node_name: str, node_update: dict) -> ServerSentEvent | None:
    delta = node_update.get("budget_usage_delta")
    usage = node_update.get("budget_usage")
    if (
        not isinstance(delta, dict)
        or delta.get("kind") not in {"llm", "tool"}
        or not isinstance(usage, dict)
    ):
        return None
    return sse_event(
        "usage_update",
        {
            "node": node_name,
            "delta": delta,
            "usage": usage,
        },
    )


def stream_part_type_and_data(part) -> tuple[str | None, object]:
    if isinstance(part, dict):
        return part.get("type"), part.get("data")

    if isinstance(part, (tuple, list)) and len(part) == 2:
        return part[0], part[1]

    return None, None


def _extract_update_data(part) -> dict:
    if isinstance(part, dict):
        update_data = part.get("data", part)
    elif isinstance(part, (tuple, list)) and len(part) == 2:
        update_data = part[1]
    else:
        update_data = {}

    return update_data if isinstance(update_data, dict) else {}


def extract_message_part_data(part_data) -> tuple[object, dict] | None:
    if not isinstance(part_data, (tuple, list)) or len(part_data) != 2:
        return None

    msg_chunk, metadata = part_data
    return msg_chunk, metadata if isinstance(metadata, dict) else {}


def iter_update_events(part) -> Iterable[ServerSentEvent]:
    update_data = _extract_update_data(part)

    for node_name, node_update in update_data.items():
        transition_payload = _agent_transition_payload(node_name)
        if transition_payload:
            yield sse_event("agent_transition", transition_payload)

        if not isinstance(node_update, dict):
            continue

        plan_payload = _plan_update_payload(node_name, node_update)
        if plan_payload:
            yield sse_event("plan_update", plan_payload)

        yield from _structured_result_events(node_name, node_update)

        usage_event = _usage_update_event(node_name, node_update)
        if usage_event is not None:
            yield usage_event

        messages = node_update.get("messages", [])
        for message in messages:
            raw_type = getattr(message, "type", None)
            message_agent = getattr(message, "name", None) or node_name

            if raw_type == "ai":
                content = extract_text_from_content(getattr(message, "content", ""))
                if content.strip():
                    yield sse_event(
                        "agent_message",
                        {
                            "agent": message_agent,
                            "node": node_name,
                            "message_id": getattr(message, "id", None),
                            "content": content,
                        },
                    )

                tool_calls = getattr(message, "tool_calls", []) or []
                for tool_call in tool_calls:
                    yield sse_event(
                        "tool_call",
                        {
                            "agent": message_agent,
                            "node": node_name,
                            "tool": tool_call.get("name"),
                            "args": tool_call.get("args", {}),
                            "tool_call_id": tool_call.get("id"),
                        },
                    )

            elif raw_type == "tool":
                yield sse_event(
                    "tool_result",
                    _tool_result_payload(message, node_name),
                )
