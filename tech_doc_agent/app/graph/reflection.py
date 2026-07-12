from __future__ import annotations

from collections.abc import Mapping
import json
import re
from typing import Any, Literal

from langchain_core.messages import ToolMessage

from tech_doc_agent.app.core.observability import log_event

from .specs import ReflectionPolicy
from .state import State


ReflectionRoute = Literal["continue", "terminate", "budget_terminate"]
ReflectionTerminalReason = Literal[
    "non_repairable_error",
    "max_rounds_exhausted",
    "finalization_tool_call_blocked",
]

_SAFE_LOCATION_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_SAFE_ERROR_TYPE = re.compile(r"^[a-z0-9_.-]{1,80}$")


def reflection_active_reset() -> dict[str, Any]:
    return {
        "reflection_status": "idle",
        "reflection_tool": "",
        "reflection_error_code": "",
        "reflection_terminal_reason": "",
    }


def reflection_request_reset() -> dict[str, Any]:
    return {
        **reflection_active_reset(),
        "reflection_rounds_used": 0,
    }


def safe_validation_repair_context(error: BaseException) -> dict[str, Any]:
    """Extract public validation locations/types without input values or messages."""

    errors = getattr(error, "errors", None)
    if not callable(errors):
        return {}

    try:
        raw_issues = errors()
    except Exception:
        return {}
    if not isinstance(raw_issues, list):
        return {}

    issues = []
    for raw_issue in raw_issues[:8]:
        if not isinstance(raw_issue, Mapping):
            continue
        raw_location = raw_issue.get("loc", ())
        if not isinstance(raw_location, (list, tuple)):
            raw_location = ()
        location = [_safe_location_segment(segment) for segment in raw_location]
        raw_error_type = raw_issue.get("type")
        error_type = (
            raw_error_type
            if isinstance(raw_error_type, str) and _SAFE_ERROR_TYPE.fullmatch(raw_error_type)
            else "validation_error"
        )
        issues.append(
            {
                "location": location,
                "type": error_type,
            }
        )

    return {"validation_issues": issues} if issues else {}


def apply_reflection_policy(
    state: State,
    tool_update: dict[str, Any],
    policy: ReflectionPolicy,
) -> dict[str, Any]:
    messages = list(tool_update.get("messages", []) or [])
    error_messages = [message for message in messages if _is_error_tool_message(message)]
    if not error_messages:
        return {
            **tool_update,
            **reflection_active_reset(),
        }

    payloads = [_error_payload(message) for message in error_messages]
    repairable = all(
        payload.get("code") in policy.repairable_error_codes
        for payload in payloads
    )
    rounds_used = _nonnegative_int(state.get("reflection_rounds_used", 0))
    first_payload = payloads[0]
    tool_name = _tool_name(error_messages[0], first_payload)
    error_code = _safe_string(first_payload.get("code"), default="tool_error")

    if repairable and rounds_used < policy.max_rounds:
        next_round = rounds_used + 1
        reflection = {
            "action": "repair_arguments",
            "round": next_round,
            "max_rounds": policy.max_rounds,
            "instruction": (
                "Correct the tool name or arguments once using the public tool schema. "
                "Do not repeat unchanged arguments and do not invent hidden error details."
            ),
        }
        log_event(
            "reflection.started",
            agent=_current_step(state),
            tool=tool_name,
            error_code=error_code,
            reflection_round=next_round,
            max_rounds=policy.max_rounds,
            error_count=len(error_messages),
        )
        return {
            **tool_update,
            "messages": [
                _decorate_error_message(message, reflection)
                if _is_error_tool_message(message)
                else message
                for message in messages
            ],
            "reflection_rounds_used": next_round,
            "reflection_status": "repairing",
            "reflection_tool": tool_name,
            "reflection_error_code": error_code,
            "reflection_terminal_reason": "",
        }

    terminal_reason: ReflectionTerminalReason = (
        "max_rounds_exhausted" if repairable else "non_repairable_error"
    )
    if state.get("reflection_status") == "finalizing":
        terminal_reason = "finalization_tool_call_blocked"
        reflection = {
            "action": "stop_retry",
            "round": rounds_used,
            "max_rounds": policy.max_rounds,
            "reason": terminal_reason,
            "instruction": "The tool chain is closed. Do not issue another tool call for this step.",
        }
        log_event(
            "reflection.terminated",
            agent=_current_step(state),
            tool=tool_name,
            error_code=error_code,
            reflection_rounds_used=rounds_used,
            max_rounds=policy.max_rounds,
            reason=terminal_reason,
            error_count=len(error_messages),
        )
        return {
            **tool_update,
            "messages": [
                _decorate_error_message(message, reflection)
                if _is_error_tool_message(message)
                else message
                for message in messages
            ],
            "reflection_rounds_used": rounds_used,
            "reflection_status": "terminal",
            "reflection_tool": tool_name,
            "reflection_error_code": error_code,
            "reflection_terminal_reason": terminal_reason,
        }

    reflection = {
        "action": "finalize_without_tools",
        "round": rounds_used,
        "max_rounds": policy.max_rounds,
        "reason": terminal_reason,
        "instruction": (
            "Do not issue any further tool call in the current step. "
            "Use already available evidence to produce a partial result, or end/escalate the step."
        ),
    }
    log_event(
        "reflection.finalization_required",
        agent=_current_step(state),
        tool=tool_name,
        error_code=error_code,
        reflection_rounds_used=rounds_used,
        max_rounds=policy.max_rounds,
        reason=terminal_reason,
        error_count=len(error_messages),
    )
    return {
        **tool_update,
        "messages": [
            _decorate_error_message(message, reflection)
            if _is_error_tool_message(message)
            else message
            for message in messages
        ],
        "reflection_rounds_used": rounds_used,
        "reflection_status": "finalizing",
        "reflection_tool": tool_name,
        "reflection_error_code": error_code,
        "reflection_terminal_reason": terminal_reason,
    }


