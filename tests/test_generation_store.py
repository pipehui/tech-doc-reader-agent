from __future__ import annotations

import json

import pytest

from tech_doc_agent.app.infrastructure.persistence import generations
from tech_doc_agent.app.infrastructure.persistence.generations import GenerationStore


def test_unpublished_generation_draft_is_cleaned(tmp_path):
    store = GenerationStore(tmp_path / "state")

    with pytest.raises(RuntimeError):
        with store.draft() as draft:
            generation_path = draft.path
            (generation_path / "partial.json").write_text("{}", encoding="utf-8")
            raise RuntimeError("injected write failure")

    assert not generation_path.exists()
    assert not store.manifest_path.exists()


def test_published_generation_is_retained_and_current(tmp_path):
    store = GenerationStore(tmp_path / "state")

    with store.draft() as draft:
        generation_path = draft.path
        generation = draft.generation
        (draft.path / "state.json").write_text("{}", encoding="utf-8")
        draft.publish({"generation": generation, "schema_version": 1})

    assert generation_path.is_dir()
    manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    assert manifest["generation"] == generation

    inventory = store.inventory()
    assert inventory.manifest_exists is True
    assert inventory.current_generation == generation
    assert inventory.current_generation_present is True
    assert inventory.generation_ids == (generation,)
    assert inventory.non_current_generation_ids == ()
    assert inventory.unknown_entries == ()


def test_publication_started_failure_preserves_possible_current_generation(
    tmp_path,
    monkeypatch,
):
    store = GenerationStore(tmp_path / "state")

    def fail_manifest_write(path, value):
        raise OSError("injected manifest failure")

    monkeypatch.setattr(generations, "write_json_atomic", fail_manifest_write)

    with pytest.raises(OSError):
        with store.draft() as draft:
            generation_path = draft.path
            draft.publish({"generation": draft.generation})

    # Once atomic publication starts, cleanup cannot know whether replace won.
    assert generation_path.is_dir()
    assert not store.manifest_path.exists()


def test_generation_path_rejects_traversal(tmp_path):
    store = GenerationStore(tmp_path / "state")

    with pytest.raises(ValueError):
        store.generation_path("../outside")


def test_inventory_classifies_non_current_missing_and_unknown_entries(tmp_path):
    store = GenerationStore(tmp_path / "state")
    current = "a" * 32
    non_current = "b" * 32
    store.generation_path(current).mkdir(parents=True)
    store.generation_path(non_current).mkdir()
    (store.generations_dir / "unexpected").mkdir()
    (store.generations_dir / "partial.tmp").write_text("partial", encoding="utf-8")
    store.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    store.manifest_path.write_text(
        json.dumps({"generation": current, "schema_version": 1}),
        encoding="utf-8",
    )

    inventory = store.inventory()

    assert inventory.to_payload() == {
        "manifest_exists": True,
        "current_generation": current,
        "current_generation_present": True,
        "generation_ids": [current, non_current],
        "non_current_generation_ids": [non_current],
        "unknown_entries": ["partial.tmp", "unexpected"],
    }

    store.generation_path(current).rmdir()
    missing = store.inventory()
    assert missing.current_generation == current
    assert missing.current_generation_present is False
    assert missing.non_current_generation_ids == (non_current,)


def test_inventory_rejects_invalid_current_manifest(tmp_path):
    store = GenerationStore(tmp_path / "state")
    store.manifest_path.parent.mkdir(parents=True)
    store.manifest_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="manifest is invalid"):
        store.inventory()
