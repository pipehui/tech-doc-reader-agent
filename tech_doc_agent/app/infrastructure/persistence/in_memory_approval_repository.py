from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from tech_doc_agent.app.application.approval_models import GuardrailApprovalRequest


@dataclass(slots=True)
class InMemoryApprovalRepository:
    """Process-local approval adapter for tests and non-production composition."""

    _items: dict[str, GuardrailApprovalRequest] = field(default_factory=dict, init=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def put(self, key: str, request: GuardrailApprovalRequest) -> None:
        with self._lock:
            self._items[key] = request

    def get(self, key: str) -> GuardrailApprovalRequest | None:
        with self._lock:
            return self._items.get(key)

    def pop(self, key: str) -> GuardrailApprovalRequest | None:
        with self._lock:
            return self._items.pop(key, None)

    def close(self) -> None:
        return None


__all__ = ["InMemoryApprovalRepository"]
