import json
from pathlib import Path

import pytest

from evals.context_compaction_eval import (
    ContextCompactionCase,
    evaluate_context_compaction_case,
    load_context_compaction_cases,
)
from evals.run_context_compaction_eval import (
    render_markdown_report,
    summarize_by_category,
    summarize_results,
)
from tech_doc_agent.app.core.context_compaction import ContextCompactionPolicy


CASES_PATH = Path("evals/context_compaction_cases.json")
POLICY = ContextCompactionPolicy(max_messages=12, keep_recent_turns=3)


def test_context_compaction_cases_are_valid_and_cover_known_risk_categories():
    cases = load_context_compaction_cases(CASES_PATH)

    assert len(cases) == 6
    assert len({case.id for case in cases}) == len(cases)
    assert {case.category for case in cases} >= {
        "closed_text_recall",
        "raw_tool_dependency",
        "recency_precedence",
        "summary_bound",
    }
    tool_only = next(case for case in cases if case.id == "tool_only_fact_is_not_copied")
    assert tool_only.expected_compacted_marker is None


def test_case_validation_rejects_marker_outside_declared_turns(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "invalid",
                    "category": "invalid",
                    "turn_count": 2,
                    "markers": [
                        {"value": "MARKER", "turn": 2, "role": "human"}
                    ],
                    "expected_marker": "MARKER",
                    "expected_compacted_marker": "MARKER",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Marker turn"):
        load_context_compaction_cases(path)


def test_offline_eval_preserves_closed_text_and_reports_measured_reductions():
    case = _case("early_user_fact")

    row = evaluate_context_compaction_case(case, POLICY, iterations=2)

    assert row["status"] == "done"
    assert row["scores"]["baseline_task_correct"] == 1.0
    assert row["scores"]["compacted_task_correct"] == 1.0
    assert row["scores"]["answer_consistent"] == 1.0
    assert row["scores"]["checkpoint_reduction_ratio"] > 0
    assert row["baseline"]["provider_input_tokens"] is None
    assert row["limitations"]["model_answer_consistency"] is None
    assert row["compaction"]["events"] > 0


def test_offline_eval_exposes_raw_tool_dependency_instead_of_hiding_it():
    case = _case("tool_only_fact_is_not_copied")

    row = evaluate_context_compaction_case(case, POLICY, iterations=1)

    assert row["baseline"]["answer_marker"] == "TOOL-ONLY-DELTA-488"
    assert row["compacted"]["answer_marker"] is None
    assert row["scores"]["compacted_task_correct"] == 0.0
    assert row["scores"]["answer_consistent"] == 0.0
    assert row["scores"]["policy_expectation_match"] == 1.0


def test_summary_and_report_keep_proxy_limitations_explicit():
    rows = [
        evaluate_context_compaction_case(_case("early_user_fact"), POLICY, iterations=1),
        evaluate_context_compaction_case(
            _case("tool_only_fact_is_not_copied"),
            POLICY,
            iterations=1,
        ),
    ]

    summary = summarize_results(rows)
    by_category = summarize_by_category(rows)
    report = render_markdown_report(rows)

    assert summary["total"] == 2
    assert summary["answer_consistent_avg"] == 0.5
    assert summary["checkpoint_reduction_avg"] > 0
    assert by_category["raw_tool_dependency"]["compacted_task_correct_avg"] == 0.0
    assert "deterministic_marker_recall_proxy" in report
    assert "not provider usage" in report
    assert "model_answer_consistency" in report
    assert "tool_only_fact_is_not_copied" in report

    manifested_report = render_markdown_report(
        rows,
        manifest={
            "dataset": {"sha256": "dataset-hash"},
            "settings": {"fingerprint": "settings-hash"},
            "runner_git": {"commit": "commit-a"},
        },
    )
    assert "Dataset SHA-256: `dataset-hash`" in manifested_report
    assert "Runtime identity: `not_applicable`" in manifested_report


def test_case_payload_rejects_unsupported_marker_role():
    payload = {
        "id": "bad-role",
        "category": "invalid",
        "turn_count": 2,
        "markers": [{"value": "MARKER", "turn": 0, "role": "system"}],
        "expected_marker": "MARKER",
        "expected_compacted_marker": "MARKER",
    }

    with pytest.raises(ValueError, match="role is unsupported"):
        ContextCompactionCase.from_payload(payload)


def _case(case_id: str) -> ContextCompactionCase:
    return next(case for case in load_context_compaction_cases(CASES_PATH) if case.id == case_id)
