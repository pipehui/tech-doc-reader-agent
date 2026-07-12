from collections.abc import Iterable

from fastapi.sse import ServerSentEvent

from tech_doc_agent.app.core.observability import log_event

from .agent_metadata import AGENT_NODE_NAMES
from .events import sse_event
from .message_translator import extract_text_from_content
from .parts import extract_update_data


TRANSITION_PREFIXES = (
    ("enter_", "enter"),
    ("finish_", "finish"),
    ("leave_", "leave"),
)


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


def _structured_result_events(
    node_name: str,
    node_update: dict,
) -> Iterable[ServerSentEvent]:
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


def _budget_terminated_event(
    node_name: str,
    node_update: dict,
) -> ServerSentEvent | None:
    termination = node_update.get("budget_termination")
    usage = node_update.get("budget_usage")
    if (
        node_update.get("budget_status") != "terminated"
        or not isinstance(termination, dict)
        or not termination
    ):
        return None
    return sse_event(
        "budget_terminated",
        {
            "node": node_name,
            "termination": termination,
            "usage": usage if isinstance(usage, dict) else None,
        },
    )


def _budget_started_event(
    node_name: str,
    node_update: dict,
) -> ServerSentEvent | None:
    usage = node_update.get("budget_usage")
    if node_update.get("budget_status") != "active" or not isinstance(usage, dict):
        return None
    return sse_event(
        "budget_started",
        {
            "node": node_name,
            "status": "active",
            "usage": usage,
        },
    )


def _context_metrics_update_event(
    node_name: str,
    node_update: dict,
) -> ServerSentEvent | None:
    delta = node_update.get("context_metrics_delta")
    metrics = node_update.get("context_metrics")
    if (
        not isinstance(delta, dict)
        or delta.get("kind") not in {"reset", "assistant"}
        or not isinstance(metrics, dict)
    ):
        return None
    return sse_event(
        "context_metrics_update",
        {
            "node": node_name,
            "delta": delta,
            "metrics": metrics,
        },
    )


def _provider_retry_update_event(
    node_name: str,
    node_update: dict,
) -> ServerSentEvent | None:
    delta = node_update.get("provider_retry_usage_delta")
    usage = node_update.get("provider_retry_usage")
    if (
        not isinstance(delta, dict)
        or delta.get("kind") not in {"reset", "operations"}
        or not isinstance(usage, dict)
    ):
        return None
    return sse_event(
        "provider_retry_update",
        {
            "node": node_name,
            "delta": delta,
            "usage": usage,
        },
    )


def iter_update_events(part) -> Iterable[ServerSentEvent]:
    update_data = extract_update_data(part)

    for node_name, node_update in update_data.items():
        if not isinstance(node_name, str):
            log_event(
                "sse.translation.ignored",
                reason="invalid_node_name",
                node_type=type(node_name).__name__,
            )
            continue
        transition_payload = _agent_transition_payload(node_name)
        if transition_payload:
            yield sse_event("agent_transition", transition_payload)

        if not isinstance(node_update, dict):
            log_event(
                "sse.translation.ignored",
                reason="invalid_node_update",
                node=node_name,
                update_type=type(node_update).__name__,
            )
            continue

        plan_payload = _plan_update_payload(node_name, node_update)
        if plan_payload:
            yield sse_event("plan_update", plan_payload)

        yield from _structured_result_events(node_name, node_update)

        usage_event = _usage_update_event(node_name, node_update)
        if usage_event is not None:
            yield usage_event

        budget_event = _budget_terminated_event(node_name, node_update)
        if budget_event is not None:
            yield budget_event

        budget_started = _budget_started_event(node_name, node_update)
        if budget_started is not None:
            yield budget_started

        context_event = _context_metrics_update_event(node_name, node_update)
        if context_event is not None:
            yield context_event

        provider_retry_event = _provider_retry_update_event(node_name, node_update)
        if provider_retry_event is not None:
            yield provider_retry_event

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
            elif raw_type != "remove":
                log_event(
                    "sse.translation.ignored",
                    reason="unsupported_update_message",
                    node=node_name,
                    message_type=type(message).__name__,
                )


__all__ = ["iter_update_events"]
