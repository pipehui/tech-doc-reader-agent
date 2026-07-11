import re
from pathlib import Path

from tech_doc_agent.app.api.sse.contract import SSE_EVENT_NAMES, TOOL_RESULT_STATUSES


FRONTEND_CONTRACT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "sseContract.ts"
FRONTEND_STREAMING_DIR = FRONTEND_CONTRACT.parent / "streaming"
FRONTEND_TRANSPORT = FRONTEND_CONTRACT.parent / "useChatStream.ts"
FRONTEND_STREAM_ORCHESTRATOR = FRONTEND_STREAMING_DIR / "chatStream.ts"
FRONTEND_INSPECTOR_MODEL = (
    FRONTEND_CONTRACT.parent / "features" / "inspector" / "inspectorModel.ts"
)


def test_frontend_and_backend_sse_event_names_stay_in_sync():
    source = FRONTEND_CONTRACT.read_text(encoding="utf-8")
    frontend_events = set(re.findall(r'^\s+"([a-z_]+)",?$', source, flags=re.MULTILINE))

    assert frontend_events == SSE_EVENT_NAMES


def test_frontend_and_backend_tool_result_statuses_stay_in_sync():
    source = FRONTEND_CONTRACT.read_text(encoding="utf-8")
    declaration = re.search(
        r"TOOL_RESULT_STATUSES\s*=\s*\[([^\]]+)\]",
        source,
    )
    assert declaration is not None
    frontend_statuses = set(re.findall(r'"([a-z_]+)"', declaration.group(1)))

    assert frontend_statuses == TOOL_RESULT_STATUSES


def test_frontend_sse_parser_and_reducer_are_store_and_browser_independent():
    source = "\n".join(
        (FRONTEND_STREAMING_DIR / filename).read_text(encoding="utf-8")
        for filename in ("sseEnvelope.ts", "sseReducer.ts")
    )

    assert "useAppStore" not in source
    assert "localStorage" not in source
    assert 'from "../store"' not in source


def test_frontend_stream_transport_delegates_event_semantics():
    composition = FRONTEND_TRANSPORT.read_text(encoding="utf-8")
    source = FRONTEND_STREAM_ORCHESTRATOR.read_text(encoding="utf-8")

    assert "parseSseMessage" in source
    assert "reduceSseMessage" in source
    assert "dispatchStreamActions" in source
    assert "useAppStore" not in source
    assert "applySseEvent" not in source
    assert ".recordEvent(" not in source
    assert ".updateStreamingMessage(" not in source
    assert ".addToolCall(" not in source
    assert ".updateToolResult(" not in source

    assert "createChatStream" in composition
    assert "fetchEventSource" in composition
    assert "refreshSessionContext" in composition
    assert "parseSseMessage" not in composition
    assert len(composition.splitlines()) < 50


def test_frontend_tool_errors_use_protocol_status_not_content_heuristics():
    reducer = (FRONTEND_STREAMING_DIR / "sseReducer.ts").read_text(
        encoding="utf-8"
    )
    inspector = FRONTEND_INSPECTOR_MODEL.read_text(encoding="utf-8")

    assert "inferToolStatus" not in reducer
    assert 'data.status === "error"' in reducer
    assert 'event.data.status === "error"' in inspector
    assert "error|exception|traceback" not in reducer
    assert "error|exception|traceback" not in inspector
