from tech_doc_agent.app.core.retry_usage import RetryUsage, RetryUsageLedger
from tech_doc_agent.app.graph.provider_retries import (
    ProviderRetryUsageTracker,
    provider_retry_usage_request_start_node,
)


def _usage() -> RetryUsage:
    return RetryUsage(
        operation="embedding.create",
        dependency="embedding",
        tool="read_docs",
        idempotent=True,
        attempts=2,
        retries=1,
        waited_seconds=0.1,
        outcome="succeeded",
        reason="completed",
    )


def test_request_start_replaces_prior_provider_retry_ledger():
    prior = RetryUsageLedger().record((_usage(),))
    node = provider_retry_usage_request_start_node(
        lambda state, config=None: {"learning_target": "StateGraph"}
    )

    update = node(
        {"messages": [], "provider_retry_usage": prior.to_state()},
        None,
    )

    assert update["learning_target"] == "StateGraph"
    assert update["provider_retry_usage"] == RetryUsageLedger().to_state()
    assert update["provider_retry_usage_delta"]["kind"] == "reset"
    assert update["provider_retry_usage_delta"]["summary"]["operations"] == 0


def test_tracker_accumulates_prior_operations_and_returns_only_new_delta():
    events = []
    tracker = ProviderRetryUsageTracker(
        event_logger=lambda event, **fields: events.append({"event": event, **fields})
    )
    first = _usage()
    second = RetryUsage(
        operation="web_search.duckduckgo",
        dependency="duckduckgo",
        tool="web_search",
        idempotent=True,
        attempts=1,
        retries=0,
        waited_seconds=0,
        outcome="succeeded",
        reason="completed",
    )
    prior = RetryUsageLedger().record((first,))

    update = tracker.record_tool_operations(
        {"messages": [], "provider_retry_usage": prior.to_state()},
        {"messages": []},
        (second,),
    )

    ledger = RetryUsageLedger.from_state(update["provider_retry_usage"])
    assert ledger.operations == (first, second)
    assert update["provider_retry_usage_delta"]["kind"] == "operations"
    assert update["provider_retry_usage_delta"]["summary"]["operations"] == 1
    assert update["provider_retry_usage_delta"]["operations"] == [
        second.to_payload()
    ]
    assert events == [
        {
            "event": "provider_retry.usage.recorded",
            "operations_delta": 1,
            "attempts_delta": 1,
            "retries_delta": 0,
            "waited_seconds_delta": 0.0,
            "recovered_operations_delta": 0,
            "exhausted_operations_delta": 0,
            "operations": 2,
            "attempts": 3,
            "retries": 1,
        }
    ]
