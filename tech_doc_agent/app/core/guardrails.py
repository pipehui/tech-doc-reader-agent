from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from tech_doc_agent.app.core.observability import log_event


RiskLevel = Literal["none", "low", "medium", "high"]


@dataclass(frozen=True)
class GuardrailFinding:
    name: str
    severity: RiskLevel


@dataclass(frozen=True)
class InputRisk:
    level: RiskLevel
    findings: list[GuardrailFinding]


_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, RiskLevel, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        "medium",
        re.compile(
            r"(ignore|forget|disregard)\s+(all\s+)?(previous|prior|above)\s+"
            r"(instructions|rules|messages|prompts)",
            re.IGNORECASE,
        ),
    ),
    (
        "chinese_ignore_previous_instructions",
        "medium",
        re.compile(r"忽略(之前|以上|前面|所有).{0,12}(指令|规则|要求|提示词)"),
    ),
    (
        "system_prompt_exfiltration",
        "high",
        re.compile(
            r"(reveal|print|show|dump|leak).{0,40}"
            r"(system|developer).{0,20}(prompt|message|instruction)",
            re.IGNORECASE,
        ),
    ),
    (
        "chinese_prompt_exfiltration",
        "high",
        re.compile(r"(泄露|输出|打印|显示).{0,20}(系统|开发者).{0,12}(提示词|消息|指令)"),
    ),
    (
        "jailbreak_attempt",
        "high",
        re.compile(r"\b(jailbreak|DAN mode|developer mode)\b|越狱模式", re.IGNORECASE),
    ),
    (
        "secret_exfiltration",
        "high",
        re.compile(r"(leak|reveal|print|show|dump|泄露|输出|打印).{0,30}(secret|api key|密钥|令牌|token)", re.IGNORECASE),
    ),
)

_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_CHINA_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
_SECRET_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}\b")


def _max_risk_level(findings: list[GuardrailFinding]) -> RiskLevel:
    if any(finding.severity == "high" for finding in findings):
        return "high"
    if any(finding.severity == "medium" for finding in findings):
        return "medium"
    if any(finding.severity == "low" for finding in findings):
        return "low"
    return "none"


def detect_prompt_injection(text: str) -> InputRisk:
    findings = [
        GuardrailFinding(name=name, severity=severity)
        for name, severity, pattern in _PROMPT_INJECTION_PATTERNS
        if pattern.search(text)
    ]
    return InputRisk(level=_max_risk_level(findings), findings=findings)


def record_input_risk(text: str, *, source: str, input_length: int | None = None) -> InputRisk:
    risk = detect_prompt_injection(text)

    if risk.findings:
        log_event(
            "guardrail.input_risk",
            source=source,
            risk_level=risk.level,
            findings=[finding.name for finding in risk.findings],
            input_length=len(text) if input_length is None else input_length,
        )

    return risk


def redact_pii(text: str) -> str:
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = _CHINA_MOBILE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = _CREDIT_CARD_RE.sub("[REDACTED_CARD]", redacted)
    return _SECRET_RE.sub("[REDACTED_SECRET]", redacted)
