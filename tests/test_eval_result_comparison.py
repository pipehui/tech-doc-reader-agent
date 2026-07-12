import copy
import json
from pathlib import Path

import pytest

from evals.check_result_regression import main as regression_main
from evals.manifests import RuntimeIdentityLookup, build_eval_run_manifest
from evals.result_comparison import compare_eval_results
from evals.thresholds import ThresholdPolicy


def _manifest(tmp_path, *, commit: str = "a" * 40) -> dict:
    dataset = tmp_path / "cases.json"
    dataset.write_text('[{"id":"case-a"},{"id":"case-b"}]', encoding="utf-8")
    manifest = build_eval_run_manifest(
        runner="offline_context_compaction_eval",
        dataset_path=dataset,
        settings={"iterations": 10, "metric": "deterministic"},
        runtime_identity=RuntimeIdentityLookup(status="not_applicable"),
        repository_root=tmp_path / "not-a-repository",
    )
    manifest["runner_git"] = {"commit": commit, "dirty": False}
    return manifest


def _policy(*, runner: str = "offline_context_compaction_eval") -> ThresholdPolicy:
    return ThresholdPolicy.from_payload(
        {
            "schema_version": 1,
            "policy_id": "test-context.v1",
            "runner": runner,
            "metrics": {
                "answer_consistent_avg": {
                    "direction": "higher",
                    "absolute_limit": 0.75,
                    "max_regression": 0.10,
                },
                "errored": {
                    "direction": "lower",
                    "absolute_limit": 0,
                    "max_regression": 0,
                },
            },
        }
    )


def _rows(*, second_score: float = 1.0) -> list[dict]:
    return [
        {
            "id": "case-a",
            "status": "done",
            "error": None,
            "scores": {"answer_consistent": 1.0},
        },
        {
            "id": "case-b",
            "status": "done",
            "error": None,
            "scores": {"answer_consistent": second_score},
        },
    ]


def test_result_comparison_passes_only_after_manifest_and_metric_gates(tmp_path):
    baseline_manifest = _manifest(tmp_path)
    candidate_manifest = copy.deepcopy(baseline_manifest)
    candidate_manifest["runner_git"]["commit"] = "b" * 40

    result = compare_eval_results(
        baseline_manifest,
        candidate_manifest,
        _rows(),
        _rows(second_score=0.9),
        _policy(),
    )

    assert result.status == "passed"
    assert result.manifest_compatibility.status == "compatible"
    answer_check = next(
        check for check in result.checks if check.metric == "answer_consistent_avg"
    )
    assert answer_check.absolute_passed is True
    assert answer_check.regression_amount == pytest.approx(0.05)
    assert answer_check.regression_passed is True


def test_absolute_limit_and_regression_delta_fail_independently(tmp_path):
    baseline_manifest = _manifest(tmp_path)
    candidate_manifest = copy.deepcopy(baseline_manifest)

    result = compare_eval_results(
        baseline_manifest,
        candidate_manifest,
        _rows(),
        _rows(second_score=0.4),
        _policy(),
    )

    assert result.status == "failed"
    answer_check = next(
        check for check in result.checks if check.metric == "answer_consistent_avg"
    )
    assert answer_check.candidate == 0.7
    assert answer_check.absolute_passed is False
    assert answer_check.regression_amount == pytest.approx(0.3)
    assert answer_check.regression_passed is False


