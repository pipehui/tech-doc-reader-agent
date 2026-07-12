from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evals.manifests import fingerprint_payload


ThresholdDirection = Literal["higher", "lower"]


@dataclass(frozen=True, slots=True)
class MetricThreshold:
    direction: ThresholdDirection
    absolute_limit: float
    max_regression: float

    @classmethod
    def from_payload(cls, payload: Any, *, metric: str) -> MetricThreshold:
        if not isinstance(payload, dict):
            raise ValueError(f"Threshold for {metric} must be an object")
        direction = payload.get("direction")
        if direction not in ("higher", "lower"):
            raise ValueError(f"Threshold direction for {metric} is invalid")
        absolute_limit = _finite_number(
            payload.get("absolute_limit"),
            field=f"metrics.{metric}.absolute_limit",
        )
        max_regression = _finite_number(
            payload.get("max_regression"),
            field=f"metrics.{metric}.max_regression",
        )
        if max_regression < 0:
            raise ValueError(f"Threshold max_regression for {metric} must be non-negative")
        return cls(
            direction=direction,
            absolute_limit=absolute_limit,
            max_regression=max_regression,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "absolute_limit": self.absolute_limit,
            "max_regression": self.max_regression,
        }


@dataclass(frozen=True, slots=True)
class ThresholdPolicy:
    policy_id: str
    runner: str
    metrics: tuple[tuple[str, MetricThreshold], ...]
    schema_version: int = 1

    @classmethod
    def from_payload(cls, payload: Any) -> ThresholdPolicy:
        if not isinstance(payload, dict):
            raise ValueError("Threshold policy must be an object")
        if payload.get("schema_version") != 1:
            raise ValueError("Threshold policy schema_version is unsupported")
        policy_id = _required_text(payload.get("policy_id"), field="policy_id")
        runner = _required_text(payload.get("runner"), field="runner")
        raw_metrics = payload.get("metrics")
        if not isinstance(raw_metrics, dict) or not raw_metrics:
            raise ValueError("Threshold policy metrics must be a non-empty object")

        metrics: list[tuple[str, MetricThreshold]] = []
        for metric, raw_threshold in raw_metrics.items():
            metric_name = _required_text(metric, field="metric name")
            metrics.append(
                (
                    metric_name,
                    MetricThreshold.from_payload(
                        raw_threshold,
                        metric=metric_name,
                    ),
                )
            )
        return cls(
            policy_id=policy_id,
            runner=runner,
            metrics=tuple(sorted(metrics)),
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint_payload(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "runner": self.runner,
            "metrics": {
                metric: threshold.to_payload()
                for metric, threshold in self.metrics
            },
        }


def load_threshold_policy(path: Path) -> ThresholdPolicy:
    return ThresholdPolicy.from_payload(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"Threshold policy {field} must be a non-empty trimmed string")
    return value


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Threshold policy {field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Threshold policy {field} must be finite")
    return number


__all__ = [
    "MetricThreshold",
    "ThresholdPolicy",
    "load_threshold_policy",
]
