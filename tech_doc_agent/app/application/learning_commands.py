from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite
from typing import Any

from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.tenant import TenantContext


@dataclass(frozen=True, slots=True)
class UpdateLearningStateCommand:
    tenant: TenantContext
    session_id: str
    tool_call_id: str
    knowledge: str
    timestamp: str
    score: float | None = None
    memory_kind: str | None = None
    memory_topic: str | None = None
    memory_content: str | None = None
    memory_confidence: float | None = None

    def __post_init__(self) -> None:
        for name in ("session_id", "tool_call_id", "knowledge", "timestamp"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(
                    "The learning update is missing required execution context.",
                    code="learning_command_invalid",
                    dependency="learning_state_repository",
                    cause_type=f"Missing{name.title().replace('_', '')}",
                )
        for name in ("score", "memory_confidence"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not isfinite(value)
            ):
                raise ValidationError(
                    "The learning update contains an invalid numeric value.",
                    code="learning_command_invalid",
                    dependency="learning_state_repository",
                    cause_type=f"Invalid{name.title().replace('_', '')}",
                )

    def idempotency_key(self) -> str:
        identity = (
            self.tenant.user_id,
            self.tenant.namespace,
            self.session_id,
            self.tool_call_id,
        )
        return _stable_digest(identity)

    def fingerprint(self) -> str:
        return _stable_digest(asdict(self))

    def owner_key(self) -> str:
        return _stable_digest(
            (
                self.tenant.user_id,
                self.tenant.namespace,
            )
        )


@dataclass(frozen=True, slots=True)
class UpdateLearningStateResult:
    learning_message: str
    memory_message: str
    memory_id: str | None = None
    replayed: bool = False

    @property
    def message(self) -> str:
        return f"{self.learning_message} {self.memory_message}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "learning_message": self.learning_message,
            "memory_message": self.memory_message,
            "memory_id": self.memory_id,
        }

    @classmethod
    def from_payload(
        cls,
        value: Any,
        *,
        replayed: bool,
    ) -> UpdateLearningStateResult:
        if not isinstance(value, dict):
            raise _corrupt_learning_state("InvalidCommandResult")
        learning_message = value.get("learning_message")
        memory_message = value.get("memory_message")
        memory_id = value.get("memory_id")
        if (
            not isinstance(learning_message, str)
            or not isinstance(memory_message, str)
            or (memory_id is not None and not isinstance(memory_id, str))
        ):
            raise _corrupt_learning_state("InvalidCommandResult")
        return cls(
            learning_message=learning_message,
            memory_message=memory_message,
            memory_id=memory_id,
            replayed=replayed,
        )


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _corrupt_learning_state(cause_type: str) -> ValidationError:
    return ValidationError(
        "The learning state snapshot is invalid.",
        code="learning_state_corrupt",
        retryable=False,
        dependency="file_repository",
        cause_type=cause_type,
    )


__all__ = [
    "UpdateLearningStateCommand",
    "UpdateLearningStateResult",
]