def test_lower_is_better_metric_and_missing_candidate_metric_fail(tmp_path):
    baseline_manifest = _manifest(tmp_path)
    candidate_manifest = copy.deepcopy(baseline_manifest)
    candidate_rows = _rows()
    candidate_rows[1] = {
        "id": "case-b",
        "status": "error",
        "error": "simulated",
        "scores": {},
    }

    errored = compare_eval_results(
        baseline_manifest,
        candidate_manifest,
        _rows(),
        candidate_rows,
        _policy(),
    )
    assert errored.status == "failed"
    error_check = next(check for check in errored.checks if check.metric == "errored")
    assert error_check.candidate == 1.0
    assert error_check.absolute_passed is False

    missing_rows = copy.deepcopy(candidate_rows)
    missing_rows[0]["scores"] = {}
    missing = compare_eval_results(
        baseline_manifest,
        candidate_manifest,
        _rows(),
        missing_rows,
        _policy(),
    )
    answer_check = next(
        check for check in missing.checks if check.metric == "answer_consistent_avg"
    )
    assert missing.status == "failed"
    assert answer_check.candidate is None
    assert answer_check.passed is False


def test_incompatible_manifest_prevents_metric_comparison(tmp_path):
    baseline_manifest = _manifest(tmp_path)
    candidate_manifest = copy.deepcopy(baseline_manifest)
    candidate_manifest["dataset"]["sha256"] = "f" * 64

    result = compare_eval_results(
        baseline_manifest,
        candidate_manifest,
        _rows(),
        _rows(),
        _policy(),
    )

    assert result.status == "not_comparable"
    assert result.manifest_compatibility.status == "incompatible"
    assert result.checks == ()


def test_case_id_or_policy_runner_mismatch_cannot_pass(tmp_path):
    baseline_manifest = _manifest(tmp_path)
    candidate_manifest = copy.deepcopy(baseline_manifest)
    candidate_rows = _rows()
    candidate_rows[1]["id"] = "case-c"

    case_mismatch = compare_eval_results(
        baseline_manifest,
        candidate_manifest,
        _rows(),
        candidate_rows,
        _policy(),
    )
    wrong_policy = compare_eval_results(
        baseline_manifest,
        candidate_manifest,
        _rows(),
        _rows(),
        _policy(runner="offline_retrieval_eval"),
    )

    assert case_mismatch.status == "failed"
    assert case_mismatch.issues[0].code == "result_case_ids_mismatch"
    assert wrong_policy.status == "invalid"


def test_result_regression_cli_uses_pass_fail_and_not_comparable_exit_codes(
    tmp_path,
    capsys,
):
    baseline_manifest = _manifest(tmp_path)
    candidate_manifest = copy.deepcopy(baseline_manifest)
    candidate_manifest["runner_git"]["commit"] = "b" * 40
    paths = {
        "baseline_manifest": tmp_path / "baseline.manifest.json",
        "candidate_manifest": tmp_path / "candidate.manifest.json",
        "baseline_results": tmp_path / "baseline.jsonl",
        "candidate_results": tmp_path / "candidate.jsonl",
        "policy": tmp_path / "policy.json",
    }
    paths["baseline_manifest"].write_text(
        json.dumps(baseline_manifest), encoding="utf-8"
    )
    paths["candidate_manifest"].write_text(
        json.dumps(candidate_manifest), encoding="utf-8"
    )
    _write_jsonl(paths["baseline_results"], _rows())
    _write_jsonl(paths["candidate_results"], _rows())
    paths["policy"].write_text(
        json.dumps(_policy().to_payload()), encoding="utf-8"
    )
    argv = _cli_args(paths)

    assert regression_main(argv) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"

    _write_jsonl(paths["candidate_results"], _rows(second_score=0.0))
    assert regression_main(argv) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"

    candidate_manifest["settings"]["values"]["metric"] = "changed"
    paths["candidate_manifest"].write_text(
        json.dumps(candidate_manifest), encoding="utf-8"
    )
    assert regression_main(argv) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _cli_args(paths: dict[str, Path]) -> list[str]:
    return [
        "--baseline-manifest",
        str(paths["baseline_manifest"]),
        "--baseline-results",
        str(paths["baseline_results"]),
        "--candidate-manifest",
        str(paths["candidate_manifest"]),
        "--candidate-results",
        str(paths["candidate_results"]),
        "--policy",
        str(paths["policy"]),
    ]
