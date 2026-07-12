from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.tenant import TenantContext


class ApprovalRequestPayloadError(ValueError):
    """Raised when a stored approval request does not match the domain contract."""


@dataclass(frozen=True, slots=True)
class GuardrailApprovalRequest:
    session_id: str
    user_input: str
    user_id: str
    namespace: str
    source: str
    risk_level: str
    findings: tuple[str, ...]

    def __post_init__(self) -> None:
        required_text = (
            self.session_id,
            self.user_input,
            self.user_id,
            self.namespace,
            self.source,
            self.risk_level,
        )
        if any(not isinstance(value, str) for value in required_text):
            raise ApprovalRequestPayloadError(
                "Approval request fields must be strings."
            )
        if not isinstance(self.findings, tuple) or not all(
            isinstance(item, str) for item in self.findings
        ):
            raise ApprovalRequestPayloadError(
                "Approval findings must be a tuple of strings."
            )
        try:
            TenantContext(self.user_id, self.namespace)
        except ValidationError as exc:
            raise ApprovalRequestPayloadError(
                "Approval request tenant is invalid."
            ) from exc

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        user_input: str,
        tenant: TenantContext,
        source: str,
        risk_level: str,
        findings: Sequence[str],
    ) -> GuardrailApprovalRequest:
        return cls(
            session_id=session_id,
            user_input=user_input,
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            source=source,
            risk_level=risk_level,
            findings=tuple(findings),
        )

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> GuardrailApprovalRequest:
        findings = value.get("findings")
        if not isinstance(findings, list) or not all(
            isinstance(item, str) for item in findings
        ):
            raise ApprovalRequestPayloadError(
                "Approval findings must be a list of strings."
            )

        required_text_fields = (
            "session_id",
            "user_input",
            "user_id",
            "namespace",
            "source",
            "risk_level",
        )
        if any(not isinstance(value.get(field), str) for field in required_text_fields):
            raise ApprovalRequestPayloadError(
                "Approval request fields must be strings."
            )

        return cls(
            session_id=value["session_id"],
            user_input=value["user_input"],
            user_id=value["user_id"],
            namespace=value["namespace"],
            source=value["source"],
            risk_level=value["risk_level"],
            findings=tuple(findings),
        )

    @property
    def tenant(self) -> TenantContext:
        return TenantContext(self.user_id, self.namespace)

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_input": self.user_input,
            "user_id": self.user_id,
            "namespace": self.namespace,
            "source": self.source,
            "risk_level": self.risk_level,
            "findings": list(self.findings),
        }


class ApprovalRepository(Protocol):
    def put(self, key: str, request: GuardrailApprovalRequest) -> None: ...

    def get(self, key: str) -> GuardrailApprovalRequest | None: ...

    def pop(self, key: str) -> GuardrailApprovalRequest | None: ...

    def close(self) -> None: ...


__all__ = [
    "ApprovalRepository",
    "ApprovalRequestPayloadError",
    "GuardrailApprovalRequest",
]
