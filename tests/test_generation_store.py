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
