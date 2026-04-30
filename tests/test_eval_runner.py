from pathlib import Path

from evals.run_eval import approve_url_for, load_cases, render_markdown_report, summarize_results


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
    assert summary["total"] == 2
    assert summary["done"] == 2
    assert summary["tool_results_avg"] == 1
    assert summary["structured_results_avg"] == 1
    assert summary["interrupts_total"] == 1


def test_eval_approve_url_defaults_from_chat_endpoint():
    assert approve_url_for("http://127.0.0.1:8000/chat", None) == "http://127.0.0.1:8000/chat/approve"
    assert approve_url_for("http://127.0.0.1:8000/api", None) == "http://127.0.0.1:8000/api/chat/approve"
    assert approve_url_for("http://127.0.0.1:8000/chat", "http://x/approve") == "http://x/approve"
