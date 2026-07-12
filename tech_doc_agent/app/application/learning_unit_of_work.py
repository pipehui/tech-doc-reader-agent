from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import threading
from typing import Any, Protocol

from tech_doc_agent.app.application.learning_commands import (
    UpdateLearningStateCommand,
    UpdateLearningStateResult,
)
from tech_doc_agent.app.application.learning_models import LearningRecord, MemoryFragment
from tech_doc_agent.app.core.errors import Conflict


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


class LearningStateRepositoryPort(Protocol):
    def load(self) -> LearningStateSnapshot | None: ...

    def save(self, snapshot: LearningStateSnapshot) -> LearningStateSnapshot: ...


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


__all__ = [
    "LearningStateRepositoryPort",
    "LearningStateSnapshot",
    "LearningStateUnitOfWork",
]
