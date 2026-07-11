from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import sleep
from typing import Any

from redis.exceptions import BusyLoadingError

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings


ResourceFactory = Callable[[Settings], Any]
ResourcePublisher = Callable[[Any], None]
ResourceResetter = Callable[[], None]
CheckpointerContextFactory = Callable[[str], Any]
GraphFactory = Callable[[Any], Any]


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
    resource_publisher: ResourcePublisher
    resource_resetter: ResourceResetter
    checkpointer_context_factory: CheckpointerContextFactory
    graph_factory: GraphFactory
    event_logger: Callable[..., None] = field(default_factory=lambda: log_event)
    sleeper: Callable[[float], None] = field(default_factory=lambda: sleep)
    resources: Any | None = field(default=None, init=False)
    checkpointer: Any | None = field(default=None, init=False)
    graph: Any | None = field(default=None, init=False)
    _checkpointer_cm: Any | None = field(default=None, init=False, repr=False)
    _resources_published: bool = field(default=False, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    def start(self) -> RuntimeLifecycle:
        if self._started:
            raise RuntimeError("Runtime lifecycle is already started.")

        try:
            self.resources = self.resource_factory(self.settings)
            self._resources_published = True
            self.resource_publisher(self.resources)
            self._setup_checkpointer_with_retry()
            self.graph = self.graph_factory(self.checkpointer)
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
                    raise
                self.event_logger(
                    "redis.checkpointer.setup.retry",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    retry_seconds=retry_seconds,
                    error_type=type(exc).__name__,
                    error=str(exc),
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
            try:
                if self._resources_published:
                    self.resource_resetter()
            finally:
                self._resources_published = False
                self.resources = None
