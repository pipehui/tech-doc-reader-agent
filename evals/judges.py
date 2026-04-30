from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JudgeScores:
    plan_match: float
    keyword: float
    behavior: float
    latency: float | None


def normalize_plan(plan: Any) -> list[str]:
    if plan is None:
        return []

    if isinstance(plan, list):
        return [str(item).strip() for item in plan if str(item).strip()]

    if isinstance(plan, str):
        value = plan.strip()
        if not value or value.lower() == "direct":
            return []
        if value.startswith("[") and value.endswith("]"):
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                return [item.strip() for item in value.strip("[]").split(",") if item.strip()]
            return normalize_plan(parsed)
        return [item.strip() for item in value.split(",") if item.strip()]

    return [str(plan).strip()]


def plan_match_score(predicted: Any, expected: Any) -> float:
    predicted_plan = normalize_plan(predicted)
    expected_plan = normalize_plan(expected)

    if not predicted_plan and not expected_plan:
        return 1.0
    if not predicted_plan or not expected_plan:
        return 0.0
    if predicted_plan == expected_plan:
        return 1.0

    lcs = _longest_common_subsequence_length(predicted_plan, expected_plan)
    order_score = lcs / max(len(predicted_plan), len(expected_plan))
    exact_position_matches = sum(
        1 for left, right in zip(predicted_plan, expected_plan, strict=False) if left == right
    )
    position_score = exact_position_matches / max(len(predicted_plan), len(expected_plan))
    return round((order_score * 0.7) + (position_score * 0.3), 4)


def text_keyword_score(
    answer: str,
    must_contain: Iterable[str] | None = None,
    must_not_contain: Iterable[str] | None = None,
) -> float:
    required = [item for item in (must_contain or []) if item]
    forbidden = [item for item in (must_not_contain or []) if item]
    total = len(required) + len(forbidden)
    if total == 0:
        return 1.0

    answer_lower = answer.lower()
    passed = 0

    for item in required:
        if item.lower() in answer_lower:
            passed += 1

    for item in forbidden:
        if item.lower() not in answer_lower:
            passed += 1

    return round(passed / total, 4)


def behavior_check_score(case: dict[str, Any], run: dict[str, Any]) -> float:
    checks = case.get("behavior_checks", [])
    if not checks:
        return 1.0
    if not isinstance(checks, list):
        return 0.0

    passed = 0
    for check in checks:
        if _behavior_check_passed(check, run):
            passed += 1

    return round(passed / len(checks), 4)


def latency_score(elapsed_s: float | None) -> float | None:
    if elapsed_s is None:
        return None
    if elapsed_s <= 5:
        return 1.0
    if elapsed_s <= 15:
        return 0.8
    if elapsed_s <= 30:
        return 0.6
    if elapsed_s <= 60:
        return 0.3
    return 0.0


def judge_case(case: dict[str, Any], run: dict[str, Any]) -> JudgeScores:
    expected_plans = [case.get("expected_plan")]
    expected_plans.extend(case.get("acceptable_plans", []))

    return JudgeScores(
        plan_match=max(plan_match_score(run.get("predicted_plan"), expected) for expected in expected_plans),
        keyword=text_keyword_score(
            run.get("answer", ""),
            case.get("must_contain", []),
            case.get("must_not_contain", []),
        ),
        behavior=behavior_check_score(case, run),
        latency=latency_score(run.get("e2e_s")),
    )


def _longest_common_subsequence_length(left: list[str], right: list[str]) -> int:
    rows = len(left) + 1
    cols = len(right) + 1
    dp = [[0] * cols for _ in range(rows)]

    for row in range(1, rows):
        for col in range(1, cols):
            if left[row - 1] == right[col - 1]:
                dp[row][col] = dp[row - 1][col - 1] + 1
            else:
                dp[row][col] = max(dp[row - 1][col], dp[row][col - 1])

    return dp[-1][-1]


def _behavior_check_passed(check: Any, run: dict[str, Any]) -> bool:
    if not isinstance(check, dict):
        return False

    check_type = str(check.get("type") or "").strip()
    answer = str(run.get("answer") or "")
    answer_lower = answer.casefold()

    if check_type == "contains_any":
        return any(str(phrase).casefold() in answer_lower for phrase in check.get("phrases", []))

    if check_type == "contains_all":
        phrases = [str(phrase).casefold() for phrase in check.get("phrases", [])]
        return bool(phrases) and all(phrase in answer_lower for phrase in phrases)

    if check_type == "not_contains_any":
        return all(str(phrase).casefold() not in answer_lower for phrase in check.get("phrases", []))

    if check_type == "tool_results_max":
        return int(run.get("tool_results", 0) or 0) <= int(check.get("value", 0))

    if check_type == "tool_calls_max":
        return int(run.get("tool_calls", 0) or 0) <= int(check.get("value", 0))

    if check_type == "interrupt_count_max":
        return int(run.get("interrupt_count", 0) or 0) <= int(check.get("value", 0))

    if check_type == "status_in":
        return str(run.get("status") or "") in {str(value) for value in check.get("values", [])}

    if check_type == "plan_is_direct":
        return normalize_plan(run.get("predicted_plan")) == []

    return False
