from langchain_core.messages import AIMessage

from tech_doc_agent.app.application.approval_models import GuardrailApprovalRequest


def guardrail_rejection_part(
    pending: GuardrailApprovalRequest,
    feedback: str,
) -> tuple[str, dict]:
    reason = feedback or "未提供原因"
    return (
        "updates",
        {
            "guardrail": {
                "messages": [
                    AIMessage(
                        content=(
                            "这条输入被 guardrails 标记为 medium risk，审批未通过，"
                            f"已停止执行。原因：{reason}"
                        ),
                        name="guardrail",
                    )
                ]
            }
        },
    )


__all__ = ["guardrail_rejection_part"]
