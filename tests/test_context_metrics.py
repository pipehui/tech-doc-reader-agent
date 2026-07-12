from langchain_core.messages import AIMessage, HumanMessage
import pytest

from tech_doc_agent.app.core.budget import LlmUsage
from tech_doc_agent.app.core.context_metrics import (
    ContextMetrics,
    ContextSnapshot,
)
from tech_doc_agent.app.core.context_serialization import (
    estimate_serialized_bytes,
    measure_context,
)
from tech_doc_agent.app.core.errors import ValidationError


def _usage(
    *,
    calls: int = 1,
    input_tokens: int | None = 10,
) -> LlmUsage:
    return LlmUsage(
        calls=calls,
        provider="provider",
        model="model",
        input_tokens=input_tokens,
        output_tokens=2 if input_tokens is not None else None,
        total_tokens=(input_tokens + 2) if input_tokens is not None else None,
    )


def test_serialized_byte_estimate_is_utf8_and_does_not_call_unknown_repr():
    class PrivateObject:
        def __str__(self):
            raise AssertionError("private string conversion must not run")

    assert estimate_serialized_bytes("你") == 5
    assert estimate_serialized_bytes({"private": PrivateObject()}) is not None


def test_serialized_byte_estimate_handles_cycles_without_recursion_failure():
    payload = []
    payload.append(payload)

    assert estimate_serialized_bytes(payload) is not None


def test_measure_context_separates_checkpoint_from_scoped_prompt():
    full_state = {
        "messages": [
            HumanMessage(content="question"),
            AIMessage(content="primary history", name="primary"),
            AIMessage(content="parser history", name="parser"),
        ],
        "workflow_plan": ["parser", "explanation"],
    }
    prompt_state = {
        **full_state,
        "messages": [HumanMessage(content="controlled parser task")],
    }

    snapshot = measure_context(
        state=full_state,
        prompt_state=prompt_state,
        agent="parser",
        scope="scoped",
    )

    assert snapshot.checkpoint_message_count == 3
    assert snapshot.prompt_message_count == 1
    assert snapshot.checkpoint_serialized_bytes is not None
    assert snapshot.prompt_serialized_bytes is not None
    assert snapshot.checkpoint_serialized_bytes > snapshot.prompt_serialized_bytes


def test_context_metrics_accumulate_known_and_unknown_provider_input_tokens():
    metrics = ContextMetrics.new()
    snapshot = ContextSnapshot(
        agent="primary",
        scope="full",
        checkpoint_message_count=4,
        checkpoint_serialized_bytes=400,
        prompt_message_count=4,
        prompt_serialized_bytes=350,
    )
    metrics = metrics.record(snapshot, (_usage(input_tokens=10),))
    metrics = metrics.record(
        ContextSnapshot(
            agent="primary",
            scope="full",
            checkpoint_message_count=6,
            checkpoint_serialized_bytes=600,
            prompt_message_count=6,
            prompt_serialized_bytes=550,
        ),
        (_usage(calls=2, input_tokens=None),),
    )

    primary = metrics.agents["primary"]
    assert metrics.measurements == 2
    assert primary.invocations == 2
    assert primary.llm_calls == 3
    assert primary.max_checkpoint_message_count == 6
    assert primary.max_checkpoint_serialized_bytes == 600
    assert primary.reported_input_tokens == 10
    assert primary.unreported_input_token_calls == 2
    assert primary.input_tokens is None
    assert primary.last_input_tokens is None
    assert ContextMetrics.from_state(metrics.to_state()) == metrics


def test_context_metrics_keep_agents_independent():
    metrics = ContextMetrics.new().record(
        ContextSnapshot("parser", "scoped", 10, 1000, 2, 200),
        (_usage(input_tokens=20),),
    ).record(
        ContextSnapshot("summary", "full", 12, 1200, 12, 1100),
        (_usage(input_tokens=100),),
    )

    assert metrics.agents["parser"].scope == "scoped"
    assert metrics.agents["parser"].last_prompt_message_count == 2
    assert metrics.agents["summary"].scope == "full"
    assert metrics.agents["summary"].last_prompt_message_count == 12


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"schema_version": True, "measurements": 0, "agents": {}},
        {"schema_version": 1, "measurements": -1, "agents": {}},
        {
            "schema_version": 1,
            "measurements": 1,
            "agents": {},
        },
    ],
)
def test_context_metrics_reject_corrupt_checkpoint_payload(payload):
    with pytest.raises(ValidationError) as exc_info:
        ContextMetrics.from_state(payload)

    assert exc_info.value.code == "context_metrics_invalid"