def route_after_tool_result(state: State) -> ReflectionRoute:
    if state.get("budget_status") == "terminating":
        return "budget_terminate"
    if state.get("reflection_status") == "terminal":
        return "terminate"
    return "continue"


def _decorate_error_message(message: Any, reflection: dict[str, Any]) -> Any:
    if not isinstance(message, ToolMessage):
        return message

    artifact = dict(message.artifact) if isinstance(message.artifact, dict) else {}
    error_payload = _error_payload(message)
    artifact["reflection"] = reflection
    content_payload = {
        **error_payload,
        "reflection": reflection,
    }
    repair_context = artifact.get("repair_context")
    if isinstance(repair_context, dict) and repair_context:
        content_payload["repair_context"] = repair_context
    return message.model_copy(
        update={
            "artifact": artifact,
            "content": json.dumps(content_payload, ensure_ascii=False, sort_keys=True),
        }
    )


def _is_error_tool_message(message: Any) -> bool:
    return isinstance(message, ToolMessage) and message.status == "error"


def _error_payload(message: Any) -> dict[str, Any]:
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict) and isinstance(artifact.get("error"), dict):
        return dict(artifact["error"])
    return {
        "status": "error",
        "code": "tool_error",
        "retryable": False,
        "safe_message": "The tool call failed.",
        "dependency": None,
        "tool": getattr(message, "name", None),
        "cause_type": "UnknownToolError",
    }


def _tool_name(message: Any, payload: dict[str, Any]) -> str:
    return _safe_string(getattr(message, "name", None) or payload.get("tool"), default="tool")


def _current_step(state: State) -> str:
    dialog_state = state.get("dialog_state", [])
    return dialog_state[-1] if dialog_state else "primary"


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _safe_string(value: Any, *, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _safe_location_segment(value: Any) -> str | int:
    if isinstance(value, bool):
        return "<field>"
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _SAFE_LOCATION_SEGMENT.fullmatch(value):
        return value
    return "<field>"


__all__ = [
    "ReflectionRoute",
    "ReflectionTerminalReason",
    "apply_reflection_policy",
    "reflection_active_reset",
    "reflection_request_reset",
    "route_after_tool_result",
    "safe_validation_repair_context",
]
