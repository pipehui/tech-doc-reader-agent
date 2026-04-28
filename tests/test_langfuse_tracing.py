from tech_doc_agent.app.core.settings import Settings
import tech_doc_agent.app.core.langfuse_tracing as tracing


class FakeLangfuse:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.flushed = False
        self.shutdown_called = False
        FakeLangfuse.instances.append(self)

    @staticmethod
    def create_trace_id(*, seed=None):
        return f"lf-{seed}"

    def get_trace_url(self, *, trace_id):
        return f"https://langfuse.test/project/traces/{trace_id}"

    def flush(self):
        self.flushed = True

    def shutdown(self):
        self.shutdown_called = True


class FakeCallbackHandler:
    def __init__(self, *, public_key=None, trace_context=None):
        self.public_key = public_key
        self.trace_context = trace_context


def _reset_fakes(monkeypatch):
    FakeLangfuse.instances.clear()
    monkeypatch.setattr(tracing, "_CLIENT", None)
    monkeypatch.setattr(tracing, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(tracing, "CallbackHandler", FakeCallbackHandler)


def test_build_langfuse_trace_returns_none_when_disabled(monkeypatch):
    _reset_fakes(monkeypatch)

    trace = tracing.build_langfuse_trace(
        Settings(LANGFUSE_ENABLED=False),
        "trace-local",
    )

    assert trace is None


def test_build_langfuse_trace_creates_callback_with_deterministic_trace_id(monkeypatch):
    _reset_fakes(monkeypatch)

    settings = Settings(
        LANGFUSE_ENABLED=True,
        LANGFUSE_PUBLIC_KEY="pk-test",
        LANGFUSE_SECRET_KEY="sk-test",
        LANGFUSE_BASE_URL="https://langfuse.test",
        LANGFUSE_ENVIRONMENT="test",
        LANGFUSE_RELEASE="v-test",
    )

    trace = tracing.build_langfuse_trace(settings, "trace-local")

    assert trace is not None
    assert trace.trace_id == "lf-trace-local"
    assert trace.trace_url == "https://langfuse.test/project/traces/lf-trace-local"
    assert trace.callback.public_key == "pk-test"
    assert trace.callback.trace_context == {"trace_id": "lf-trace-local"}
    assert FakeLangfuse.instances[0].kwargs["secret_key"] == "sk-test"
    assert FakeLangfuse.instances[0].kwargs["base_url"] == "https://langfuse.test"


def test_flush_and_shutdown_are_best_effort(monkeypatch):
    _reset_fakes(monkeypatch)
    settings = Settings(
        LANGFUSE_ENABLED=True,
        LANGFUSE_PUBLIC_KEY="pk-test",
        LANGFUSE_SECRET_KEY="sk-test",
    )

    tracing.build_langfuse_trace(settings, "trace-local")
    tracing.flush_langfuse(settings)
    tracing.shutdown_langfuse(settings)

    assert FakeLangfuse.instances[0].flushed is True
    assert FakeLangfuse.instances[0].shutdown_called is True
