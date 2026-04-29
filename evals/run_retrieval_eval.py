from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tech_doc_agent.app.services.resources import AppResources, reset_app_resources
from tech_doc_agent.app.services.retrieval import HybridRetriever, RetrievalMode


DEFAULT_CASES = Path("evals/retrieval_cases.json")
DEFAULT_TOP_K = 5


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        cases = json.load(file)

    if not isinstance(cases, list):
        raise ValueError(f"{path} must contain a JSON array of retrieval eval cases.")

    for case in cases:
        _validate_case(case)

    return cases


def run_case(
    case: dict[str, Any],
    retriever: HybridRetriever,
    *,
    default_top_k: int,
    mode: RetrievalMode = "hybrid",
) -> dict[str, Any]:
    top_k = int(case.get("top_k") or default_top_k)
    filters = case.get("filters") or {}
    started_at = time.perf_counter()

    try:
        results = retriever.search(case["query"], top_k=top_k, mode=mode, filters=filters)
        elapsed = time.perf_counter() - started_at
        scores = score_case(case, results, top_k=top_k)
        return {
            "id": case["id"],
            "mode": mode,
            "filters": filters,
            "category": case["category"],
            "query_type": case.get("query_type", "unspecified"),
            "difficulty": case.get("difficulty", "unspecified"),
            "query": case["query"],
            "top_k": top_k,
            "expected_titles": case["expected_titles"],
            "expected_keywords": case.get("expected_keywords", []),
            "retrieved": results,
            "retrieved_titles": [str(item.get("title", "")) for item in results],
            "match_types": _match_type_counts(results),
            "e2e_s": elapsed,
            "status": "done",
            "error": None,
            "scores": scores,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started_at
        return {
            "id": case["id"],
            "mode": mode,
            "filters": filters,
            "category": case["category"],
            "query_type": case.get("query_type", "unspecified"),
            "difficulty": case.get("difficulty", "unspecified"),
            "query": case["query"],
            "top_k": top_k,
            "expected_titles": case["expected_titles"],
            "expected_keywords": case.get("expected_keywords", []),
            "retrieved": [],
            "retrieved_titles": [],
            "match_types": {},
            "e2e_s": elapsed,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "scores": {
                "hit_at_1": 0.0,
                "recall_at_k": 0.0,
                "mrr": 0.0,
                "keyword_coverage": _keyword_coverage(case, []),
            },
        }


def score_case(case: dict[str, Any], results: list[dict[str, Any]], *, top_k: int) -> dict[str, float | None]:
    top_results = results[:top_k]
    retrieved_titles = [_normalize_title(item.get("title")) for item in top_results]
    expected_titles = [_normalize_title(title) for title in case["expected_titles"]]
    hits = [
        expected_title
        for expected_title in expected_titles
        if any(_title_matches(retrieved_title, expected_title) for retrieved_title in retrieved_titles)
    ]

    first_rank = None
    for index, title in enumerate(retrieved_titles, start=1):
        if any(_title_matches(title, expected_title) for expected_title in expected_titles):
            first_rank = index
            break

    return {
        "hit_at_1": 1.0 if first_rank == 1 else 0.0,
        "recall_at_k": len(hits) / len(expected_titles) if expected_titles else None,
        "mrr": 1 / first_rank if first_rank else 0.0,
        "keyword_coverage": _keyword_coverage(case, top_results),
    }


def run_all(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = load_cases(args.cases)
    skipped = [case for case in cases if case.get("enabled", True) is False]
    if not args.include_disabled:
        cases = [case for case in cases if case.get("enabled", True) is not False]
    if args.limit is not None:
        cases = cases[: args.limit]

    if skipped and not args.include_disabled:
        print(f"Skipping {len(skipped)} disabled retrieval case(s). Use --include-disabled to run them.")

    resources = AppResources.create()
    if args.vector_top_k is not None:
        resources.hybrid_retriever.vector_top_k = args.vector_top_k

    try:
        results: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['id']} {case['query'][:60]}")
            result = run_case(case, resources.hybrid_retriever, default_top_k=args.k, mode=args.mode)
            results.append(result)
            scores = result["scores"]
            print(
                "  "
                f"status={result['status']} "
                f"mode={result['mode']} "
                f"recall@{result['top_k']}={format_score(scores.get('recall_at_k'))} "
                f"mrr={format_score(scores.get('mrr'))} "
                f"keywords={format_score(scores.get('keyword_coverage'))}"
            )
            if result.get("error"):
                print(f"  error={result['error']}")
        return results
    finally:
        reset_app_resources()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def render_markdown_report(rows: list[dict[str, Any]]) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    summary = summarize_results(rows)
    top_k_label = _top_k_label(rows)
    mode_label = _mode_label(rows)
    lines = [
        "# Retrieval Eval Report",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Mode: `{mode_label}`",
        f"- Cases: `{summary['total']}`",
        f"- Done: `{summary['done']}`",
        f"- Errored: `{summary['errored']}`",
        f"- Top K: `{top_k_label}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Recall@K avg | {format_score(summary['recall_at_k_avg'])} |",
        f"| Hit@1 avg | {format_score(summary['hit_at_1_avg'])} |",
        f"| MRR avg | {format_score(summary['mrr_avg'])} |",
        f"| Keyword coverage avg | {format_score(summary['keyword_coverage_avg'])} |",
        f"| E2E p50 | {format_seconds(summary['e2e_p50'])} |",
        f"| E2E p95 | {format_seconds(summary['e2e_p95'])} |",
        "",
        "## By Category",
        "",
        "| Category | Cases | Recall@K | Hit@1 | MRR | Keyword Coverage | E2E p50 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    for category, stats in summarize_by_category(rows).items():
        lines.append(
            "| "
            f"{_md_text(category)} | {stats['total']} | "
            f"{format_score(stats['recall_at_k_avg'])} | {format_score(stats['hit_at_1_avg'])} | "
            f"{format_score(stats['mrr_avg'])} | "
            f"{format_score(stats['keyword_coverage_avg'])} | {format_seconds(stats['e2e_p50'])} |"
        )

    if any(row.get("query_type") for row in rows):
        lines.extend(
            [
                "",
                "## By Query Type",
                "",
                "| Query Type | Cases | Recall@K | Hit@1 | MRR | Keyword Coverage |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for query_type, stats in summarize_by_field(rows, "query_type").items():
            lines.append(
                "| "
                f"{_md_text(query_type)} | {stats['total']} | "
                f"{format_score(stats['recall_at_k_avg'])} | {format_score(stats['hit_at_1_avg'])} | "
                f"{format_score(stats['mrr_avg'])} | {format_score(stats['keyword_coverage_avg'])} |"
            )

    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| ID | Category | Type | Status | Query | Expected Titles | Top Titles | Recall@K | Hit@1 | MRR | Keywords | E2E |",
            "|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        scores = row.get("scores", {})
        lines.append(
            "| "
            f"{_md_text(row['id'])} | {_md_text(row['category'])} | {_md_text(row.get('query_type', ''))} | "
            f"{_md_text(row['status'])} | "
            f"{_md_text(_query_with_filters(row))} | {_md_text(_join_titles(row.get('expected_titles', [])))} | "
            f"{_md_text(_join_titles(row.get('retrieved_titles', [])[: row.get('top_k', DEFAULT_TOP_K)]))} | "
            f"{format_score(scores.get('recall_at_k'))} | {format_score(scores.get('hit_at_1'))} | "
            f"{format_score(scores.get('mrr'))} | "
            f"{format_score(scores.get('keyword_coverage'))} | {format_seconds(row.get('e2e_s'))} |"
        )

    lines.append("")
    return "\n".join(lines)


def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if row.get("status") == "done"]
    errored = [row for row in rows if row.get("status") == "error" or row.get("error")]
    return {
        "total": len(rows),
        "done": len(done),
        "errored": len(errored),
        "recall_at_k_avg": _mean_score(done, "recall_at_k"),
        "hit_at_1_avg": _mean_score(done, "hit_at_1"),
        "mrr_avg": _mean_score(done, "mrr"),
        "keyword_coverage_avg": _mean_score(done, "keyword_coverage"),
        "e2e_p50": _percentile([row["e2e_s"] for row in done if row.get("e2e_s") is not None], 50),
        "e2e_p95": _percentile([row["e2e_s"] for row in done if row.get("e2e_s") is not None], 95),
    }


def summarize_by_category(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return summarize_by_field(rows, "category")


def summarize_by_field(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field, "unspecified"))].append(row)
    return {category: summarize_results(items) for category, items in sorted(grouped.items())}


def format_score(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.2f}"


def format_seconds(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.3f}s"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run offline retrieval eval cases against the HybridRetriever.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--mode",
        choices=("bm25", "vector", "hybrid"),
        default="hybrid",
        help="Retrieval mode used for this run.",
    )
    parser.add_argument("--k", type=int, default=DEFAULT_TOP_K, help="Default top-k used for Recall@K.")
    parser.add_argument("--vector-top-k", type=int, default=None, help="Override semantic/vector candidate count.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-disabled", action="store_true", help="Run cases marked enabled=false.")
    parser.add_argument("--output", type=Path, default=Path("eval_results/retrieval_latest.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("eval_reports/retrieval_latest.md"))
    return parser.parse_args()


def _validate_case(case: Any) -> None:
    if not isinstance(case, dict):
        raise ValueError("Each retrieval eval case must be an object.")

    required = {"id", "category", "query", "expected_titles"}
    missing = required - set(case)
    if missing:
        raise ValueError(f"Retrieval eval case missing required fields: {sorted(missing)}")

    if not isinstance(case["expected_titles"], list) or not case["expected_titles"]:
        raise ValueError("Retrieval eval case expected_titles must be a non-empty list.")
    if not all(isinstance(title, str) and title.strip() for title in case["expected_titles"]):
        raise ValueError("Retrieval eval case expected_titles must contain non-empty strings.")

    keywords = case.get("expected_keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(keyword, str) for keyword in keywords):
        raise ValueError("Retrieval eval case expected_keywords must be a list of strings.")

    for key in ("query_type", "difficulty"):
        value = case.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Retrieval eval case {key} must be a string.")

    filters = case.get("filters")
    if filters is not None and not isinstance(filters, dict):
        raise ValueError("Retrieval eval case filters must be an object.")

    top_k = case.get("top_k")
    if top_k is not None and int(top_k) <= 0:
        raise ValueError("Retrieval eval case top_k must be positive.")


def _keyword_coverage(case: dict[str, Any], results: list[dict[str, Any]]) -> float | None:
    keywords = [str(keyword).strip().lower() for keyword in case.get("expected_keywords", []) if str(keyword).strip()]
    if not keywords:
        return None

    haystack = "\n".join(
        f"{item.get('title', '')}\n{item.get('content', '')}\n"
        f"{' '.join(str(chunk.get('text', '')) for chunk in item.get('matched_chunks', []))}"
        for item in results
    ).lower()
    return sum(1 for keyword in keywords if keyword in haystack) / len(keywords)


def _match_type_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in results:
        for match_type in str(item.get("match_type", "")).split("+"):
            if match_type:
                counts[match_type] += 1
    return dict(counts)


def _normalize_title(value: Any) -> str:
    return str(value or "").strip().casefold()


def _title_matches(retrieved_title: str, expected_title: str) -> bool:
    return retrieved_title == expected_title or expected_title in retrieved_title or retrieved_title in expected_title


def _mean_score(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [
        row.get("scores", {}).get(key)
        for row in rows
        if row.get("scores", {}).get(key) is not None
    ]
    return statistics.mean(values) if values else None


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int(len(ordered) * percentile / 100)
    return ordered[min(index, len(ordered) - 1)]


def _top_k_label(rows: list[dict[str, Any]]) -> str:
    values = sorted({int(row.get("top_k", DEFAULT_TOP_K)) for row in rows})
    return ",".join(str(value) for value in values) if values else str(DEFAULT_TOP_K)


def _mode_label(rows: list[dict[str, Any]]) -> str:
    values = sorted({str(row.get("mode", "unknown")) for row in rows})
    return ",".join(values) if values else "unknown"


def _join_titles(titles: list[Any]) -> str:
    return ", ".join(str(title) for title in titles)


def _query_with_filters(row: dict[str, Any]) -> str:
    filters = row.get("filters") or {}
    if not filters:
        return str(row["query"])
    return f"{row['query']} filters={json.dumps(filters, ensure_ascii=False, sort_keys=True)}"


def _md_text(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def main() -> None:
    args = parse_args()
    rows = run_all(args)
    write_jsonl(args.output, rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown_report(rows), encoding="utf-8")
    print(f"Raw retrieval results saved to {args.output}")
    print(f"Markdown retrieval report saved to {args.report}")


if __name__ == "__main__":
    main()
