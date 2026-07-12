from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from evals.manifest_compatibility import compare_eval_run_manifests


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether two eval run manifests describe comparable runs "
            "before their metrics are diffed."
        )
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow known dirty worktrees; unknown Git provenance still fails verification.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        baseline = _load_manifest(args.baseline)
        candidate = _load_manifest(args.candidate)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "compatible": False,
                    "differences": [],
                    "verification_issues": [
                        {
                            "code": "manifest_file_invalid",
                            "path": "input",
                            "message": f"{type(exc).__name__}: {exc}",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    result = compare_eval_run_manifests(
        baseline,
        candidate,
        allow_dirty=args.allow_dirty,
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    if result.status == "compatible":
        return 0
    if result.status == "incompatible":
        return 1
    return 2


def _load_manifest(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Eval run manifest file must contain a JSON object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
