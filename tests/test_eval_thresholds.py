import copy
import json
from pathlib import Path

import pytest

from evals.thresholds import ThresholdPolicy, load_threshold_policy


def _policy_payload() -> dict:
    return {
        "schema_version": 1,
        "policy_id": "test-policy.v1",
        "runner": "offline_context_compaction_eval",
        "metrics": {
            "answer_consistent_avg": {
                "direction": "higher",
                "absolute_limit": 0.8,
                "max_regression": 0.05,
            },
            "errored": {
                "direction": "lower",
                "absolute_limit": 0,
                "max_regression": 0,
            },
        },
    }


def test_threshold_policy_is_versioned_canonical_and_order_independent():
    payload = _policy_payload()
    reordered = copy.deepcopy(payload)
    reordered["metrics"] = dict(reversed(list(reordered["metrics"].items())))

    first = ThresholdPolicy.from_payload(payload)
    second = ThresholdPolicy.from_payload(reordered)

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.to_payload()["metrics"]["answer_consistent_avg"] == {
        "direction": "higher",
        "absolute_limit": 0.8,
        "max_regression": 0.05,
    }


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("schema_version",), 2, "schema_version"),
        (("metrics", "answer_consistent_avg", "direction"), "sideways", "direction"),
        (("metrics", "answer_consistent_avg", "absolute_limit"), True, "must be a number"),
        (("metrics", "answer_consistent_avg", "max_regression"), -0.1, "non-negative"),
        (("metrics", "answer_consistent_avg", "max_regression"), float("inf"), "finite"),
    ],
)
def test_threshold_policy_rejects_invalid_contract(path, value, message):
    payload = _policy_payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=message):
        ThresholdPolicy.from_payload(payload)


def test_committed_context_policy_loads_with_absolute_and_delta_limits():
    policy = load_threshold_policy(
        Path("evals/policies/context_compaction_pr_v1.json")
    )
    metrics = dict(policy.metrics)

    assert policy.policy_id == "context_compaction_pr.v1"
    assert metrics["answer_consistent_avg"].absolute_limit == 0.83
    assert metrics["answer_consistent_avg"].max_regression == 0.0
    assert metrics["checkpoint_reduction_avg"].max_regression == 0.03
    assert "compaction_latency_p95_ms" not in metrics


def test_threshold_policy_loader_rejects_non_object(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps([]), encoding="utf-8")

    with pytest.raises(ValueError, match="must be an object"):
        load_threshold_policy(path)
