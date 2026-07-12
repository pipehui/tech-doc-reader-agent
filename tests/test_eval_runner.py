import asyncio
import json
from pathlib import Path

import httpx

from evals.run_eval import (
    approve_url_for,
    load_cases,
    render_markdown_report,
    run_case,
    summarize_results,
)
from tech_doc_agent.app.core.retry_usage import RetryUsage, RetryUsageLedger


def test_eval_cases_are_valid():
    cases = load_cases(Path("evals/cases.json"))
    full_cases = load_cases(Path("evals/cases_full.json"))

    assert len(cases) >= 15
    assert len(full_cases) >= 24
    assert {case["category"] for case in cases}
    assert {case["category"] for case in full_cases}


def test_render_markdown_report_contains_summary_and_cases():
    rows = [
        {
            "id": "case_1",
            "category": "direct",
            "status": "done",
            "expected_plan": [],
            "predicted_plan": [],
            "e2e_s": 1.2,
            "tool_calls": 0,
            "tool_results": 0,
            "structured_result_count": 0,
            "interrupt_count": 0,
            "scores": {"plan_match": 1.0, "keyword": 1.0, "behavior": 1.0, "latency": 1.0},
        },
        {
            "id": "case_2",
            "category": "multi_agent_standard",
            "status": "done",
            "expected_plan": ["parser", "relation", "explanation"],
            "predicted_plan": ["parser", "explanation"],
            "e2e_s": 10.0,
            "tool_calls": 2,
            "tool_results": 2,
            "structured_result_count": 2,
            "interrupt_count": 1,
            "provider_retry_usage": _provider_retry_usage(),
            "scores": {"plan_match": 0.5, "keyword": 0.5, "behavior": 0.5, "latency": 0.8},
        },
    ]

    report = render_markdown_report(rows)
    summary = summarize_results(rows)

    assert "# Agent Eval Report" in report
    assert "case_1" in report
    assert "Tool results avg" in report
    assert "Behavior avg" in report
    assert "Structured results avg" in report
    assert "Interrupts total" in report
    assert "Provider retries total" in report
    assert "Provider exhausted operations" in report
    assert summary["total"] == 2
    assert summary["done"] == 2
    assert summary["tool_results_avg"] == 1
    assert summary["structured_results_avg"] == 1
    assert summary["interrupts_total"] == 1
    assert summary["provider_operations_total"] == 2
    assert summary["provider_attempts_total"] == 4
    assert summary["provider_retries_total"] == 2
    assert summary["provider_recovered_operations_total"] == 1
    assert summary["provider_exhausted_operations_total"] == 1

    manifested_report = render_markdown_report(
        rows,
        manifest={
            "runtime_identity": {
                "status": "available",
                "manifest": {
                    "fingerprint": "runtime-hash",
                    "deployment": {
                        "status": "configured",
                        "commit_sha": "d" * 40,
                    },
                },
            },
            "dataset": {"sha256": "dataset-hash"},
            "settings": {"fingerprint": "settings-hash"},
            "runner_git": {"commit": "runner-commit"},
        },
    )
    assert "Deployment identity: `configured`" in manifested_report
    assert f"Deployment commit: `{'d' * 40}`" in manifested_report


def test_eval_approve_url_defaults_from_chat_endpoint():
    assert approve_url_for("http://127.0.0.1:8000/chat", None) == "http://127.0.0.1:8000/chat/approve"
    assert approve_url_for("http://127.0.0.1:8000/api", None) == "http://127.0.0.1:8000/api/chat/approve"
    assert approve_url_for("http://127.0.0.1:8000/chat", "http://x/approve") == "http://x/approve"


def test_run_case_collects_provider_retry_operation_deltas():
    operation = RetryUsage(
        operation="embedding.create",
        dependency="embedding",
        tool="read_docs",
        idempotent=True,
        attempts=2,
        retries=1,
        waited_seconds=0.25,
        outcome="succeeded",
        reason="completed",
    )
    response_body = "".join(
        [
            _sse("provider_retry_update", {"delta": {"kind": "reset"}}),
            _sse(
                "provider_retry_update",
                {
                    "delta": {
                        "kind": "operations",
                        "operations": [operation.to_payload()],
                    }
                },
            ),
            _sse("done", {"session_id": "eval-case"}),
        ]
    )

    async def execute() -> dict:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=response_body,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await run_case(
                client,
                {
                    "id": "retry_case",
                    "category": "direct",
                    "input": "Explain retries",
                    "expected_plan": [],
                },
                "http://eval/chat",
                "http://eval/chat/approve",
                5.0,
            )

    result = asyncio.run(execute())

    assert result["status"] == "done"
    assert result["provider_retry_usage"]["summary"] == {
        "operations": 1,
        "attempts": 2,
        "retries": 1,
        "waited_seconds": 0.25,
        "recovered_operations": 1,
        "exhausted_operations": 0,
        "failed_operations": 0,
        "dependencies": {
            "embedding": {
                "operations": 1,
                "attempts": 2,
                "retries": 1,
                "waited_seconds": 0.25,
            }
        },
    }


def _provider_retry_usage() -> dict:
    ledger = RetryUsageLedger().record(
        (
            RetryUsage(
                operation="embedding.create",
                dependency="embedding",
                tool="read_docs",
                idempotent=True,
                attempts=2,
                retries=1,
                waited_seconds=0.1,
                outcome="succeeded",
                reason="completed",
            ),
            RetryUsage(
                operation="web_search.duckduckgo",
                dependency="duckduckgo",
                tool="web_search",
                idempotent=True,
                attempts=2,
                retries=1,
                waited_seconds=0.2,
                outcome="exhausted",
                reason="max_attempts_exhausted",
                error_code="dependency_timeout",
            ),
        )
    )
    return ledger.to_state()


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"
