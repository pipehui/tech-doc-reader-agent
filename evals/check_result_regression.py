from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from evals.artifacts import write_json
from evals.result_comparison import compare_eval_results, load_jsonl_rows
from evals.thresholds import load_threshold_policy


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Require compatible eval manifests, then apply versioned absolute "
            "and regression-delta metric thresholds."
        )
    )
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--baseline-results", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--candidate-results", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow known dirty worktrees for local diagnosis; CI should not use this.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        baseline_manifest = _load_json_object(args.baseline_manifest)
        candidate_manifest = _load_json_object(args.candidate_manifest)
        baseline_rows = load_jsonl_rows(args.baseline_results)
        candidate_rows = load_jsonl_rows(args.candidate_results)
        policy = load_threshold_policy(args.policy)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        payload = _invalid_input_payload(exc)
        _emit(payload, output=args.output)
        return 2

    result = compare_eval_results(
        baseline_manifest,
        candidate_manifest,
        baseline_rows,
        candidate_rows,
        policy,
        allow_dirty=args.allow_dirty,
    )
    payload = result.to_payload()
    _emit(payload, output=args.output)
    if result.status == "passed":
        return 0
    if result.status == "failed":
        return 1
    return 2


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _invalid_input_payload(exc: Exception) -> dict[str, Any]:
    return {
        "status": "invalid",
        "passed": False,
        "issues": [
            {
                "code": "regression_input_invalid",
                "message": f"{type(exc).__name__}: {exc}",
            }
        ],
    }


def _emit(payload: dict[str, Any], *, output: Path | None) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if output is not None:
        write_json(output, payload)


if __name__ == "__main__":
    raise SystemExit(main())
