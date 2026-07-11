import re
from pathlib import Path

from tech_doc_agent.app.api.sse.contract import SSE_EVENT_NAMES


FRONTEND_CONTRACT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "sseContract.ts"


def test_frontend_and_backend_sse_event_names_stay_in_sync():
    source = FRONTEND_CONTRACT.read_text(encoding="utf-8")
    frontend_events = set(re.findall(r'^\s+"([a-z_]+)",?$', source, flags=re.MULTILINE))

    assert frontend_events == SSE_EVENT_NAMES
