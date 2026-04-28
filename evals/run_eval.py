from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from evals.judges import judge_case, normalize_plan


DEFAULT_API_URL = "http://127.0.0.1:8000/chat"
DEFAULT_CASES = Path("evals/cases.json")
DEFAULT_TIMEOUT = 240.0


async def iter_sse_events(response: httpx.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    event_name = "message"
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if line.startswith(":"):
            continue

        if not line:
            if data_lines:
                payload = _parse_sse_payload(data_lines)
                if payload is not None:
                    yield event_name, payload
            event_name = "message"
            data_lines.clear()
            continue

        if line.startswith("event:"):
            event_name = line[len("event:"):].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())

    if data_lines:
        payload = _parse_sse_payload(data_lines)
        if payload is not None:
            yield event_name, payload


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        cases = json.load(file)

    if not isinstance(cases, list):
        raise ValueError(f"{path} must contain a JSON array of eval cases.")

    for case in cases:
        _validate_case(case)

    return cases


async def run_case(
    client: httpx.AsyncClient,
    case: dict[str, Any],
    api_url: str,
    timeout_s: float,
) -> dict[str, Any]:
    session_id = f"eval_{case['id']}_{uuid.uuid4().hex[:8]}"
    payload = {
        "session_id": session_id,
        "message": case["input"],
    }

    t_start = time.perf_counter()
    t_first_token: float | None = None
    t_done: float | None = None
    token_count = 0
    tool_calls = 0
    tool_results = 0
    events_seen = 0
    token_parts: list[str] = []
    answer_parts: list[str] = []
    predicted_plan: list[str] = []
    predicted_learning_target: str | None = None
    final_status = "unknown"
    error: str | None = None

    try:
        async with asyncio.timeout(timeout_s):
            async with client.stream("POST", api_url, json=payload, timeout=timeout_s) as response:
                if response.status_code != 200:
                    return _error_result(case, session_id, t_start, f"HTTP {response.status_code}")

                async for event_name, event in iter_sse_events(response):
                    now = time.perf_counter()
                    events_seen += 1

                    if event_name == "token":
                        token_count += 1
                        if t_first_token is None:
                            t_first_token = now
                        text = event.get("text")
                        if isinstance(text, str):
                            token_parts.append(text)

                    elif event_name == "tool_call":
                        tool_calls += 1
                        if event.get("tool") == "PlanWorkflow":
                            args = event.get("args", {})
                            if isinstance(args, dict):
                                predicted_plan = normalize_plan(args.get("steps"))
                                predicted_learning_target = _string_or_none(args.get("learning_target"))

                    elif event_name == "tool_result":
                        tool_results += 1

                    elif event_name == "plan_update":
                        if "plan" in event:
                            predicted_plan = normalize_plan(event.get("plan"))
                        if "learning_target" in event:
                            predicted_learning_target = _string_or_none(event.get("learning_target"))

                    elif event_name == "agent_message":
                        content = event.get("content")
                        if isinstance(content, str) and content.strip():
                            answer_parts.append(content)

                    elif event_name == "done":
                        final_status = "done"
                        t_done = now
                        break

                    elif event_name == "interrupt_required":
                        final_status = "interrupted"
                        t_done = now
                        break

                    elif event_name == "error":
                        final_status = "error"
                        error = _string_or_none(event.get("message")) or "unknown error"
                        t_done = now
                        break

    except TimeoutError:
        return _error_result(case, session_id, t_start, f"TimeoutError: exceeded {timeout_s:.0f}s")
    except Exception as exc:
        return _error_result(case, session_id, t_start, f"{type(exc).__name__}: {exc}")

    elapsed = (t_done or time.perf_counter()) - t_start
    ttft = (t_first_token - t_start) if t_first_token else None
    answer = "\n\n".join(answer_parts).strip() or "".join(token_parts).strip()
    result = {
        "id": case["id"],
        "category": case["category"],
        "input": case["input"],
        "session_id": session_id,
        "expected_plan": normalize_plan(case.get("expected_plan")),
        "predicted_plan": predicted_plan,
        "expected_learning_target": case.get("expected_learning_target"),
        "predicted_learning_target": predicted_learning_target,
        "answer": answer,
        "ttft_s": ttft,
        "e2e_s": elapsed,
        "token_events": token_count,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "event_count": events_seen,
        "status": final_status,
        "error": error,
    }
    scores = judge_case(case, result)
    result["scores"] = {
        "plan_match": scores.plan_match,
        "keyword": scores.keyword,
        "latency": scores.latency,
    }
    return result


