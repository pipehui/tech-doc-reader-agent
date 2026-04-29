from pathlib import Path

from evals.run_retrieval_eval import (
    load_cases,
    render_markdown_report,
    run_case,
    score_case,
    summarize_results,
)


class FakeRetriever:
    def search(self, query: str, top_k: int, mode: str = "hybrid", filters: dict | None = None):
        if filters and filters.get("category") == "fastapi":
            return [
                {
                    "title": "FastAPI 依赖注入",
                    "content": "FastAPI 通过 Depends 实现依赖注入。",
                    "source": "seed",
                    "match_type": "bm25",
                    "score": 0.01,
                }
            ][:top_k]
        return [
            {
                "title": "LangGraph StateGraph",
                "content": "StateGraph 是状态驱动工作流，支持条件分支。",
                "source": "seed",
                "match_type": "bm25+semantic",
                "score": 0.03,
            },
            {
                "title": "FastAPI 依赖注入",
                "content": "FastAPI 通过 Depends 实现依赖注入。",
                "source": "seed",
                "match_type": "bm25",
                "score": 0.01,
            },
        ][:top_k]


def test_retrieval_cases_are_valid():
    cases = load_cases(Path("evals/retrieval_cases.json"))
    full_cases = load_cases(Path("evals/retrieval_cases_full.json"))
    filter_cases = load_cases(Path("evals/retrieval_filter_cases.json"))

    assert len(cases) >= 3
    assert {case["category"] for case in cases}
    assert len(full_cases) >= 50
    assert {case["query_type"] for case in full_cases}
    assert filter_cases
    assert all(case.get("filters") for case in filter_cases)


def test_score_case_computes_recall_mrr_and_keyword_coverage():
    case = {
        "id": "case_1",
        "category": "langgraph",
        "query": "StateGraph",
        "expected_titles": ["LangGraph StateGraph"],
        "expected_keywords": ["StateGraph", "条件分支", "missing"],
    }
    results = FakeRetriever().search("StateGraph", top_k=2)

    scores = score_case(case, results, top_k=2)

    assert scores["recall_at_k"] == 1.0
    assert scores["hit_at_1"] == 1.0
    assert scores["mrr"] == 1.0
    assert scores["keyword_coverage"] == 2 / 3


def test_score_case_treats_more_specific_titles_as_relevant():
    case = {
        "id": "case_1",
        "category": "langgraph",
        "query": "StateGraph",
        "expected_titles": ["LangGraph StateGraph"],
    }
    results = [
        {
            "title": "LangGraph StateGraph 详细解析",
            "content": "StateGraph details",
        }
    ]

    scores = score_case(case, results, top_k=1)

    assert scores["recall_at_k"] == 1.0
    assert scores["hit_at_1"] == 1.0
    assert scores["mrr"] == 1.0


def test_run_case_records_retrieved_titles_and_match_types():
    case = {
        "id": "case_1",
        "category": "langgraph",
        "query": "StateGraph",
        "expected_titles": ["LangGraph StateGraph"],
        "expected_keywords": ["状态驱动"],
    }

    row = run_case(case, FakeRetriever(), default_top_k=2)

    assert row["status"] == "done"
    assert row["mode"] == "hybrid"
    assert row["filters"] == {}
    assert row["retrieved_titles"] == ["LangGraph StateGraph", "FastAPI 依赖注入"]
    assert row["match_types"] == {"bm25": 2, "semantic": 1}
    assert row["scores"]["recall_at_k"] == 1.0


def test_run_case_passes_filters_to_retriever():
    case = {
        "id": "case_1",
        "category": "fastapi",
        "query": "Depends",
        "filters": {"category": "fastapi"},
        "expected_titles": ["FastAPI 依赖注入"],
    }

    row = run_case(case, FakeRetriever(), default_top_k=2)

    assert row["status"] == "done"
    assert row["filters"] == {"category": "fastapi"}
    assert row["retrieved_titles"] == ["FastAPI 依赖注入"]
    assert row["scores"]["hit_at_1"] == 1.0


def test_render_retrieval_markdown_report_contains_summary_and_cases():
    rows = [
        {
            "id": "case_1",
            "mode": "bm25",
            "filters": {"category": "langgraph_core"},
            "category": "langgraph",
            "query_type": "title",
            "query": "StateGraph",
            "top_k": 2,
            "expected_titles": ["LangGraph StateGraph"],
            "retrieved_titles": ["LangGraph StateGraph"],
            "status": "done",
            "e2e_s": 0.01,
            "scores": {"recall_at_k": 1.0, "hit_at_1": 1.0, "mrr": 1.0, "keyword_coverage": 0.5},
        },
        {
            "id": "case_2",
            "mode": "bm25",
            "filters": {},
            "category": "fastapi",
            "query_type": "conceptual",
            "query": "Depends",
            "top_k": 2,
            "expected_titles": ["FastAPI 依赖注入"],
            "retrieved_titles": ["LangGraph StateGraph"],
            "status": "done",
            "e2e_s": 0.02,
            "scores": {"recall_at_k": 0.0, "hit_at_1": 0.0, "mrr": 0.0, "keyword_coverage": 0.0},
        },
    ]

    report = render_markdown_report(rows)
    summary = summarize_results(rows)

    assert "# Retrieval Eval Report" in report
    assert "Mode: `bm25`" in report
    assert "Recall@K avg" in report
    assert "Hit@1 avg" in report
    assert "By Query Type" in report
    assert "case_1" in report
    assert "filters=" in report
    assert summary["total"] == 2
    assert summary["done"] == 2
    assert summary["recall_at_k_avg"] == 0.5
    assert summary["hit_at_1_avg"] == 0.5
    assert summary["mrr_avg"] == 0.5
