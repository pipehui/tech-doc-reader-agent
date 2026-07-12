from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import statistics
from typing import Any

from evals.artifacts import redact_artifact_rows, safe_artifact_text, write_jsonl
from evals.context_compaction_eval import (
    ContextCompactionCase,
    evaluate_context_compaction_case,
    load_context_compaction_cases,
)
from tech_doc_agent.app.core.context_compaction import ContextCompactionPolicy


DEFAULT_CASES = Path("evals/context_compaction_cases.json")


def run_all(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = load_context_compaction_cases(args.cases)
    if args.limit is not None:
        cases = cases[: args.limit]
    policy = ContextCompactionPolicy(
        max_messages=args.max_messages,
        max_serialized_bytes=args.max_serialized_bytes,
        keep_recent_turns=args.keep_recent_turns,
        summary_max_chars=args.summary_max_chars,
    )

    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {safe_artifact_text(case.id)}")
        try:
            row = evaluate_context_compaction_case(
                case,
                policy,
                iterations=args.iterations,
            )
        except Exception as exc:
            row = _error_row(case, policy, exc)
        rows.append(row)
        scores = row.get("scores", {})
        print(
            "  "
            f"status={row['status']} "
            f"consistent={_format_score(scores.get('answer_consistent'))} "
            f"checkpoint_reduction={_format_percent(scores.get('checkpoint_reduction_ratio'))} "
            f"token_proxy_reduction={_format_percent(scores.get('approximate_input_token_reduction_ratio'))}"
        )
    return rows


def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if row.get("status") == "done"]
    errored = [row for row in rows if row.get("status") == "error" or row.get("error")]
    return {
        "total": len(rows),
        "done": len(done),
        "errored": len(errored),
        "baseline_task_correct_avg": _score_mean(done, "baseline_task_correct"),
        "compacted_task_correct_avg": _score_mean(done, "compacted_task_correct"),
        "answer_consistent_avg": _score_mean(done, "answer_consistent"),
        "policy_expectation_match_avg": _score_mean(done, "policy_expectation_match"),
        "checkpoint_reduction_avg": _score_mean(done, "checkpoint_reduction_ratio"),
        "prompt_bytes_reduction_avg": _score_mean(done, "prompt_bytes_reduction_ratio"),
        "approximate_input_token_reduction_avg": _score_mean(
            done,
            "approximate_input_token_reduction_ratio",
        ),
        "compaction_latency_p50_ms": _field_percentile(
            done,
            ("compaction", "latency_p50_ms"),
            50,
        ),
        "compaction_latency_p95_ms": _field_percentile(
            done,
            ("compaction", "latency_p95_ms"),
            95,
        ),
    }


def summarize_by_category(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("category", "unspecified"))].append(row)
    return {
        category: summarize_results(category_rows)
        for category, category_rows in sorted(grouped.items())
    }


