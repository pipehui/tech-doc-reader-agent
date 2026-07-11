import re
from pathlib import Path


FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"
STORE_SOURCE = (FRONTEND_SRC / "store.ts").read_text(encoding="utf-8")
STORE_DIR = FRONTEND_SRC / "store"
SESSION_SLICE_SOURCE = (STORE_DIR / "sessionSlice.ts").read_text(encoding="utf-8")
TRANSCRIPT_SLICE_SOURCE = (STORE_DIR / "transcriptSlice.ts").read_text(
    encoding="utf-8"
)
UI_SLICE_SOURCE = (STORE_DIR / "uiSlice.ts").read_text(encoding="utf-8")


def test_zustand_store_uses_storage_port_instead_of_browser_global():
    assert "localStorage" not in STORE_SOURCE
    assert "resolveBrowserStorage" in STORE_SOURCE
    assert "readStorage" not in STORE_SOURCE
    assert "writeStorage" not in STORE_SOURCE
    assert "sessionRepository.loadContext(" in STORE_SOURCE
    assert "sessionRepository.loadSessions(" in SESSION_SLICE_SOURCE
    assert "preferenceRepository.loadTheme(" in UI_SLICE_SOURCE


def test_zustand_store_delegates_transcript_persistence_to_repository():
    assert "TRANSCRIPT_VERSION" not in STORE_SOURCE
    assert "transcriptStorageKey" not in STORE_SOURCE
    assert "transcriptRepository.load(" in TRANSCRIPT_SLICE_SOURCE
    assert "transcriptRepository.save(" in TRANSCRIPT_SLICE_SOURCE
    assert "transcriptRepository.delete(" in SESSION_SLICE_SOURCE


def test_zustand_store_facade_only_composes_named_slices():
    slice_creators = (
        "createSessionSlice",
        "createTranscriptSlice",
        "createTraceSlice",
        "createLearningSlice",
        "createUiSlice",
    )
    for creator in slice_creators:
        assert creator in STORE_SOURCE

    assert len(STORE_SOURCE.splitlines()) < 120
    assert "addUserMessage(content)" not in STORE_SOURCE
    assert "recordEvent(event)" not in STORE_SOURCE
    assert "resetForContext(sessionId" not in STORE_SOURCE


def test_zustand_slices_do_not_import_store_singleton_or_each_other():
    slice_paths = sorted(STORE_DIR.glob("*Slice.ts"))
    assert [path.name for path in slice_paths] == [
        "learningSlice.ts",
        "sessionSlice.ts",
        "traceSlice.ts",
        "transcriptSlice.ts",
        "uiSlice.ts",
    ]

    for path in slice_paths:
        source = path.read_text(encoding="utf-8")
        assert "useAppStore" not in source
        assert "localStorage" not in source
        for other_path in slice_paths:
            if other_path != path:
                assert f'from "./{other_path.stem}"' not in source


def test_zustand_slice_action_chaining_stays_on_explicit_allowlist():
    expected_calls = {
        "learningSlice.ts": set(),
        "sessionSlice.ts": {"rememberSession", "resetForContext"},
        "traceSlice.ts": {"persistTranscript"},
        "transcriptSlice.ts": {"addToolCall", "persistTranscript"},
        "uiSlice.ts": {"addSystemMessage"},
    }

    for filename, expected in expected_calls.items():
        source = (STORE_DIR / filename).read_text(encoding="utf-8")
        calls = set(re.findall(r"get\(\)\.([A-Za-z]+)\(", source))
        assert calls == expected
