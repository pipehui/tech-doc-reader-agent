import re

import pytest

from tech_doc_agent.app.core.redaction import (
    REDACTED_API_KEY,
    REDACTED_AUTHORIZATION,
    REDACTED_CREDENTIAL,
    REDACTED_EMAIL,
    REDACTED_JWT,
    REDACTED_PHONE,
    RedactionPolicy,
    pseudonymize,
    redact_text,
)


@pytest.mark.parametrize(
    ("raw", "marker"),
    [
        ("Authorization: Bearer bearer-token-value", REDACTED_AUTHORIZATION),
        ("Basic dXNlcjpwYXNzd29yZA==", REDACTED_AUTHORIZATION),
        ("openai=sk-proj-abcdefghijklmnop", REDACTED_API_KEY),
        ("tavily tvly-abcdefghijklmnop", REDACTED_API_KEY),
        ("github ghp_abcdefghijklmnopqrstuvwxyz123456", REDACTED_API_KEY),
        ("api_key='plain-secret-value'", REDACTED_CREDENTIAL),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEyMyJ9.abcdefghijklmnop",
            REDACTED_JWT,
        ),
        ("contact person@example.com", REDACTED_EMAIL),
        ("mobile 13800138000", REDACTED_PHONE),
        ("international +1 415-555-2671", REDACTED_PHONE),
        ("redis://admin:private-password@internal-host", REDACTED_CREDENTIAL),
    ],
)
def test_redact_text_positive_fixtures(raw, marker):
    redacted = redact_text(raw)

    assert marker in redacted
    for secret in re.findall(r"(?:private-password|plain-secret-value|bearer-token-value)", raw):
        assert secret not in redacted


@pytest.mark.parametrize(
    "value",
    [
        "550e8400-e29b-41d4-a716-446655440000",
        "sklearn.model_selection",
        "token_count=128 output_tokens=64",
        "StateGraph version 1.2.3",
        "assistant.empty_response.exhausted",
        "Basic concepts and bearer capacity are ordinary prose",
        "2026-07-12T10:30:00+08:00",
        "issue 1234567",
        "user at localhost",
    ],
)
def test_redact_text_negative_fixtures_keep_non_sensitive_values(value):
    assert redact_text(value) == value


def test_redaction_policy_recurses_by_value_and_sensitive_field_name():
    payload = {
        "authorization": "Bearer should-not-survive",
        "nested": [
            {"OPENAI_API_KEY": "short-but-sensitive"},
            "mail user@example.com or call 13800138000",
        ],
        "token_count": 17,
        "trace_id": "550e8400-e29b-41d4-a716-446655440000",
    }

    redacted = RedactionPolicy().redact(payload)

    assert redacted == {
        "authorization": REDACTED_AUTHORIZATION,
        "nested": [
            {"OPENAI_API_KEY": REDACTED_CREDENTIAL},
            f"mail {REDACTED_EMAIL} or call {REDACTED_PHONE}",
        ],
        "token_count": 17,
        "trace_id": "550e8400-e29b-41d4-a716-446655440000",
    }


def test_pseudonymization_is_keyed_stable_and_not_enabled_without_a_key():
    first = pseudonymize("person@example.com", key="controlled-key-with-32-random-bytes")
    second = pseudonymize("person@example.com", key="controlled-key-with-32-random-bytes")
    different_key = pseudonymize("person@example.com", key="other-controlled-key-with-32-bytes")

    assert first == second
    assert first != different_key
    assert "person@example.com" not in first
    assert first.startswith("pseudonym:")
    with pytest.raises(ValueError, match="at least 16 bytes"):
        pseudonymize("person@example.com", key="")


def test_policy_only_pseudonymizes_user_id_when_controlled_key_is_configured():
    raw = {
        "user_id": "stable-user-42",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
    }

    without_key = RedactionPolicy().redact(raw)
    with_key = RedactionPolicy(pseudonymization_key="controlled-key-with-32-random-bytes").redact(raw)

    assert without_key == raw
    assert with_key["user_id"].startswith("pseudonym:")
    assert with_key["session_id"] == raw["session_id"]


def test_policy_handles_recursive_containers_without_recursing_forever():
    payload = {}
    payload["self"] = payload

    assert RedactionPolicy().redact(payload) == {"self": "[REDACTED:CYCLE]"}
