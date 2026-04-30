import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tech_doc_agent.app.services.message_scope import (
    build_scoped_messages,
    build_scoped_state,
    should_route_to_examination,
)


def test_scoped_parser_messages_exclude_primary_tool_results_and_keep_own_tools():
    messages = [
        HumanMessage(content="帮我理解 LangGraph StateGraph"),
        AIMessage(
            content="",
            name="primary",
            tool_calls=[
                {
                    "name": "web_search",
                    "args": {"query": "StateGraph"},
                    "id": "search-1",
                }
            ],
        ),
        ToolMessage(content="external search result", tool_call_id="search-1"),
        AIMessage(
            content="",
            name="primary",
            tool_calls=[
                {
                    "name": "ToDocParserAssistant",
                    "args": {
                        "content": "StateGraph",
                        "request": "解析核心机制",
                    },
                    "id": "handoff-1",
                }
            ],
        ),
        ToolMessage(content="handoff to parser", tool_call_id="handoff-1"),
        AIMessage(
            content="",
            name="parser",
            tool_calls=[
                {
                    "name": "read_docs",
                    "args": {"query": "StateGraph"},
                    "id": "read-1",
                }
            ],
        ),
        ToolMessage(content="local docs result", tool_call_id="read-1"),
    ]
    state = {
        "messages": messages,
        "user_info": "",
        "dialog_state": ["parser"],
        "learning_target": "LangGraph StateGraph",
        "workflow_plan": ["parser", "relation", "explanation"],
        "plan_index": 0,
    }

    scoped_messages = build_scoped_messages(state, "parser")
    scoped_text = "\n".join(str(getattr(message, "content", "")) for message in scoped_messages)

    assert scoped_messages[0].type == "human"
    assert "帮我理解 LangGraph StateGraph" in scoped_messages[0].content
    assert "handoff_request" in scoped_messages[0].content
    assert "解析核心机制" in scoped_messages[0].content
    assert "external search result" not in scoped_text
    assert "handoff to parser" not in scoped_text
    assert scoped_messages[1].name == "parser"
    assert scoped_messages[2].content == "local docs result"


def test_scoped_explanation_messages_include_structured_results_without_raw_text():
    parser_result = {
        "parsed": True,
        "topic": "StateGraph",
        "core_content": "状态驱动图执行。",
        "raw_text": "very long parser markdown",
    }
    relation_result = {
        "parsed": True,
        "target": "StateGraph",
        "recommended_analogies": ["Redux reducer"],
        "raw_text": "very long relation markdown",
    }
    state = {
        "messages": [HumanMessage(content="解释 StateGraph")],
        "user_info": "用户熟悉 FastAPI。",
        "dialog_state": ["explanation"],
        "learning_target": "StateGraph",
        "workflow_plan": ["parser", "relation", "explanation"],
        "plan_index": 2,
        "parser_result": parser_result,
        "relation_result": relation_result,
    }

    scoped_messages = build_scoped_messages(state, "explanation")
    context = json.loads(scoped_messages[0].content.split("\n\n", 1)[1])

    assert context["parser_result"]["topic"] == "StateGraph"
    assert context["relation_result"]["recommended_analogies"] == ["Redux reducer"]
    assert "raw_text" not in context["parser_result"]
    assert "raw_text" not in context["relation_result"]


def test_build_scoped_state_keeps_full_messages_for_primary_and_summary():
    state = {
        "messages": [HumanMessage(content="你好")],
        "user_info": "",
        "dialog_state": [],
        "learning_target": "",
    }

    assert build_scoped_state(state, "primary") is state
    assert build_scoped_state(state, "summary") is state


def test_scoped_examination_messages_include_previous_exam_context():
    state = {
        "messages": [HumanMessage(content="我的答案是：先检索再生成。")],
        "user_info": "",
        "dialog_state": ["examination"],
        "learning_target": "RAG",
        "examination_context": "上一轮题目：解释 RAG 的检索和生成两个阶段。",
    }

    scoped_messages = build_scoped_messages(state, "examination")
    context = json.loads(scoped_messages[0].content.split("\n\n", 1)[1])

    assert context["user_query"] == "我的答案是：先检索再生成。"
    assert context["previous_examination_context"] == "上一轮题目：解释 RAG 的检索和生成两个阶段。"


def test_scoped_examination_messages_include_previous_exam_for_option_sequence():
    state = {
        "messages": [HumanMessage(content="1-B 2-B 3-C 4-B 5-B 6-B 7-B 8-B")],
        "user_info": "",
        "dialog_state": ["examination"],
        "learning_target": "RAG",
        "examination_context": "上一轮题目：RAG 八道选择题。",
    }

    scoped_messages = build_scoped_messages(state, "examination")
    context = json.loads(scoped_messages[0].content.split("\n\n", 1)[1])

    assert context["user_query"] == "1-B 2-B 3-C 4-B 5-B 6-B 7-B 8-B"
    assert context["previous_examination_context"] == "上一轮题目：RAG 八道选择题。"


def test_scoped_examination_messages_include_previous_exam_for_free_text_answer():
    state = {
        "messages": [HumanMessage(content="RAG 会先检索资料，再解释和生成答案。")],
        "user_info": "",
        "dialog_state": ["examination"],
        "learning_target": "RAG",
        "examination_context": "上一轮题目：解释 RAG 的检索和生成两个阶段。",
    }

    scoped_messages = build_scoped_messages(state, "examination")
    context = json.loads(scoped_messages[0].content.split("\n\n", 1)[1])

    assert context["previous_examination_context"] == "上一轮题目：解释 RAG 的检索和生成两个阶段。"


def test_scoped_examination_messages_do_not_include_previous_exam_for_new_quiz_request():
    state = {
        "messages": [HumanMessage(content="再给我出一道关于 checkpoint 的题")],
        "user_info": "",
        "dialog_state": ["examination"],
        "learning_target": "checkpoint",
        "examination_context": "上一轮题目：解释 RAG 的检索和生成两个阶段。",
    }

    scoped_messages = build_scoped_messages(state, "examination")
    context = json.loads(scoped_messages[0].content.split("\n\n", 1)[1])

    assert "previous_examination_context" not in context


def test_should_route_to_examination_only_for_answer_continuation():
    base_state = {
        "user_info": "",
        "dialog_state": [],
        "learning_target": "RAG",
        "examination_context": "上一轮题目：解释 RAG 的检索和生成两个阶段。",
    }

    assert should_route_to_examination(
        {
            **base_state,
            "messages": [HumanMessage(content="我的答案是：先检索再生成。")],
        }
    )
    assert should_route_to_examination(
        {
            **base_state,
            "messages": [HumanMessage(content="再给我出一道关于 checkpoint 的题")],
        }
    )
    assert should_route_to_examination(
        {
            **base_state,
            "messages": [HumanMessage(content="先检索相关文档，再把上下文交给模型生成回答。")],
        }
    )
    assert should_route_to_examination(
        {
            **base_state,
            "messages": [HumanMessage(content="RAG 会先检索资料，再解释和生成答案。")],
        }
    )
    assert not should_route_to_examination(
        {
            **base_state,
            "messages": [HumanMessage(content="帮我总结刚才的学习内容")],
        }
    )
    assert not should_route_to_examination(
        {
            **base_state,
            "messages": [HumanMessage(content="帮我解释一下 RAG 的工作原理")],
        }
    )
