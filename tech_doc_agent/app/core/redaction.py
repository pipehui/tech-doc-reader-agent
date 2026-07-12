from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import hmac
import re
from typing import Any


REDACTED_API_KEY = "[REDACTED:API_KEY]"
REDACTED_AUTHORIZATION = "[REDACTED:AUTHORIZATION]"
REDACTED_CREDENTIAL = "[REDACTED:CREDENTIAL]"
REDACTED_EMAIL = "[REDACTED:EMAIL]"
REDACTED_JWT = "[REDACTED:JWT]"
REDACTED_PHONE = "[REDACTED:PHONE]"
REDACTED_BINARY = "[REDACTED:BINARY]"
REDACTED_CYCLE = "[REDACTED:CYCLE]"

_AUTHORIZATION_HEADER_RE = re.compile(
    r"(?P<label>\bAuthorization\s*[:=]\s*)(?P<scheme>Basic|Bearer)\s+[^\s,;]+",
    flags=re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_BASIC_RE = re.compile(r"\bBasic\s+[A-Za-z0-9+/]{12,}={0,2}")
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)
_API_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_-]{8,}"
    r"|tvly-[A-Za-z0-9_-]{8,}"
    r"|AIza[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r")(?![A-Za-z0-9])"
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?P<label>\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\b"
    r"\s*[:=]\s*)"
    r"[\"']?[^\s,;\"']{6,}[\"']?",
    flags=re.IGNORECASE,
)
_URI_PASSWORD_RE = re.compile(r"(?P<prefix>://[^:/@\s]+:)[^@\s/]+(?P<suffix>@)")
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9._%+-])"
)
_CHINA_MOBILE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_INTERNATIONAL_PHONE_RE = re.compile(r"(?<!\w)\+\d{1,3}(?:[ .-]?\d){7,12}(?!\d)")

_AUTHORIZATION_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
    }
)
_CREDENTIAL_KEYS = frozenset(
    {
        "api_key",
        "x_api_key",
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "passwd",
        "secret",
        "secret_key",
        "client_secret",
    }
)
_DEFAULT_PSEUDONYMIZED_FIELDS = frozenset({"user_id"})


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """One recursive policy shared by logs, traces and persisted eval artifacts."""

    pseudonymization_key: str = ""
    pseudonymized_fields: frozenset[str] = _DEFAULT_PSEUDONYMIZED_FIELDS

    def redact(self, value: Any) -> Any:
        return _redact_value(value, policy=self, field_name=None, active_containers=set())

    def redact_text(self, value: str) -> str:
        return redact_text(value)


@lru_cache(maxsize=8)
def telemetry_redaction_policy(pseudonymization_key: str = "") -> RedactionPolicy:
    return RedactionPolicy(pseudonymization_key=pseudonymization_key)


def pseudonymize(value: str, *, key: str, scope: str = "telemetry-user") -> str:
    """Create a stable keyed pseudonym; this is not claimed to be anonymization."""

    if len(key.encode("utf-8")) < 16:
        raise ValueError("Pseudonymization requires a controlled key of at least 16 bytes.")
    message = f"{scope}\0{value}".encode("utf-8")
    digest = hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()[:24]
    return f"pseudonym:{digest}"


def redact_text(value: str) -> str:
    value = _AUTHORIZATION_HEADER_RE.sub(
        lambda match: (
            f"{match.group('label')}{match.group('scheme')} {REDACTED_AUTHORIZATION}"
        ),
        value,
    )
    value = _BEARER_RE.sub(f"Bearer {REDACTED_AUTHORIZATION}", value)
    value = _BASIC_RE.sub(f"Basic {REDACTED_AUTHORIZATION}", value)
    value = _URI_PASSWORD_RE.sub(
        lambda match: f"{match.group('prefix')}{REDACTED_CREDENTIAL}{match.group('suffix')}",
        value,
    )
    value = _JWT_RE.sub(REDACTED_JWT, value)
    value = _API_KEY_RE.sub(REDACTED_API_KEY, value)
    value = _CREDENTIAL_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('label')}{REDACTED_CREDENTIAL}",
        value,
    )
    value = _EMAIL_RE.sub(REDACTED_EMAIL, value)
    value = _CHINA_MOBILE_RE.sub(REDACTED_PHONE, value)
    return _INTERNATIONAL_PHONE_RE.sub(REDACTED_PHONE, value)


def _redact_value(
    value: Any,
    *,
    policy: RedactionPolicy,
    field_name: str | None,
    active_containers: set[int],
) -> Any:
    normalized_field = _normalize_field_name(field_name)
    sensitive_replacement = _sensitive_field_replacement(normalized_field)
    if sensitive_replacement is not None:
        return sensitive_replacement

    if (
        normalized_field in policy.pseudonymized_fields
        and policy.pseudonymization_key
        and value is not None
    ):
        return pseudonymize(
            str(value),
            key=policy.pseudonymization_key,
            scope=normalized_field,
        )

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return REDACTED_BINARY
    if isinstance(value, Mapping):
        return _redact_mapping(value, policy=policy, active_containers=active_containers)
    if isinstance(value, list):
        return _redact_sequence(value, policy=policy, active_containers=active_containers)
    if isinstance(value, tuple):
        return tuple(_redact_sequence(value, policy=policy, active_containers=active_containers))
    if isinstance(value, set):
        return set(_redact_sequence(value, policy=policy, active_containers=active_containers))
    if isinstance(value, frozenset):
        return frozenset(_redact_sequence(value, policy=policy, active_containers=active_containers))
    return value


def _redact_mapping(
    value: Mapping[Any, Any],
    *,
    policy: RedactionPolicy,
    active_containers: set[int],
) -> dict[Any, Any] | str:
    identity = id(value)
    if identity in active_containers:
        return REDACTED_CYCLE
    active_containers.add(identity)
    try:
        return {
            key: _redact_value(
                item,
                policy=policy,
                field_name=key if isinstance(key, str) else None,
                active_containers=active_containers,
            )
            for key, item in value.items()
        }
    finally:
        active_containers.remove(identity)


def _redact_sequence(
    value,
    *,
    policy: RedactionPolicy,
    active_containers: set[int],
) -> list[Any] | list[str]:
    identity = id(value)
    if identity in active_containers:
        return [REDACTED_CYCLE]
    active_containers.add(identity)
    try:
        return [
            _redact_value(
                item,
                policy=policy,
                field_name=None,
                active_containers=active_containers,
            )
            for item in value
        ]
    finally:
        active_containers.remove(identity)


def _normalize_field_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _sensitive_field_replacement(field_name: str) -> str | None:
    if field_name in _AUTHORIZATION_KEYS:
        return REDACTED_AUTHORIZATION
    if field_name in _CREDENTIAL_KEYS:
        return REDACTED_CREDENTIAL
    if field_name.endswith("_api_key") or field_name.endswith("_password"):
        return REDACTED_CREDENTIAL
    if field_name.endswith("_token") and field_name not in {"input_token", "output_token"}:
        return REDACTED_CREDENTIAL
    return None


__all__ = [
    "RedactionPolicy",
    "pseudonymize",
    "redact_text",
    "telemetry_redaction_policy",
]
