from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import faiss
import pytest

from tech_doc_agent.app.core.errors import ApplicationError, ValidationError
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.infrastructure.retrieval.faiss_store import FaissStore


DOCUMENTS = [
    {
        "title": "StateGraph",
        "content": "StateGraph builds state-driven workflows.",
        "source": "test",
    },
    {
        "title": "FastAPI",
        "content": "FastAPI supports dependency injection.",
        "source": "test",
    },
]


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    def generate(content):
        if isinstance(content, str):
            return [float(len(content)), 1.0, 0.0]
        return [[float(index + 1), float(len(item)), 0.0] for index, item in enumerate(content)]

    monkeypatch.setattr(
        "tech_doc_agent.app.infrastructure.retrieval.faiss_store.generate_embedding",
        generate,
    )


def _store(tmp_path: Path) -> FaissStore:
    return FaissStore(
        chunk_size=1_000,
        settings=Settings(DATA_PATH=str(tmp_path)),
    )


def _manifest(store: FaissStore) -> dict[str, Any]:
    return json.loads((store.store_dir / "current.json").read_text(encoding="utf-8"))


def _generation_dir(store: FaissStore) -> Path:
    return store.store_dir / "generations" / _manifest(store)["generation"]


def test_save_publishes_validated_generation_and_loads_it(tmp_path):
    store = _store(tmp_path)
    result = store.build_index(DOCUMENTS)

    assert result == {"added_documents": 2, "added_chunks": 2}
    assert store.save() is True

    manifest = _manifest(store)
    generation_dir = _generation_dir(store)
    assert manifest["schema_version"] == 1
    assert manifest["dimension"] == 3
    assert manifest["counts"] == {
        "vectors": 2,
        "documents": 2,
        "chunk_metadata": 2,
    }
    assert sorted(path.name for path in generation_dir.iterdir()) == [
        "chunk_metadata.json",
        "documents.json",
        "index.faiss",
    ]
    assert not (store.store_dir / "index.faiss").exists()

    reloaded = _store(tmp_path)
    assert reloaded.load() is True
    assert [document["title"] for document in reloaded.documents] == [
        "StateGraph",
        "FastAPI",
    ]
    assert reloaded.index is not None
    assert reloaded.index.ntotal == len(reloaded.chunk_metadata) == 2


@pytest.mark.parametrize(
    "write_step",
    [
        "_write_index",
        "_write_documents",
        "_write_chunk_metadata",
        "_publish_manifest",
    ],
)
def test_failed_snapshot_step_keeps_previous_generation_current(
    tmp_path,
    monkeypatch,
    write_step,
):
    store = _store(tmp_path)
    store.build_index([DOCUMENTS[0]])
    store.save()
    previous_manifest = (store.store_dir / "current.json").read_text(encoding="utf-8")
    previous_generation = _manifest(store)["generation"]

    store.add_documents([DOCUMENTS[1]])

    original_write = getattr(store._snapshot_repository, write_step)

    def fail_write(*args, **kwargs):
        if write_step != "_publish_manifest":
            original_write(*args, **kwargs)
        raise OSError(f"injected failure in {write_step}")

    monkeypatch.setattr(store._snapshot_repository, write_step, fail_write)

    with pytest.raises(ApplicationError) as raised:
        store.save()

    assert raised.value.dependency == "file_repository"
    assert (store.store_dir / "current.json").read_text(encoding="utf-8") == previous_manifest
    assert [path.name for path in (store.store_dir / "generations").iterdir()] == [previous_generation]

    reloaded = _store(tmp_path)
    assert reloaded.load() is True
    assert [document["title"] for document in reloaded.documents] == ["StateGraph"]
    assert reloaded.index is not None
    assert reloaded.index.ntotal == 1


def test_unreferenced_partial_generation_is_ignored_after_process_crash(tmp_path):
    store = _store(tmp_path)
    store.build_index([DOCUMENTS[0]])
    store.save()
    current_generation = _manifest(store)["generation"]

    orphan_dir = store.store_dir / "generations" / uuid4().hex
    orphan_dir.mkdir()
    (orphan_dir / "documents.json").write_text("[]", encoding="utf-8")

    reloaded = _store(tmp_path)
    assert reloaded.load() is True
    assert _manifest(reloaded)["generation"] == current_generation
    assert [document["title"] for document in reloaded.documents] == ["StateGraph"]


