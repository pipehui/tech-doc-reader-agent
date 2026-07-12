from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import sleep
from typing import Any

from redis.exceptions import BusyLoadingError

from tech_doc_agent.app.core.errors import classify_error, safe_error_fields
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings


ResourceFactory = Callable[[Settings], Any]
CheckpointerContextFactory = Callable[[str], Any]
GraphFactory = Callable[[Any, Any], Any]


def _is_retryable_redis_startup_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        isinstance(exc, BusyLoadingError)
        or "redis is loading" in message
        or "loading the dataset" in message
    )


@dataclass(slots=True)
class RuntimeLifecycle:
    settings: Settings
    resource_factory: ResourceFactory
    checkpointer_context_factory: CheckpointerContextFactory
    graph_factory: GraphFactory
    event_logger: Callable[..., None] = field(default_factory=lambda: log_event)
    sleeper: Callable[[float], None] = field(default_factory=lambda: sleep)
    resources: Any | None = field(default=None, init=False)
    checkpointer: Any | None = field(default=None, init=False)
    graph: Any | None = field(default=None, init=False)
    _checkpointer_cm: Any | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    def start(self) -> RuntimeLifecycle:
        if self._started:
            raise RuntimeError("Runtime lifecycle is already started.")

        try:
            self.resources = self.resource_factory(self.settings)
            self._setup_checkpointer_with_retry()
            self.graph = self.graph_factory(self.checkpointer, self.resources)
            self._started = True
            return self
        except Exception:
            self.close()
            raise

    def _setup_checkpointer_with_retry(self) -> None:
        max_attempts = max(1, int(self.settings.REDIS_SETUP_MAX_ATTEMPTS))
        retry_seconds = max(0.0, float(self.settings.REDIS_SETUP_RETRY_SECONDS))

        for attempt in range(1, max_attempts + 1):
            self._checkpointer_cm = self.checkpointer_context_factory(self.settings.REDIS_URL)
            try:
                self.checkpointer = self._checkpointer_cm.__enter__()
                self.checkpointer.setup()
                if attempt > 1:
                    self.event_logger("redis.checkpointer.setup.ready", attempt=attempt)
                return
            except Exception as exc:
                self._close_checkpointer()
                if attempt >= max_attempts or not _is_retryable_redis_startup_error(exc):
                    raise classify_error(exc, dependency="redis") from exc
                self.event_logger(
                    "redis.checkpointer.setup.retry",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    retry_seconds=retry_seconds,
                    **safe_error_fields(exc, dependency="redis"),
                )
                self.sleeper(retry_seconds)

    def _close_checkpointer(self, exc_type=None, exc=None, tb=None) -> None:
        checkpointer_cm = self._checkpointer_cm
        self._checkpointer_cm = None
        self.checkpointer = None
        if checkpointer_cm is not None:
            checkpointer_cm.__exit__(exc_type, exc, tb)

    def close(self, exc_type=None, exc=None, tb=None) -> None:
        try:
            self._close_checkpointer(exc_type, exc, tb)
        finally:
            self.graph = None
            self._started = False
            self.resources = None
