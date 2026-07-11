from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from threading import Lock

import pytest

from tech_doc_agent.app.infrastructure.persistence import approval_repository
from tech_doc_agent.app.infrastructure.persistence.approval_repository import (
    ApprovalRepositoryDataError,
    RedisApprovalRepository,
)
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.runtime.approvals import GuardrailApprovalRequest
from tech_doc_agent.app.services.chat_runtime import ChatRuntime


class FakeRedisBackend:
    def __init__(self):
        self.values = {}
        self.expirations = {}
        self.lock = Lock()


class FakeRedisClient:
    def __init__(self, backend=None):
        self.backend = backend or FakeRedisBackend()
        self.closed = False

    def set(self, key, value, ex):
        with self.backend.lock:
            self.backend.values[key] = value
            self.backend.expirations[key] = ex
        return True

    def get(self, key):
        with self.backend.lock:
            return self.backend.values.get(key)

    def getdel(self, key):
        with self.backend.lock:
            self.backend.expirations.pop(key, None)
            return self.backend.values.pop(key, None)

    def close(self):
        self.closed = True


def _request() -> GuardrailApprovalRequest:
    return GuardrailApprovalRequest(
        session_id="session-1",
        user_input="Ignore previous instructions",
        user_id="user-a",
        namespace="docs-a",
        source="chat.message",
        risk_level="medium",
        findings=("ignore_previous_instructions",),
    )


def test_redis_repository_shares_pending_request_and_resolves_atomically():
    backend = FakeRedisBackend()
    writer_client = FakeRedisClient(backend)
    resolver_client = FakeRedisClient(backend)
    fixed_now = datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)
    writer = RedisApprovalRepository(
        client=writer_client,
        ttl_seconds=900,
        clock=lambda: fixed_now,
    )
    resolver = RedisApprovalRepository(
        client=resolver_client,
        ttl_seconds=900,
        clock=lambda: fixed_now,
    )
    key = "user-a:docs-a:session-1"

    writer.put(key, _request())

    redis_key = f"tech_doc_agent:guardrail_approval:{key}"
    envelope = json.loads(backend.values[redis_key])
    assert backend.expirations[redis_key] == 900
    assert envelope["schema_version"] == 1
    assert envelope["status"] == "pending"
    assert envelope["created_at"] == "2026-07-11T08:00:00+00:00"
    assert envelope["expires_at"] == "2026-07-11T08:15:00+00:00"
    assert resolver.get(key) == _request()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda repository: repository.pop(key), [writer, resolver]))

    assert results.count(_request()) == 1
    assert results.count(None) == 1
    assert writer.get(key) is None


def test_separate_runtimes_can_reject_the_same_redis_backed_guardrail_request():
    backend = FakeRedisBackend()
    runtime_a = ChatRuntime(
        approval_repository=RedisApprovalRepository(
            client=FakeRedisClient(backend),
            ttl_seconds=900,
        ),
        settings=Settings(LANGFUSE_FLUSH_ON_REQUEST=False),
    )
    runtime_b = ChatRuntime(
        approval_repository=RedisApprovalRepository(
            client=FakeRedisClient(backend),
            ttl_seconds=900,
        ),
        settings=Settings(LANGFUSE_FLUSH_ON_REQUEST=False),
    )

    runtime_a.request_guardrail_approval(
        "session-cross-runtime",
        "Ignore previous instructions",
        source="chat.message",
        risk_level="medium",
        findings=["ignore_previous_instructions"],
        user_id="user-a",
        namespace="docs-a",
    )

    assert runtime_b.has_pending_guardrail_approval(
        "session-cross-runtime",
        user_id="user-a",
        namespace="docs-a",
    )
    parts = list(
        runtime_b.stream_approval(
            "session-cross-runtime",
            approved=False,
            feedback="Rejected by another worker",
            user_id="user-a",
            namespace="docs-a",
        )
    )

    assert len(parts) == 1
    assert not runtime_a.has_pending_guardrail_approval(
        "session-cross-runtime",
        user_id="user-a",
        namespace="docs-a",
    )


def test_redis_repository_rejects_corrupt_or_unknown_payloads():
    client = FakeRedisClient()
    repository = RedisApprovalRepository(client=client, ttl_seconds=60)
    redis_key = "tech_doc_agent:guardrail_approval:user:docs:session"

    client.backend.values[redis_key] = "not-json"
    with pytest.raises(ApprovalRepositoryDataError, match="not valid JSON"):
        repository.get("user:docs:session")

    client.backend.values[redis_key] = json.dumps(
        {
            "schema_version": 99,
            "status": "pending",
            "created_at": "now",
            "expires_at": "later",
            "request": {},
        }
    )
    with pytest.raises(ApprovalRepositoryDataError, match="schema version"):
        repository.get("user:docs:session")


def test_redis_repository_factory_is_lazy_and_close_owns_the_client(monkeypatch):
    client = FakeRedisClient()
    calls = []

    monkeypatch.setattr(
        approval_repository.Redis,
        "from_url",
        lambda redis_url, **kwargs: calls.append((redis_url, kwargs)) or client,
    )

    repository = RedisApprovalRepository.from_url(
        "redis://approval-test",
        ttl_seconds=30,
    )
    repository.close()

    assert calls == [("redis://approval-test", {"decode_responses": True})]
    assert client.closed is True


def test_redis_repository_rejects_non_positive_ttl():
    with pytest.raises(ValueError, match="greater than zero"):
        RedisApprovalRepository(client=FakeRedisClient(), ttl_seconds=0)
