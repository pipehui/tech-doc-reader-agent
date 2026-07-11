import pytest

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.runtime.approvals import InMemoryApprovalRepository
from tech_doc_agent.app.runtime.lifecycle import RuntimeLifecycle
from tech_doc_agent.app.services import chat_runtime
from tech_doc_agent.app.services.chat_runtime import ChatRuntime


class ClosingRepository(InMemoryApprovalRepository):
    def __init__(self):
        super().__init__()
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class RecordingCheckpointer:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("checkpointer.enter")
        return self

    def setup(self):
        self.events.append("checkpointer.setup")

    def __exit__(self, exc_type, exc, tb):
        self.events.append("checkpointer.exit")


def test_lifecycle_start_and_close_are_explicit_and_close_is_idempotent():
    events = []
    checkpointer = RecordingCheckpointer(events)
    lifecycle = RuntimeLifecycle(
        settings=Settings(REDIS_URL="redis://lifecycle-test"),
        resource_factory=lambda settings: events.append("resources.create") or object(),
        resource_publisher=lambda resources: events.append("resources.publish"),
        resource_resetter=lambda: events.append("resources.reset"),
        checkpointer_context_factory=lambda redis_url: checkpointer,
        graph_factory=lambda active_checkpointer: {"checkpointer": active_checkpointer},
    )

    assert lifecycle.start() is lifecycle
    assert lifecycle.graph == {"checkpointer": checkpointer}
    with pytest.raises(RuntimeError, match="already started"):
        lifecycle.start()

    lifecycle.close()
    lifecycle.close()

    assert events == [
        "resources.create",
        "resources.publish",
        "checkpointer.enter",
        "checkpointer.setup",
        "checkpointer.exit",
        "resources.reset",
    ]
    assert lifecycle.resources is None
    assert lifecycle.checkpointer is None
    assert lifecycle.graph is None


def test_runtime_start_failure_closes_checkpointer_resources_and_approval_repository(monkeypatch):
    events = []
    repository = ClosingRepository()
    checkpointer = RecordingCheckpointer(events)

    class FakeRedisSaver:
        @staticmethod
        def from_conn_string(redis_url):
            events.append(("checkpointer.create", redis_url))
            return checkpointer

    monkeypatch.setattr(
        chat_runtime.AppResources,
        "create",
        lambda settings: events.append("resources.create") or object(),
    )
    monkeypatch.setattr(
        chat_runtime,
        "set_app_resources",
        lambda resources: events.append("resources.publish"),
    )
    monkeypatch.setattr(
        chat_runtime,
        "reset_app_resources",
        lambda: events.append("resources.reset"),
    )
    monkeypatch.setattr(chat_runtime, "RedisSaver", FakeRedisSaver)
    monkeypatch.setattr(
        chat_runtime,
        "build_multi_agentic_graph",
        lambda checkpointer: (_ for _ in ()).throw(RuntimeError("graph build failed")),
    )

    runtime = ChatRuntime(
        approval_repository=repository,
        settings=Settings(REDIS_URL="redis://lifecycle-test"),
    )

    with pytest.raises(RuntimeError, match="graph build failed"):
        runtime.__enter__()

    assert events == [
        "resources.create",
        "resources.publish",
        ("checkpointer.create", "redis://lifecycle-test"),
        "checkpointer.enter",
        "checkpointer.setup",
        "checkpointer.exit",
        "resources.reset",
    ]
    assert repository.close_calls == 1


def test_langfuse_shutdown_failure_does_not_skip_other_runtime_cleanup(monkeypatch):
    events = []
    repository = ClosingRepository()
    checkpointer = RecordingCheckpointer(events)
    monkeypatch.setattr(
        chat_runtime,
        "reset_app_resources",
        lambda: events.append("resources.reset"),
    )
    runtime = ChatRuntime(
        approval_repository=repository,
        settings=Settings(LANGFUSE_ENABLED=False),
    )
    runtime._lifecycle._checkpointer_cm = checkpointer
    runtime.checkpointer = checkpointer
    runtime._lifecycle._resources_published = True

    monkeypatch.setattr(
        chat_runtime,
        "shutdown_langfuse",
        lambda settings: (_ for _ in ()).throw(RuntimeError("shutdown failed")),
    )
    with pytest.raises(RuntimeError, match="shutdown failed"):
        runtime.__exit__(None, None, None)

    assert events == ["checkpointer.exit", "resources.reset"]
    assert repository.close_calls == 1
