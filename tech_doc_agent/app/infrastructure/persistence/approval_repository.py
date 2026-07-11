from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from redis import Redis

from tech_doc_agent.app.runtime.approvals import GuardrailApprovalRequest


APPROVAL_SCHEMA_VERSION = 1
DEFAULT_KEY_PREFIX = "tech_doc_agent:guardrail_approval"


class ApprovalRepositoryDataError(ValueError):
    """Raised when a stored approval payload does not match the supported schema."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_lifecycle_timestamp(envelope: dict[str, Any], field: str) -> datetime:
    value = envelope.get(field)
    if not isinstance(value, str):
        raise ApprovalRepositoryDataError("Approval payload is missing lifecycle timestamps.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ApprovalRepositoryDataError("Approval lifecycle timestamp is invalid.") from exc
    if parsed.tzinfo is None:
        raise ApprovalRepositoryDataError("Approval lifecycle timestamp must include a timezone.")
    return parsed


@dataclass(slots=True)
class RedisApprovalRepository:
    client: Redis
    ttl_seconds: int
    key_prefix: str = DEFAULT_KEY_PREFIX
    clock: Callable[[], datetime] = _utc_now

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("Approval TTL must be greater than zero.")

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        *,
        ttl_seconds: int,
        key_prefix: str = DEFAULT_KEY_PREFIX,
    ) -> RedisApprovalRepository:
        client = Redis.from_url(redis_url, decode_responses=True)
        return cls(
            client=client,
            ttl_seconds=ttl_seconds,
            key_prefix=key_prefix,
        )

    def _key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"

    def _serialize(self, request: GuardrailApprovalRequest) -> str:
        created_at = self.clock()
        if created_at.tzinfo is None:
            raise ValueError("Approval repository clock must return a timezone-aware datetime.")
        expires_at = created_at + timedelta(seconds=self.ttl_seconds)
        envelope = {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "status": "pending",
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "request": asdict(request),
        }
        return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))

    def _deserialize(self, raw_value: Any) -> GuardrailApprovalRequest:
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")
        if not isinstance(raw_value, str):
            raise ApprovalRepositoryDataError("Approval payload must be JSON text.")

        try:
            envelope = json.loads(raw_value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ApprovalRepositoryDataError("Approval payload is not valid JSON.") from exc

        if not isinstance(envelope, dict):
            raise ApprovalRepositoryDataError("Approval payload must be a JSON object.")
        if envelope.get("schema_version") != APPROVAL_SCHEMA_VERSION:
            raise ApprovalRepositoryDataError("Unsupported approval schema version.")
        if envelope.get("status") != "pending":
            raise ApprovalRepositoryDataError("Approval payload is not pending.")
        created_at = _parse_lifecycle_timestamp(envelope, "created_at")
        expires_at = _parse_lifecycle_timestamp(envelope, "expires_at")
        if expires_at <= created_at:
            raise ApprovalRepositoryDataError("Approval expiry must be after creation time.")

        request = envelope.get("request")
        if not isinstance(request, dict):
            raise ApprovalRepositoryDataError("Approval payload is missing its request object.")

        findings = request.get("findings")
        if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
            raise ApprovalRepositoryDataError("Approval findings must be a list of strings.")

        required_text_fields = (
            "session_id",
            "user_input",
            "user_id",
            "namespace",
            "source",
            "risk_level",
        )
        if any(not isinstance(request.get(field), str) for field in required_text_fields):
            raise ApprovalRepositoryDataError("Approval request fields must be strings.")

        return GuardrailApprovalRequest(
            session_id=request["session_id"],
            user_input=request["user_input"],
            user_id=request["user_id"],
            namespace=request["namespace"],
            source=request["source"],
            risk_level=request["risk_level"],
            findings=tuple(findings),
        )

    def put(self, key: str, request: GuardrailApprovalRequest) -> None:
        self.client.set(
            self._key(key),
            self._serialize(request),
            ex=self.ttl_seconds,
        )

    def get(self, key: str) -> GuardrailApprovalRequest | None:
        raw_value = self.client.get(self._key(key))
        return None if raw_value is None else self._deserialize(raw_value)

    def pop(self, key: str) -> GuardrailApprovalRequest | None:
        raw_value = self.client.getdel(self._key(key))
        return None if raw_value is None else self._deserialize(raw_value)

    def close(self) -> None:
        self.client.close()
