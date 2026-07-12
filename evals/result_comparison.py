from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeGuard

from evals.manifest_compatibility import (
    ManifestCompatibilityResult,
    compare_eval_run_manifests,
)
from evals.thresholds import MetricThreshold, ThresholdPolicy


RegressionStatus = Literal["passed", "failed", "not_comparable", "invalid"]
_FLOAT_TOLERANCE = 1e-12


@dataclass(frozen=True, slots=True)
class RegressionIssue:
    code: str
    message: str

    def to_payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class MetricRegressionCheck:
    metric: str
    threshold: MetricThreshold
    baseline: float
    candidate: float | None
    absolute_passed: bool
    regression_amount: float | None
    regression_passed: bool

    @property
    def passed(self) -> bool:
        return self.absolute_passed and self.regression_passed

    def to_payload(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "direction": self.threshold.direction,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "delta": (
                None if self.candidate is None else self.candidate - self.baseline
            ),
            "absolute_limit": self.threshold.absolute_limit,
            "absolute_passed": self.absolute_passed,
            "max_regression": self.threshold.max_regression,
            "regression_amount": self.regression_amount,
            "regression_passed": self.regression_passed,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class ResultRegressionComparison:
    status: RegressionStatus
    manifest_compatibility: ManifestCompatibilityResult
    policy: ThresholdPolicy
    checks: tuple[MetricRegressionCheck, ...] = ()
    issues: tuple[RegressionIssue, ...] = ()
    baseline_summary: dict[str, Any] | None = None
    candidate_summary: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "manifest_compatibility": self.manifest_compatibility.to_payload(),
            "policy": {
                "policy_id": self.policy.policy_id,
                "fingerprint": self.policy.fingerprint,
            },
            "issues": [issue.to_payload() for issue in self.issues],
            "checks": [check.to_payload() for check in self.checks],
            "baseline_summary": self.baseline_summary,
            "candidate_summary": self.candidate_summary,
        }


def compare_eval_results(
    baseline_manifest: Any,
    candidate_manifest: Any,
    baseline_rows: Any,
    candidate_rows: Any,
    policy: ThresholdPolicy,
    *,
    allow_dirty: bool = False,
) -> ResultRegressionComparison:
    compatibility = compare_eval_run_manifests(
        baseline_manifest,
        candidate_manifest,
        allow_dirty=allow_dirty,
    )
    if compatibility.status != "compatible":
        status: RegressionStatus = (
            "invalid" if compatibility.status == "invalid" else "not_comparable"
        )
        return ResultRegressionComparison(
            status=status,
            manifest_compatibility=compatibility,
            policy=policy,
        )

    try:
        runner = str(baseline_manifest["runner"])
        if policy.runner != runner:
            raise ValueError(
                "Threshold policy runner does not match the compatible manifests"
            )
        validated_baseline = _validated_rows(baseline_rows, label="baseline")
        validated_candidate = _validated_rows(candidate_rows, label="candidate")
        baseline_summary = _summarize_rows(runner, validated_baseline)
        candidate_summary = _summarize_rows(runner, validated_candidate)
        checks = _build_checks(
            baseline_summary,
            candidate_summary,
            policy,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return ResultRegressionComparison(
            status="invalid",
            manifest_compatibility=compatibility,
            policy=policy,
            issues=(
                RegressionIssue(
                    code="result_comparison_invalid",
                    message=str(exc),
                ),
            ),
        )

    baseline_ids = {row["id"] for row in validated_baseline}
    candidate_ids = {row["id"] for row in validated_candidate}
    issues: list[RegressionIssue] = []
    if baseline_ids != candidate_ids:
        issues.append(
            RegressionIssue(
                code="result_case_ids_mismatch",
                message="Baseline and candidate result case IDs differ.",
            )
        )

    failed = bool(issues) or any(not check.passed for check in checks)
    return ResultRegressionComparison(
        status="failed" if failed else "passed",
        manifest_compatibility=compatibility,
        policy=policy,
        checks=checks,
        issues=tuple(issues),
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
    )


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL line {line_number} must contain an object")
        rows.append(value)
    return rows


def _validated_rows(value: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label.title()} results must be a non-empty list")
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for row in value:
        if not isinstance(row, dict):
            raise ValueError(f"{label.title()} result rows must be objects")
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id or case_id != case_id.strip():
            raise ValueError(f"{label.title()} result row IDs must be non-empty strings")
        if case_id in ids:
            raise ValueError(f"{label.title()} result row IDs must be unique")
        ids.add(case_id)
        rows.append(dict(row))
    return rows


def _summarize_rows(
    runner: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if runner == "online_agent_eval":
        from evals.run_eval import summarize_results

        return summarize_results(rows)
    if runner == "offline_retrieval_eval":
        from evals.run_retrieval_eval import summarize_results

        return summarize_results(rows)
    if runner == "offline_context_compaction_eval":
        from evals.run_context_compaction_eval import summarize_results

        return summarize_results(rows)
    raise ValueError(f"Result summarizer is not registered for runner {runner}")


def _build_checks(
    baseline_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
    policy: ThresholdPolicy,
) -> tuple[MetricRegressionCheck, ...]:
    checks: list[MetricRegressionCheck] = []
    for metric, threshold in policy.metrics:
        baseline = _required_metric(baseline_summary, metric, label="baseline")
        candidate = _optional_candidate_metric(candidate_summary, metric)
        checks.append(_check_metric(metric, baseline, candidate, threshold))
    return tuple(checks)


def _check_metric(
    metric: str,
    baseline: float,
    candidate: float | None,
    threshold: MetricThreshold,
) -> MetricRegressionCheck:
    if candidate is None:
        return MetricRegressionCheck(
            metric=metric,
            threshold=threshold,
            baseline=baseline,
            candidate=None,
            absolute_passed=False,
            regression_amount=None,
            regression_passed=False,
        )
    if threshold.direction == "higher":
        absolute_passed = (
            candidate + _FLOAT_TOLERANCE >= threshold.absolute_limit
        )
        regression_amount = max(0.0, baseline - candidate)
    else:
        absolute_passed = (
            candidate - _FLOAT_TOLERANCE <= threshold.absolute_limit
        )
        regression_amount = max(0.0, candidate - baseline)
    regression_passed = (
        regression_amount <= threshold.max_regression + _FLOAT_TOLERANCE
    )
    return MetricRegressionCheck(
        metric=metric,
        threshold=threshold,
        baseline=baseline,
        candidate=candidate,
        absolute_passed=absolute_passed,
        regression_amount=regression_amount,
        regression_passed=regression_passed,
    )


def _required_metric(
    summary: dict[str, Any],
    metric: str,
    *,
    label: str,
) -> float:
    value = summary.get(metric)
    if not _is_finite_number(value):
        raise ValueError(f"{label.title()} summary metric {metric} is missing or invalid")
    return float(value)


def _optional_candidate_metric(
    summary: dict[str, Any],
    metric: str,
) -> float | None:
    value = summary.get(metric)
    if value is None:
        return None
    if not _is_finite_number(value):
        raise ValueError(f"Candidate summary metric {metric} is invalid")
    return float(value)


def _is_finite_number(value: Any) -> TypeGuard[int | float]:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
    )


__all__ = [
    "MetricRegressionCheck",
    "ResultRegressionComparison",
    "compare_eval_results",
    "load_jsonl_rows",
]
