from tech_doc_agent.app import bootstrap
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.runtime.approvals import InMemoryApprovalRepository


def test_build_chat_runtime_selects_redis_approval_repository(monkeypatch):
    repository = InMemoryApprovalRepository()
    calls = []
    settings = Settings(
        REDIS_URL="redis://runtime-test",
        GUARDRAIL_APPROVAL_TTL_SECONDS=123,
    )

    monkeypatch.setattr(
        bootstrap.RedisApprovalRepository,
        "from_url",
        lambda redis_url, **kwargs: calls.append((redis_url, kwargs)) or repository,
    )

    runtime = bootstrap.build_chat_runtime(settings)
    request = runtime.request_guardrail_approval(
        "session-1",
        "message",
        source="chat.message",
        risk_level="medium",
        findings=["rule"],
        user_id="user-a",
        namespace="docs",
    )

    assert runtime.settings is settings
    assert runtime._lifecycle.settings is settings
    assert calls == [("redis://runtime-test", {"ttl_seconds": 123})]
    assert repository.get("user-a:docs:session-1") == request
