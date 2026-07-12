import json

import pytest

from tech_doc_agent.app.core.errors import (
    ApplicationError,
    Conflict,
    DependencyUnavailable,
    PermissionDenied,
    RateLimited,
    Timeout,
    UnknownDependencyError,
    ValidationError,
    classify_error,
    safe_error_fields,
)


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    ("error", "expected_type", "retryable"),
    [
        (ValueError("invalid secret input"), ValidationError, False),
        (PermissionError("C:/private/token.txt"), PermissionDenied, False),
        (ProviderError("quota for api-key-secret", 429), RateLimited, True),
        (TimeoutError("https://private-provider timed out"), Timeout, True),
        (ConnectionError("redis://user:password@host"), DependencyUnavailable, True),
        (FileExistsError("private path exists"), Conflict, False),
        (RuntimeError("internal-secret-value"), UnknownDependencyError, False),
    ],
)
def test_classify_error_maps_minimal_error_taxonomy_without_exposing_raw_text(
    error,
    expected_type,
    retryable,
):
    mapped = classify_error(error, dependency="test_dependency", tool="test_tool")

    assert isinstance(mapped, expected_type)
    assert mapped.retryable is retryable
    assert mapped.dependency == "test_dependency"
    assert mapped.tool == "test_tool"
    assert mapped.cause_type == type(error).__name__
    assert str(error) not in mapped.to_json()


def test_classify_error_preserves_existing_safe_error_and_adds_missing_context():
    error = RateLimited(
        "Search capacity is temporarily exhausted.",
        dependency="tavily",
        cause_type="RateLimitError",
    )

    mapped = classify_error(error, dependency="web_search", tool="web_search")

    assert mapped is not error
    assert mapped.to_payload() == {
        "status": "error",
        "code": "rate_limited",
        "retryable": True,
        "safe_message": "Search capacity is temporarily exhausted.",
        "dependency": "tavily",
        "tool": "web_search",
        "cause_type": "RateLimitError",
    }


def test_safe_error_fields_are_structured_and_json_serializable():
    fields = safe_error_fields(
        TimeoutError("Bearer top-secret-token"),
        dependency="llm",
    )

    assert fields == {
        "error_code": "dependency_timeout",
        "retryable": True,
        "safe_message": "A dependency timed out. Try again.",
        "dependency": "llm",
        "tool": None,
        "cause_type": "TimeoutError",
    }
    assert "top-secret-token" not in json.dumps(fields)


def test_all_public_errors_share_the_stable_payload_shape():
    error_types: list[type[ApplicationError]] = [
        ValidationError,
        PermissionDenied,
        RateLimited,
        Timeout,
        DependencyUnavailable,
        Conflict,
        UnknownDependencyError,
    ]

    for error_type in error_types:
        assert set(error_type().to_payload()) == {
            "status",
            "code",
            "retryable",
            "safe_message",
            "dependency",
            "tool",
            "cause_type",
        }
