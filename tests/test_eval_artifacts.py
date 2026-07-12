import copy
import json
from pathlib import Path

from evals.artifacts import safe_artifact_text, write_jsonl
from evals.run_retrieval_eval import render_markdown_report as render_retrieval_report
from tech_doc_agent.app.core.redaction import RedactionPolicy


def test_eval_jsonl_redacts_without_mutating_rows_used_by_judges(tmp_path):
    rows = [
        {
            "id": "case-sensitive",
            "user_id": "stable-user",
            "session_id": "550e8400-e29b-41d4-a716-446655440000",
            "input": "email person@example.com Authorization: Bearer private-token",
            "answer": "provider key sk-proj-abcdefghijklmnop",
        }
    ]
    original = copy.deepcopy(rows)
    output = tmp_path / "eval.jsonl"

    write_jsonl(
        output,
        rows,
        policy=RedactionPolicy(pseudonymization_key="controlled-key-with-32-random-bytes"),
    )

    written = json.loads(output.read_text(encoding="utf-8"))
    assert rows == original
    assert written["user_id"].startswith("pseudonym:")
    assert written["session_id"] == rows[0]["session_id"]
    assert "person@example.com" not in written["input"]
    assert "private-token" not in written["input"]
    assert "sk-proj" not in written["answer"]


def test_retrieval_markdown_report_uses_same_redaction_policy():
    report = render_retrieval_report(
        [
            {
                "id": "case_person@example.com",
                "mode": "bm25",
                "filters": {},
                "category": "security",
                "query_type": "conceptual",
                "query": "Authorization: Bearer private-token",
                "top_k": 1,
                "expected_titles": ["Credential sk-proj-abcdefghijklmnop"],
                "retrieved_titles": [],
                "status": "done",
                "e2e_s": 0.01,
                "scores": {
                    "recall_at_k": 0.0,
                    "hit_at_1": 0.0,
                    "mrr": 0.0,
                    "keyword_coverage": 0.0,
                },
            }
        ]
    )

    assert "person@example.com" not in report
    assert "private-token" not in report
    assert "sk-proj-abcdefghijklmnop" not in report
    assert "[REDACTED:EMAIL]" in report
    assert "[REDACTED:AUTHORIZATION]" in report


def test_eval_console_text_uses_artifact_redaction():
    safe = safe_artifact_text("contact person@example.com with api_key=private-value")

    assert "person@example.com" not in safe
    assert "private-value" not in safe


def test_eval_runners_delegate_artifact_safety_to_shared_module():
    for path in (
        Path("evals/run_eval.py"),
        Path("evals/run_retrieval_eval.py"),
        Path("scripts/benchmark_latency.py"),
        Path("scripts/seed_doc_store.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "from evals.artifacts import" in source
        assert "def write_jsonl(" not in source
    assert "rows = redact_artifact_rows(rows)" in Path("evals/run_eval.py").read_text(encoding="utf-8")
    assert "rows = redact_artifact_rows(rows)" in Path("evals/run_retrieval_eval.py").read_text(encoding="utf-8")
    assert "write_jsonl(args.output, results)" in Path("scripts/benchmark_latency.py").read_text(encoding="utf-8")
    assert "safe_row = redact_artifact_rows([row])[0]" in Path("scripts/seed_doc_store.py").read_text(
        encoding="utf-8"
    )
