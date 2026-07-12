from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
from math import isfinite
import threading
from typing import Any, Callable, Protocol

from tech_doc_agent.app.core.errors import Conflict, ValidationError
from tech_doc_agent.app.core.tenant import TenantContext
from tech_doc_agent.app.application.learning_models import LearningRecord, MemoryFragment


@dataclass(slots=True)
class LearningStateSnapshot:
    records: list[LearningRecord] = field(default_factory=list)
    memories: list[MemoryFragment] = field(default_factory=list)
    processed_commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    generation: str | None = None

    def clone(self) -> LearningStateSnapshot:
        return LearningStateSnapshot(
            records=list(self.records),
            memories=list(self.memories),
            processed_commands=deepcopy(self.processed_commands),
            generation=self.generation,
        )


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
                isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value)
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


class LearningStateRepositoryPort(Protocol):
    def load(self) -> LearningStateSnapshot | None: ...

    def save(self, snapshot: LearningStateSnapshot) -> LearningStateSnapshot: ...


class LearningRecordReaderPort(Protocol):
    def query_records(
        self,
        query: str,
        *,
        user_id: str,
        namespace: str,
    ) -> Sequence[LearningRecord]: ...

    def list_records(
        self,
        *,
        user_id: str,
        namespace: str,
    ) -> Sequence[LearningRecord]: ...


class MemoryReaderPort(Protocol):
    def query_memories(
        self,
        query: str,
        *,
        user_id: str,
        namespace: str,
        limit: int,
    ) -> Sequence[MemoryFragment]: ...

    def recent_memories(
        self,
        *,
        user_id: str,
        namespace: str,
        limit: int,
    ) -> Sequence[MemoryFragment]: ...


class LearningStateCommandPort(Protocol):
    def update(
        self,
        command: UpdateLearningStateCommand,
    ) -> UpdateLearningStateResult: ...


class LearningRecordUpdaterPort(Protocol):
    def prepare_upsert_record(
        self,
        records: Sequence[LearningRecord],
        *,
        knowledge: str,
        timestamp: str,
        score: float | None,
        tenant: TenantContext,
    ) -> tuple[list[LearningRecord], str]: ...


class MemoryUpdaterPort(Protocol):
    def prepare_upsert_memory(
        self,
        memories: Sequence[MemoryFragment],
        *,
        kind: str,
        topic: str,
        content: str,
        confidence: float | None,
        source_session_id: str,
        tenant: TenantContext,
        timestamp: str,
    ) -> tuple[list[MemoryFragment], MemoryFragment]: ...


class LearningStateUnitOfWork:
    """Keep active state unchanged until one complete snapshot is published."""

    def __init__(self, repository: LearningStateRepositoryPort) -> None:
        self.repository = repository
        self._snapshot = LearningStateSnapshot()
        self._lock = threading.Lock()

    @property
    def records(self) -> tuple[LearningRecord, ...]:
        with self._lock:
            return tuple(self._snapshot.records)

    @property
    def memories(self) -> tuple[MemoryFragment, ...]:
        with self._lock:
            return tuple(self._snapshot.memories)

    @property
    def generation(self) -> str | None:
        with self._lock:
            return self._snapshot.generation

    @property
    def processed_command_count(self) -> int:
        with self._lock:
            return len(self._snapshot.processed_commands)

    def replace_records(self, records: Sequence[LearningRecord]) -> None:
        with self._lock:
            candidate = self._snapshot.clone()
            candidate.records = list(records)
            self._snapshot = candidate

    def replace_memories(self, memories: Sequence[MemoryFragment]) -> None:
        with self._lock:
            candidate = self._snapshot.clone()
            candidate.memories = list(memories)
            self._snapshot = candidate

    def load(self) -> bool:
        loaded = self.repository.load()
        if loaded is None:
            return False
        with self._lock:
            self._snapshot = loaded
        return True

    def save(self) -> bool:
        with self._lock:
            candidate = self._snapshot.clone()
            saved = self.repository.save(candidate)
            self._snapshot = saved
        return True

    def execute(
        self,
        command: UpdateLearningStateCommand,
        mutation: Callable[[LearningStateSnapshot], UpdateLearningStateResult],
    ) -> UpdateLearningStateResult:
        key = command.idempotency_key()
        fingerprint = command.fingerprint()
        with self._lock:
            existing = self._snapshot.processed_commands.get(key)
            if existing is not None:
                if existing.get("fingerprint") != fingerprint:
                    raise Conflict(
                        "The tool call id was already used for a different learning update.",
                        code="learning_idempotency_conflict",
                        dependency="learning_state_repository",
                        cause_type="IdempotencyFingerprintMismatch",
                    )
                return UpdateLearningStateResult.from_payload(
                    existing.get("result"),
                    replayed=True,
                )

            candidate = self._snapshot.clone()
            result = mutation(candidate)
            candidate.processed_commands[key] = {
                "fingerprint": fingerprint,
                "owner_key": command.owner_key(),
                "completed_at": datetime.now(UTC).isoformat(),
                "result": result.to_payload(),
            }
            saved = self.repository.save(candidate)
            self._snapshot = saved
            return result


class LearningStateService:
    def __init__(
        self,
        unit_of_work: LearningStateUnitOfWork,
        learning_records: LearningRecordUpdaterPort,
        memories: MemoryUpdaterPort,
    ) -> None:
        self.unit_of_work = unit_of_work
        self.learning_records = learning_records
        self.memories = memories

    def update(
        self,
        command: UpdateLearningStateCommand,
    ) -> UpdateLearningStateResult:
        def mutate(snapshot: LearningStateSnapshot) -> UpdateLearningStateResult:
            snapshot.records, learning_message = self.learning_records.prepare_upsert_record(
                snapshot.records,
                knowledge=command.knowledge,
                timestamp=command.timestamp,
                score=command.score,
                tenant=command.tenant,
            )

            memory_message = "No memory fragment written."
            memory_id = None
            if command.memory_content and command.memory_content.strip():
                snapshot.memories, memory = self.memories.prepare_upsert_memory(
                    snapshot.memories,
                    kind=command.memory_kind or "learned",
                    topic=command.memory_topic or command.knowledge,
                    content=command.memory_content,
                    confidence=command.memory_confidence,
                    source_session_id=command.session_id,
                    tenant=command.tenant,
                    timestamp=datetime.now(UTC).isoformat(),
                )
                memory_id = memory.id
                memory_message = f"Memory '{memory_id}' has been upserted."

            return UpdateLearningStateResult(
                learning_message=learning_message,
                memory_message=memory_message,
                memory_id=memory_id,
            )

        return self.unit_of_work.execute(command, mutate)


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
    "LearningRecordReaderPort",
    "LearningRecordUpdaterPort",
    "LearningStateCommandPort",
    "LearningStateRepositoryPort",
    "LearningStateService",
    "LearningStateSnapshot",
    "LearningStateUnitOfWork",
    "MemoryUpdaterPort",
    "MemoryReaderPort",
    "UpdateLearningStateCommand",
    "UpdateLearningStateResult",
]
