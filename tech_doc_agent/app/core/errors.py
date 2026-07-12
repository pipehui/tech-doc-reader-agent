from __future__ import annotations

from http import HTTPStatus
import json
from typing import Any, Literal, TypedDict


class ErrorPayload(TypedDict):
    status: Literal["error"]
    code: str
    retryable: bool
    safe_message: str
    dependency: str | None
    tool: str | None
    cause_type: str


class ApplicationError(Exception):
    """Safe, structured error that may cross an application boundary."""

    default_code = "application_error"
    default_retryable = False
    default_safe_message = "The operation could not be completed."

    def __init__(
        self,
        safe_message: str | None = None,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        dependency: str | None = None,
        tool: str | None = None,
        cause: BaseException | None = None,
        cause_type: str | None = None,
    ) -> None:
        self.code = code or self.default_code
        self.retryable = self.default_retryable if retryable is None else retryable
        self.safe_message = safe_message or self.default_safe_message
        self.dependency = dependency
        self.tool = tool
        self.cause_type = cause_type or (type(cause).__name__ if cause is not None else type(self).__name__)
        super().__init__(self.safe_message)

    def with_context(
        self,
        *,
        dependency: str | None = None,
        tool: str | None = None,
    ) -> ApplicationError:
        return type(self)(
            self.safe_message,
            code=self.code,
            retryable=self.retryable,
            dependency=self.dependency or dependency,
            tool=self.tool or tool,
            cause_type=self.cause_type,
        )

    def to_payload(self) -> ErrorPayload:
        return {
            "status": "error",
            "code": self.code,
            "retryable": self.retryable,
            "safe_message": self.safe_message,
            "dependency": self.dependency,
            "tool": self.tool,
            "cause_type": self.cause_type,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, sort_keys=True)


class ValidationError(ApplicationError):
    default_code = "validation_error"
    default_safe_message = "The request or dependency response was invalid."


class PermissionDenied(ApplicationError):
    default_code = "permission_denied"
    default_safe_message = "The operation is not permitted."


class RateLimited(ApplicationError):
    default_code = "rate_limited"
    default_retryable = True
    default_safe_message = "A dependency rate limit was reached. Try again later."


class Timeout(ApplicationError):
    default_code = "dependency_timeout"
    default_retryable = True
    default_safe_message = "A dependency timed out. Try again."


class DependencyUnavailable(ApplicationError):
    default_code = "dependency_unavailable"
    default_retryable = True
    default_safe_message = "A required dependency is temporarily unavailable."


class Conflict(ApplicationError):
    default_code = "conflict"
    default_safe_message = "The operation conflicts with the current state."


class UnknownDependencyError(ApplicationError):
    default_code = "unknown_dependency_error"
    default_safe_message = "A required dependency failed."


def classify_error(
    error: BaseException,
    *,
    dependency: str | None = None,
    tool: str | None = None,
) -> ApplicationError:
    """Map provider and infrastructure exceptions without exposing their text."""

    if isinstance(error, ApplicationError):
        return error.with_context(dependency=dependency, tool=tool)

    dependency = dependency or _infer_dependency(error)
    status_code = _status_code(error)
    error_name = type(error).__name__.casefold()

    error_type: type[ApplicationError]
    if isinstance(error, PermissionError) or status_code in {
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    } or any(marker in error_name for marker in ("authentication", "permission", "forbidden")):
        error_type = PermissionDenied
    elif status_code == HTTPStatus.TOO_MANY_REQUESTS or any(
        marker in error_name for marker in ("ratelimit", "rate_limit", "throttl")
    ):
        error_type = RateLimited
    elif isinstance(error, TimeoutError) or status_code in {
        HTTPStatus.REQUEST_TIMEOUT,
        HTTPStatus.GATEWAY_TIMEOUT,
    } or "timeout" in error_name:
        error_type = Timeout
    elif isinstance(error, FileExistsError) or status_code == HTTPStatus.CONFLICT or "conflict" in error_name:
        error_type = Conflict
    elif _is_validation_error(error, status_code, error_name):
        error_type = ValidationError
    elif isinstance(error, (ConnectionError, BrokenPipeError)) or (
        status_code is not None and status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
    ) or any(
        marker in error_name
        for marker in ("connection", "serviceunavailable", "unavailable", "busyloading")
    ):
        error_type = DependencyUnavailable
    elif isinstance(error, OSError):
        error_type = DependencyUnavailable
    else:
        error_type = UnknownDependencyError

    return error_type(
        dependency=dependency,
        tool=tool,
        cause=error,
    )


def safe_error_fields(
    error: BaseException,
    *,
    dependency: str | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    mapped = classify_error(error, dependency=dependency, tool=tool)
    return {
        "error_code": mapped.code,
        "retryable": mapped.retryable,
        "safe_message": mapped.safe_message,
        "dependency": mapped.dependency,
        "tool": mapped.tool,
        "cause_type": mapped.cause_type,
    }


def _infer_dependency(error: BaseException) -> str | None:
    module = type(error).__module__.casefold()
    name = type(error).__name__.casefold()
    qualified = f"{module}.{name}"

    if "redis" in qualified:
        return "redis"
    if "tavily" in qualified:
        return "tavily"
    if "duckduckgo" in qualified or "ddgs" in qualified:
        return "duckduckgo"
    if "faiss" in qualified:
        return "vector_index"
    if "openai" in qualified or "anthropic" in qualified:
        return "llm"
    if module.startswith("json") or isinstance(error, (FileNotFoundError, PermissionError)):
        return "file_repository"
    return None


def _status_code(error: BaseException) -> int | None:
    for owner in (error, getattr(error, "response", None)):
        value = getattr(owner, "status_code", None)
        if isinstance(value, int):
            return value
        if isinstance(value, HTTPStatus):
            return int(value)
    return None


def _is_validation_error(
    error: BaseException,
    status_code: int | None,
    error_name: str,
) -> bool:
    if status_code in {
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    }:
        return True
    return isinstance(error, (TypeError, ValueError)) or "validation" in error_name


__all__ = [
    "ApplicationError",
    "Conflict",
    "DependencyUnavailable",
    "ErrorPayload",
    "PermissionDenied",
    "RateLimited",
    "Timeout",
    "UnknownDependencyError",
    "ValidationError",
    "classify_error",
    "safe_error_fields",
]
