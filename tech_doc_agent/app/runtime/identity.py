from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from tech_doc_agent.app.core.settings import Settings


class RuntimeExecutionIdentityPort(Protocol):
    @property
    def fingerprint(self) -> str: ...

    def to_payload(self) -> dict[str, Any]: ...


RuntimeExecutionIdentityFactory = Callable[
    [Settings],
    RuntimeExecutionIdentityPort,
]


__all__ = [
    "RuntimeExecutionIdentityFactory",
    "RuntimeExecutionIdentityPort",
]
