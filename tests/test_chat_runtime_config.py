from types import SimpleNamespace

from redis.exceptions import BusyLoadingError

from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services import chat_runtime
from tech_doc_agent.app.services.chat_runtime import ChatRuntime


def test_build_config_adds_langfuse_callback_when_enabled(monkeypatch):
    runtime = ChatRuntime()
    runtime.settings = Settings(
        LANGGRAPH_RECURSION_LIMIT=42,
        LANGFUSE_ENABLED=True,
        LANGFUSE_PUBLIC_KEY="pk-test",
        LANGFUSE_SECRET_KEY="sk-test",
    )
    callback = object()

    monkeypatch.setattr(
        chat_runtime,
        "build_langfuse_trace",
        lambda settings, trace_id: SimpleNamespace(
            callback=callback,
            trace_id=f"lf-{trace_id}",
            trace_url=f"https://langfuse.test/traces/lf-{trace_id}",
        ),
    )

    with trace_context(trace_id="trace-local"):
        config = runtime.build_config(
            "session-1",
            operation="chat",
            with_callbacks=True,
        )

    assert config["callbacks"] == [callback]
    assert config["recursion_limit"] == 42
    assert config["metadata"]["trace_id"] == "trace-local"
    assert config["metadata"]["langfuse_trace_id"] == "lf-trace-local"
    assert config["metadata"]["langfuse_session_id"] == "session-1"
    assert config["run_name"] == "tech_doc_agent.chat"


def test_build_config_omits_callbacks_for_state_reads():
    runtime = ChatRuntime()
    runtime.settings = Settings()

    config = runtime.build_config("session-1")

    assert "callbacks" not in config
    assert config["metadata"]["langfuse_session_id"] == "session-1"
    assert config["run_name"] == "tech_doc_agent.state"


def test_enter_retries_redis_busy_loading_during_checkpointer_setup(monkeypatch):
    class FakeCheckpointer:
        setup_calls = 0
        close_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            type(self).close_calls += 1

        def setup(self):
            type(self).setup_calls += 1
            if type(self).setup_calls == 1:
                raise BusyLoadingError("Redis is loading the dataset in memory")

    class FakeRedisSaver:
        @staticmethod
        def from_conn_string(redis_url):
            assert redis_url == "redis://test"
            return FakeCheckpointer()

    monkeypatch.setattr(chat_runtime.AppResources, "create", lambda settings: SimpleNamespace())
    monkeypatch.setattr(chat_runtime, "set_app_resources", lambda resources: None)
    monkeypatch.setattr(chat_runtime, "reset_app_resources", lambda: None)
    monkeypatch.setattr(chat_runtime, "shutdown_langfuse", lambda settings: None)
    monkeypatch.setattr(chat_runtime, "build_multi_agentic_graph", lambda checkpointer: {"checkpointer": checkpointer})
    monkeypatch.setattr(chat_runtime, "RedisSaver", FakeRedisSaver)
    monkeypatch.setattr(chat_runtime, "sleep", lambda seconds: None)

    runtime = ChatRuntime()
    runtime.settings = Settings(
        REDIS_URL="redis://test",
        REDIS_SETUP_MAX_ATTEMPTS=2,
        REDIS_SETUP_RETRY_SECONDS=0,
    )

    with runtime as active:
        assert active.graph == {"checkpointer": active.checkpointer}

    assert FakeCheckpointer.setup_calls == 2
    assert FakeCheckpointer.close_calls == 2
