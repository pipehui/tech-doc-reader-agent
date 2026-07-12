from tech_doc_agent.app.core.guardrails import InputRisk, record_input_risk
from tech_doc_agent.app.core.observability import log_event


def evaluate_input_guardrail(text: str, *, source: str) -> InputRisk:
    """Evaluate an input once and record its application-level disposition."""
    risk = record_input_risk(text, source=source)

    if risk.level == "medium":
        log_event(
            "guardrail.input_warning",
            source=source,
            risk_level=risk.level,
            findings=[finding.name for finding in risk.findings],
        )
    elif risk.level == "high":
        log_event(
            "guardrail.input_blocked",
            source=source,
            risk_level=risk.level,
            findings=[finding.name for finding in risk.findings],
        )

    return risk