def test_build_failure_preserves_active_in_memory_snapshot(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.build_index([DOCUMENTS[0]])
    active_index = store.index
    active_documents = deepcopy(store.documents)
    active_metadata = deepcopy(store.chunk_metadata)

    def fail_embedding(content):
        raise RuntimeError("injected embedding failure")

    monkeypatch.setattr(
        "tech_doc_agent.app.infrastructure.retrieval.faiss_store.generate_embedding",
        fail_embedding,
    )

    with pytest.raises(ApplicationError):
        store.build_index([DOCUMENTS[1]])

    assert store.index is active_index
    assert store.documents == active_documents
    assert store.chunk_metadata == active_metadata


def test_empty_build_is_a_non_destructive_noop(tmp_path):
    store = _store(tmp_path)
    store.build_index([DOCUMENTS[0]])
    active_index = store.index
    active_documents = deepcopy(store.documents)
    active_metadata = deepcopy(store.chunk_metadata)

    assert store.build_index([]) == {
        "added_documents": 0,
        "added_chunks": 0,
    }

    assert store.index is active_index
    assert store.documents == active_documents
    assert store.chunk_metadata == active_metadata


def test_append_failure_preserves_active_in_memory_snapshot(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.build_index([DOCUMENTS[0]])
    active_index = store.index
    active_documents = deepcopy(store.documents)
    active_metadata = deepcopy(store.chunk_metadata)

    def fail_clone(index):
        raise RuntimeError("injected clone failure")

    monkeypatch.setattr(faiss, "clone_index", fail_clone)

    with pytest.raises(ApplicationError):
        store.add_documents([DOCUMENTS[1]])

    assert store.index is active_index
    assert store.documents == active_documents
    assert store.chunk_metadata == active_metadata


def _remove_chunk(generation_dir: Path, manifest: dict[str, Any]) -> None:
    path = generation_dir / "chunk_metadata.json"
    chunks = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(chunks[:-1]), encoding="utf-8")


def _break_document_reference(
    generation_dir: Path,
    manifest: dict[str, Any],
) -> None:
    path = generation_dir / "chunk_metadata.json"
    chunks = json.loads(path.read_text(encoding="utf-8"))
    chunks[0]["doc_id"] = "missing-document"
    path.write_text(json.dumps(chunks), encoding="utf-8")


def _break_manifest_count(
    generation_dir: Path,
    manifest: dict[str, Any],
) -> None:
    manifest["counts"]["documents"] += 1
    (generation_dir.parents[1] / "current.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _remove_snapshot_file(
    generation_dir: Path,
    manifest: dict[str, Any],
) -> None:
    (generation_dir / "documents.json").unlink()


@pytest.mark.parametrize(
    "corrupt",
    [
        _remove_chunk,
        _break_document_reference,
        _break_manifest_count,
        _remove_snapshot_file,
    ],
)
def test_load_rejects_inconsistent_snapshot(
    tmp_path,
    corrupt: Callable[[Path, dict[str, Any]], None],
):
    store = _store(tmp_path)
    store.build_index(DOCUMENTS)
    store.save()
    manifest = _manifest(store)
    corrupt(_generation_dir(store), manifest)

    with pytest.raises(ValidationError) as raised:
        _store(tmp_path).load()

    assert raised.value.code == "vector_store_corrupt"
    assert raised.value.retryable is False
    assert raised.value.dependency == "file_repository"


def test_manifest_generation_cannot_escape_store_directory(tmp_path):
    store = _store(tmp_path)
    store.build_index([DOCUMENTS[0]])
    store.save()
    manifest = _manifest(store)
    manifest["generation"] = "../outside"
    (store.store_dir / "current.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as raised:
        _store(tmp_path).load()

    assert raised.value.code == "vector_store_corrupt"


def test_legacy_three_file_store_loads_and_migrates_on_next_save(tmp_path):
    original = _store(tmp_path)
    original.build_index(DOCUMENTS)
    original.normalize_metadata()
    assert original.index is not None
    original.store_dir.mkdir(parents=True)
    faiss.write_index(original.index, str(original.store_dir / "index.faiss"))
    (original.store_dir / "documents.json").write_text(
        json.dumps(original.documents),
        encoding="utf-8",
    )
    (original.store_dir / "chunk_metadata.json").write_text(
        json.dumps(original.chunk_metadata),
        encoding="utf-8",
    )

    loaded = _store(tmp_path)
    assert loaded.load() is True
    assert not (loaded.store_dir / "current.json").exists()

    assert loaded.save() is True
    assert (loaded.store_dir / "current.json").is_file()
    assert _store(tmp_path).load() is True


def test_incomplete_legacy_store_is_reported_as_corrupt(tmp_path):
    store = _store(tmp_path)
    store.store_dir.mkdir(parents=True)
    (store.store_dir / "documents.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValidationError) as raised:
        store.load()

    assert raised.value.code == "vector_store_corrupt"


def test_metadata_migration_cannot_bypass_snapshot_publish_boundary():
    root = Path(__file__).resolve().parents[1]
    migration_source = (root / "scripts" / "migrate_doc_metadata.py").read_text(encoding="utf-8")
    persistence_init = (root / "tech_doc_agent" / "app" / "infrastructure" / "persistence" / "__init__.py").read_text(
        encoding="utf-8"
    )

    assert "store.save()" in migration_source
    assert "documents_path" not in migration_source
    assert "metadata_path" not in migration_source
    # Reusing AtomicJsonFile must not eagerly import the native FAISS runtime.
    assert "faiss_snapshot" not in persistence_init
