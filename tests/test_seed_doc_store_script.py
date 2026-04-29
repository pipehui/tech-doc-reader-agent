from scripts.seed_doc_store import (
    approve_url_for,
    build_message,
    build_session_id,
    load_topics,
    run_topic,
)


class FakeResponse:
    def __init__(self, lines, status_code=200):
        self.lines = lines
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def iter_lines(self):
        yield from self.lines


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def stream(self, method, url, json, timeout):
        self.requests.append({"method": method, "url": url, "json": json, "timeout": timeout})
        return self.responses.pop(0)


def test_load_topics_supports_line_file_and_inline_topics(tmp_path):
    topics_file = tmp_path / "topics.txt"
    topics_file.write_text("# comment\nLangGraph 核心中的 StateGraph\n\nFastAPI Depends\n", encoding="utf-8")

    topics = load_topics(topics_file, ["RAG 检索增强生成"])

    assert topics == ["LangGraph 核心中的 StateGraph", "FastAPI Depends", "RAG 检索增强生成"]


def test_build_message_and_session_id_are_api_safe():
    message = build_message("LangGraph 核心中的 StateGraph", "请系统解析：{topic}")
    session_id = build_session_id("seed-docs", 1, "LangGraph 核心中的 StateGraph")

    assert message == "请系统解析：LangGraph 核心中的 StateGraph"
    assert session_id.startswith("seed-docs-001-")
    assert len(session_id) <= 128
    assert " " not in session_id


def test_approve_url_defaults_from_chat_endpoint():
    assert approve_url_for("http://127.0.0.1:8000/chat", None) == "http://127.0.0.1:8000/chat/approve"
    assert approve_url_for("http://127.0.0.1:8000/api", None) == "http://127.0.0.1:8000/api/chat/approve"
    assert approve_url_for("http://127.0.0.1:8000/chat", "http://x/approve") == "http://x/approve"


def test_run_topic_auto_approves_save_docs():
    client = FakeClient(
        [
            FakeResponse(
                [
                    "event: tool_call",
                    'data: {"tool": "save_docs", "args": {"title": "T"}, "tool_call_id": "call_1"}',
                    "",
                    "event: interrupt_required",
                    'data: {"pending": true}',
                    "",
                ]
            ),
            FakeResponse(
                [
                    "event: tool_result",
                    'data: {"tool": "save_docs"}',
                    "",
                    "event: done",
                    'data: {"session_id": "s"}',
                    "",
                ]
            ),
        ]
    )

    row = run_topic(
        client,
        api_url="http://test/chat",
        approve_url="http://test/chat/approve",
        topic="Topic",
        session_id="session-1",
        message="message",
        timeout_s=1,
        allowed_approval_tools={"save_docs"},
        max_approval_rounds=2,
    )

    assert row["status"] == "done"
    assert row["approvals"] == 1
    assert row["tool_results"] == 1
    assert client.requests[1]["json"] == {"session_id": "session-1", "approved": True}


def test_run_topic_refuses_unexpected_sensitive_tool():
    client = FakeClient(
        [
            FakeResponse(
                [
                    "event: tool_call",
                    'data: {"tool": "delete_docs", "tool_call_id": "call_1"}',
                    "",
                    "event: interrupt_required",
                    'data: {"pending": true}',
                    "",
                ]
            )
        ]
    )

    row = run_topic(
        client,
        api_url="http://test/chat",
        approve_url="http://test/chat/approve",
        topic="Topic",
        session_id="session-1",
        message="message",
        timeout_s=1,
        allowed_approval_tools={"save_docs"},
        max_approval_rounds=2,
    )

    assert row["status"] == "error"
    assert row["error"] == "refusing to auto-approve tool: delete_docs"
