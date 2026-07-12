import copy
from dataclasses import dataclass
from typing import Any

import faiss
import numpy as np
import pytest

from evals.retrieval_corpus import (
    build_retrieval_corpus_identity,
    validate_retrieval_corpus_identity,
)


@dataclass
class FakeCorpusStore:
    documents: list[dict[str, Any]]
    chunk_metadata: list[dict[str, Any]]
    index: Any | None = None
    chunk_size: int = 300
    chunk_overlap: int = 20


def _store(*, title: str = "Document A", vector: tuple[float, float] | None = None):
    documents = [
        {
            "id": 1,
            "title": title,
            "content": "Stable corpus content",
            "source": "test",
            "metadata": {"category": "example", "tags": ["a"]},
        }
    ]
    chunks = [
        {
            "doc_id": 1,
            "title": title,
            "chunk_text": "Stable corpus content",
            "chunk_index": 0,
        }
    ]
    index = None
    if vector is not None:
        index = faiss.IndexFlatL2(2)
        index.add(np.asarray([vector], dtype="float32"))
    return FakeCorpusStore(documents, chunks, index=index)


def test_corpus_identity_contains_only_counts_and_content_digests():
    identity = build_retrieval_corpus_identity(_store()).to_payload()

    assert identity["kind"] == "retrieval_corpus"
    assert identity["schema_version"] == 1
    assert identity["documents"]["count"] == 1
    assert identity["chunks"]["count"] == 1
    assert identity["vector_index"] == {"status": "absent"}
    assert identity["chunking"] == {"size": 300, "overlap": 20}
    assert "Stable corpus content" not in str(identity)
    assert validate_retrieval_corpus_identity(identity) == identity


def test_corpus_identity_changes_with_documents_chunks_or_vector_values():
    base = build_retrieval_corpus_identity(_store(vector=(1.0, 2.0))).to_payload()
    changed_document = build_retrieval_corpus_identity(
        _store(title="Document B", vector=(1.0, 2.0))
    ).to_payload()
    changed_vector = build_retrieval_corpus_identity(
        _store(vector=(2.0, 1.0))
    ).to_payload()

    assert base["fingerprint"] != changed_document["fingerprint"]
    assert base["documents"]["sha256"] != changed_document["documents"]["sha256"]
    assert base["chunks"]["sha256"] != changed_document["chunks"]["sha256"]
    assert base["fingerprint"] != changed_vector["fingerprint"]
    assert base["vector_index"]["sha256"] != changed_vector["vector_index"]["sha256"]


def test_corpus_identity_rejects_index_chunk_count_mismatch():
    store = _store(vector=(1.0, 2.0))
    store.chunk_metadata = []

    with pytest.raises(ValueError, match="vector and chunk counts"):
        build_retrieval_corpus_identity(store)


def test_corpus_identity_validation_rejects_tampering():
    identity = build_retrieval_corpus_identity(_store()).to_payload()
    tampered = copy.deepcopy(identity)
    tampered["documents"]["count"] = 2

    with pytest.raises(ValueError, match="fingerprint"):
        validate_retrieval_corpus_identity(tampered)


def test_corpus_identity_rejects_non_json_metadata():
    store = _store()
    store.documents[0]["metadata"] = {"bad": object()}

    with pytest.raises(ValueError, match="JSON serializable"):
        build_retrieval_corpus_identity(store)
