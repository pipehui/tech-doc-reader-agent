from pathlib import Path

from scripts.benchmark_latency import approve_url_for, load_eval_queries, load_queries


def test_load_eval_queries_uses_enabled_cases_by_default():
    queries = load_eval_queries(Path("evals/cases.json"))

    assert len(queries) == 11
    assert all(query["query"] for query in queries)
    assert all(query["expected_plan"] for query in queries)


def test_load_queries_keeps_legacy_query_file_format(tmp_path):
    query_file = tmp_path / "queries.txt"
    query_file.write_text("# comment\n你好 ||| direct\n解释 StateGraph ||| [parser,explanation]\n", encoding="utf-8")

    queries = load_queries(query_file)

    assert queries == [
        {"query": "你好", "expected_plan": "direct"},
        {"query": "解释 StateGraph", "expected_plan": "[parser,explanation]"},
    ]


def test_benchmark_approve_url_defaults_from_chat_endpoint():
    assert approve_url_for("http://127.0.0.1:8000/chat", None) == "http://127.0.0.1:8000/chat/approve"
    assert approve_url_for("http://127.0.0.1:8000/api", None) == "http://127.0.0.1:8000/api/chat/approve"
    assert approve_url_for("http://127.0.0.1:8000/chat", "http://x/approve") == "http://x/approve"
