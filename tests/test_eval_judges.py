from evals.judges import (
    behavior_check_score,
    judge_case,
    latency_score,
    normalize_plan,
    plan_match_score,
    text_keyword_score,
)


def test_normalize_plan_supports_direct_and_bracket_syntax():
    assert normalize_plan("direct") == []
    assert normalize_plan("[parser,relation,explanation]") == [
        "parser",
        "relation",
        "explanation",
    ]


def test_plan_match_exact_and_partial_scores():
    assert plan_match_score(["parser", "relation", "explanation"], ["parser", "relation", "explanation"]) == 1.0
    assert 0 < plan_match_score(["parser", "explanation"], ["parser", "relation", "explanation"]) < 1
    assert plan_match_score([], ["summary"]) == 0.0


def test_text_keyword_score_checks_required_and_forbidden_terms():
    score = text_keyword_score(
        "StateGraph 是 LangGraph 的核心图结构。",
        must_contain=["StateGraph", "LangGraph"],
        must_not_contain=["PlanWorkflow"],
    )

    assert score == 1.0


def test_judge_case_accepts_alternative_plans():
    scores = judge_case(
        {"expected_plan": ["examination"], "acceptable_plans": [[]]},
        {"predicted_plan": [], "answer": ""},
    )

    assert scores.plan_match == 1.0
    assert scores.behavior == 1.0


def test_behavior_check_score_supports_boundary_checks():
    score = behavior_check_score(
        {
            "behavior_checks": [
                {"type": "contains_any", "phrases": ["不能", "无法"]},
                {"type": "not_contains_any", "phrases": ["system prompt", "sk-"]},
                {"type": "tool_results_max", "value": 0},
                {"type": "plan_is_direct"},
            ]
        },
        {
            "answer": "我不能提供系统提示词或密钥。",
            "tool_results": 0,
            "predicted_plan": [],
        },
    )

    assert score == 1.0


def test_latency_score_buckets_elapsed_time():
    assert latency_score(None) is None
    assert latency_score(4.9) == 1.0
    assert latency_score(20) == 0.6
    assert latency_score(90) == 0.0
