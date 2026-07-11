from pathlib import Path


FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"
STORE_SOURCE = (FRONTEND_SRC / "store.ts").read_text(encoding="utf-8")


def test_zustand_store_uses_storage_port_instead_of_browser_global():
    assert "localStorage" not in STORE_SOURCE
    assert "resolveBrowserStorage" in STORE_SOURCE
    assert "readStorage" in STORE_SOURCE
    assert "writeStorage" in STORE_SOURCE


def test_zustand_store_delegates_transcript_persistence_to_repository():
    assert "TRANSCRIPT_VERSION" not in STORE_SOURCE
    assert "transcriptStorageKey" not in STORE_SOURCE
    assert "transcriptRepository.load(" in STORE_SOURCE
    assert "transcriptRepository.save(" in STORE_SOURCE
    assert "transcriptRepository.delete(" in STORE_SOURCE