async def run_all(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = load_cases(args.cases)
    skipped = [case for case in cases if case.get("enabled", True) is False]
    if not args.include_disabled:
        cases = [case for case in cases if case.get("enabled", True) is not False]
    if args.limit is not None:
        cases = cases[: args.limit]

    if skipped and not args.include_disabled:
        print(f"Skipping {len(skipped)} disabled case(s). Use --include-disabled to run them.")

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['id']} {case['input'][:60]}")
            result = await run_case(client, case, args.api_url, args.timeout)
            results.append(result)
            status = result["status"]
            plan = ",".join(result.get("predicted_plan", [])) or "direct"
            plan_score = result.get("scores", {}).get("plan_match")
            e2e = result.get("e2e_s")
            e2e_text = f"{e2e:.2f}s" if isinstance(e2e, int | float) else "N/A"
            print(f"  status={status} plan={plan} plan_score={plan_score} e2e={e2e_text}")
            if result.get("error"):
                print(f"  error={result['error']}")

    return results


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def render_markdown_report(rows: list[dict[str, Any]]) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    summary = summarize_results(rows)
    lines = [
        "# Agent Eval Report",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Cases: `{summary['total']}`",
        f"- Done: `{summary['done']}`",
        f"- Interrupted: `{summary['interrupted']}`",
        f"- Errored: `{summary['errored']}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Plan match avg | {format_score(summary['plan_match_avg'])} |",
        f"| Keyword avg | {format_score(summary['keyword_avg'])} |",
        f"| E2E p50 | {format_seconds(summary['e2e_p50'])} |",
        f"| E2E p95 | {format_seconds(summary['e2e_p95'])} |",
        f"| Tool results avg | {format_number(summary['tool_results_avg'])} |",
        "",
        "## By Category",
        "",
        "| Category | Cases | Plan Match | Keyword | E2E p50 | Tool Results Avg |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for category, stats in summarize_by_category(rows).items():
        lines.append(
            "| "
            f"{category} | {stats['total']} | {format_score(stats['plan_match_avg'])} | "
            f"{format_score(stats['keyword_avg'])} | {format_seconds(stats['e2e_p50'])} | "
            f"{format_number(stats['tool_results_avg'])} |"
        )

    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| ID | Category | Status | Expected Plan | Predicted Plan | Plan | Keyword | E2E | Tool Results |",
            "|---|---|---|---|---|---:|---:|---:|---:|",
        ]
    )

    for row in rows:
        expected = ",".join(row.get("expected_plan", [])) or "direct"
        predicted = ",".join(row.get("predicted_plan", [])) or "direct"
        scores = row.get("scores", {})
        lines.append(
            "| "
            f"{row['id']} | {row['category']} | {row['status']} | {expected} | {predicted} | "
            f"{format_score(scores.get('plan_match'))} | {format_score(scores.get('keyword'))} | "
            f"{format_seconds(row.get('e2e_s'))} | {row.get('tool_results', 0)} |"
        )

    lines.append("")
    return "\n".join(lines)


def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if row.get("status") == "done"]
    interrupted = [row for row in rows if row.get("status") == "interrupted"]
    errored = [row for row in rows if row.get("status") == "error" or row.get("error")]
    return {
        "total": len(rows),
        "done": len(done),
        "interrupted": len(interrupted),
        "errored": len(errored),
        "plan_match_avg": _mean_score(rows, "plan_match"),
        "keyword_avg": _mean_score(rows, "keyword"),
        "e2e_p50": _percentile([row["e2e_s"] for row in done if row.get("e2e_s") is not None], 50),
        "e2e_p95": _percentile([row["e2e_s"] for row in done if row.get("e2e_s") is not None], 95),
        "tool_results_avg": statistics.mean([row.get("tool_results", 0) for row in done]) if done else None,
    }


def summarize_by_category(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["category"]].append(row)

    return {category: summarize_results(items) for category, items in sorted(grouped.items())}


def format_score(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.2f}"


def format_seconds(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.2f}s"


def format_number(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.2f}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run online eval cases against the /chat SSE endpoint.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-disabled", action="store_true", help="Run cases marked enabled=false.")
    parser.add_argument("--output", type=Path, default=Path("eval_results/latest.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("eval_reports/latest.md"))
    return parser.parse_args()


def _parse_sse_payload(lines: list[str]) -> dict[str, Any] | None:
    raw = "\n".join(lines).strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else {"value": payload}


def _validate_case(case: Any) -> None:
    if not isinstance(case, dict):
        raise ValueError("Each eval case must be an object.")

    required = {"id", "category", "input", "expected_plan"}
    missing = required - set(case)
    if missing:
        raise ValueError(f"Eval case missing required fields: {sorted(missing)}")

    normalize_plan(case["expected_plan"])


def _error_result(case: dict[str, Any], session_id: str, started_at: float, error: str) -> dict[str, Any]:
    result = {
        "id": case["id"],
        "category": case["category"],
        "input": case["input"],
        "session_id": session_id,
        "expected_plan": normalize_plan(case.get("expected_plan")),
        "predicted_plan": [],
        "expected_learning_target": case.get("expected_learning_target"),
        "predicted_learning_target": None,
        "answer": "",
        "ttft_s": None,
        "e2e_s": time.perf_counter() - started_at,
        "token_events": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "event_count": 0,
        "status": "error",
        "error": error,
    }
    scores = judge_case(case, result)
    result["scores"] = {
        "plan_match": scores.plan_match,
        "keyword": scores.keyword,
        "latency": scores.latency,
    }
    return result


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


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def async_main() -> None:
    args = parse_args()
    rows = await run_all(args)
    write_jsonl(args.output, rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown_report(rows), encoding="utf-8")
    print(f"Raw results saved to {args.output}")
    print(f"Markdown report saved to {args.report}")


if __name__ == "__main__":
    asyncio.run(async_main())
