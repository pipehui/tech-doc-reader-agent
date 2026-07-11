import re
from pathlib import Path

from tech_doc_agent.app.api.sse.contract import SSE_EVENT_NAMES


FRONTEND_CONTRACT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "sseContract.ts"
FRONTEND_STREAMING_DIR = FRONTEND_CONTRACT.parent / "streaming"
FRONTEND_TRANSPORT = FRONTEND_CONTRACT.parent / "useChatStream.ts"


def test_frontend_and_backend_sse_event_names_stay_in_sync():
    source = FRONTEND_CONTRACT.read_text(encoding="utf-8")
    frontend_events = set(re.findall(r'^\s+"([a-z_]+)",?$', source, flags=re.MULTILINE))

    assert frontend_events == SSE_EVENT_NAMES


def test_frontend_sse_parser_and_reducer_are_store_and_browser_independent():
    source = "\n".join(
        (FRONTEND_STREAMING_DIR / filename).read_text(encoding="utf-8")
        for filename in ("sseEnvelope.ts", "sseReducer.ts")
    )

    assert "useAppStore" not in source
    assert "localStorage" not in source
    assert 'from "../store"' not in source


def test_frontend_stream_transport_delegates_event_semantics():
    source = FRONTEND_TRANSPORT.read_text(encoding="utf-8")

    assert "parseSseMessage" in source
    assert "reduceSseMessage" in source
    assert "dispatchStreamActions" in source
    assert "applySseEvent" not in source
    assert ".recordEvent(" not in source
    assert ".updateStreamingMessage(" not in source
    assert ".addToolCall(" not in source
    assert ".updateToolResult(" not in source
