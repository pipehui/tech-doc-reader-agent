from types import SimpleNamespace

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