def render_markdown_report(rows: list[dict[str, Any]]) -> str:
    rows = redact_artifact_rows(rows)
    summary = summarize_results(rows)
    generated_at = datetime.now(timezone.utc).isoformat()
    policy = _policy_label(rows)
    lines = [
        "# Context Compaction Eval Report",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Cases: `{summary['total']}`",
        f"- Done: `{summary['done']}`",
        f"- Errored: `{summary['errored']}`",
        f"- Policy: `{policy}`",
        "- Answer metric: `deterministic_marker_recall_proxy`",
        "- Token metric: `langchain_count_tokens_approximately` (not provider usage)",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Baseline task correct | {_format_score(summary['baseline_task_correct_avg'])} |",
        f"| Compacted task correct | {_format_score(summary['compacted_task_correct_avg'])} |",
        f"| Answer consistency | {_format_score(summary['answer_consistent_avg'])} |",
        f"| Policy expectation match | {_format_score(summary['policy_expectation_match_avg'])} |",
        f"| Checkpoint byte reduction avg | {_format_percent(summary['checkpoint_reduction_avg'])} |",
        f"| Prompt byte reduction avg | {_format_percent(summary['prompt_bytes_reduction_avg'])} |",
        f"| Approx. input-token reduction avg | {_format_percent(summary['approximate_input_token_reduction_avg'])} |",
        f"| Compaction latency p50 | {_format_ms(summary['compaction_latency_p50_ms'])} |",
        f"| Compaction latency p95 | {_format_ms(summary['compaction_latency_p95_ms'])} |",
        "",
        "## By Category",
        "",
        "| Category | Cases | Compact Correct | Consistent | Checkpoint Reduction | Token Proxy Reduction |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for category, category_summary in summarize_by_category(rows).items():
        lines.append(
            "| "
            f"{_md(category)} | {category_summary['total']} | "
            f"{_format_score(category_summary['compacted_task_correct_avg'])} | "
            f"{_format_score(category_summary['answer_consistent_avg'])} | "
            f"{_format_percent(category_summary['checkpoint_reduction_avg'])} | "
            f"{_format_percent(category_summary['approximate_input_token_reduction_avg'])} |"
        )

    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| ID | Category | Turns | Baseline | Compacted | Expected | Consistent | Checkpoint Bytes | Prompt Tokens (Approx.) | Compaction p95 |",
            "|---|---|---:|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        baseline = row.get("baseline", {})
        compacted = row.get("compacted", {})
        scores = row.get("scores", {})
        compaction = row.get("compaction", {})
        lines.append(
            "| "
            f"{_md(row.get('id'))} | {_md(row.get('category'))} | {row.get('turn_count', 0)} | "
            f"{_md(baseline.get('answer_marker'))} | {_md(compacted.get('answer_marker'))} | "
            f"{_md(row.get('expected_marker'))} | {_format_score(scores.get('answer_consistent'))} | "
            f"{baseline.get('checkpoint_bytes', 'N/A')} -> {compacted.get('checkpoint_bytes', 'N/A')} | "
            f"{baseline.get('approximate_input_tokens', 'N/A')} -> {compacted.get('approximate_input_tokens', 'N/A')} | "
            f"{_format_ms(compaction.get('latency_p95_ms'))} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "This offline suite proves reducer, summary-lineage and deterministic information-retention behavior. "
            "It does not call a model, so `model_answer_consistency` and exact `provider_input_tokens` remain N/A. "
            "A provider-backed off/on run is required before enabling compaction by default.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the offline long-session context compaction comparison."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--max-messages", type=int, default=12)
    parser.add_argument("--max-serialized-bytes", type=int, default=0)
    parser.add_argument("--keep-recent-turns", type=int, default=3)
    parser.add_argument("--summary-max-chars", type=int, default=12_000)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval_results/context_compaction_latest.jsonl"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("eval_reports/context_compaction_latest.md"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = run_all(args)
    write_jsonl(args.output, rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown_report(rows), encoding="utf-8")
    summary = summarize_results(rows)
    print(
        "Summary: "
        f"done={summary['done']}/{summary['total']} "
        f"consistent={_format_score(summary['answer_consistent_avg'])} "
        f"checkpoint_reduction={_format_percent(summary['checkpoint_reduction_avg'])}"
    )
    print(f"JSONL: {args.output}")
    print(f"Report: {args.report}")
    return 1 if summary["errored"] else 0


def _error_row(
    case: ContextCompactionCase,
    policy: ContextCompactionPolicy,
    error: Exception,
) -> dict[str, Any]:
    return {
        "id": case.id,
        "category": case.category,
        "turn_count": case.turn_count,
        "status": "error",
        "error": f"{type(error).__name__}: {error}",
        "expected_marker": case.expected_marker,
        "expected_compacted_marker": case.expected_compacted_marker,
        "baseline": {},
        "compacted": {},
        "compaction": {
            "enabled": policy.enabled,
            "max_messages": policy.max_messages,
            "max_serialized_bytes": policy.max_serialized_bytes,
            "keep_recent_turns": policy.keep_recent_turns,
            "summary_max_chars": policy.summary_max_chars,
        },
        "scores": {},
    }


def _score_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [
        row.get("scores", {}).get(key)
        for row in rows
        if isinstance(row.get("scores", {}).get(key), (int, float))
    ]
    return statistics.mean(values) if values else None


def _field_percentile(
    rows: list[dict[str, Any]],
    path: tuple[str, str],
    percentile: float,
) -> float | None:
    values = [
        row.get(path[0], {}).get(path[1])
        for row in rows
        if isinstance(row.get(path[0], {}).get(path[1]), (int, float))
    ]
    if not values:
        return None
    values = sorted(float(value) for value in values)
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _policy_label(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        policy = row.get("compaction")
        if isinstance(policy, dict):
            return (
                f"max_messages={policy.get('max_messages')}, "
                f"max_bytes={policy.get('max_serialized_bytes')}, "
                f"keep_turns={policy.get('keep_recent_turns')}, "
                f"summary_chars={policy.get('summary_max_chars')}"
            )
    return "unknown"


def _format_score(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.2f}"


def _format_percent(value: Any) -> str:
    return "N/A" if value is None else f"{float(value) * 100:.1f}%"


def _format_ms(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.3f}ms"


def _md(value: Any) -> str:
    if value is None:
        return "None"
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "main",
    "render_markdown_report",
    "run_all",
    "summarize_by_category",
    "summarize_results",
]
