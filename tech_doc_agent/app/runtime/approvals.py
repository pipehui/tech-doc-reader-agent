from tech_doc_agent.app.application.approval_models import (
    ApprovalRepository,
    GuardrailApprovalRequest,
)
from tech_doc_agent.app.application.approval_service import (
    ApprovalService as ApplicationApprovalService,
)
from tech_doc_agent.app.runtime.approval_projection import guardrail_rejection_part


class ApprovalService(ApplicationApprovalService):
    """Compatibility wrapper for the former runtime service import path."""

    def rejection_part(
        self,
        pending: GuardrailApprovalRequest,
        feedback: str,
    ) -> tuple[str, dict]:
        return guardrail_rejection_part(pending, feedback)


__all__ = [
    "ApprovalRepository",
    "ApprovalService",
    "GuardrailApprovalRequest",
]
