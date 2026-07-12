from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

import faiss

from evals.manifests import fingerprint_payload, validate_subject_identity


RETRIEVAL_CORPUS_SCHEMA_VERSION = 1
RETRIEVAL_CORPUS_KIND = "retrieval_corpus"


class RetrievalCorpusStore(Protocol):
    documents: list[dict[str, Any]]
    chunk_metadata: list[dict[str, Any]]
    index: Any | None
    chunk_size: int
    chunk_overlap: int


@dataclass(frozen=True, slots=True)
class RetrievalCorpusIdentity:
    payload: dict[str, Any]

    @property
    def fingerprint(self) -> str:
        return str(self.payload["fingerprint"])

    def to_payload(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.payload, ensure_ascii=False))


def build_retrieval_corpus_identity(
    store: RetrievalCorpusStore,
) -> RetrievalCorpusIdentity:
    documents = _json_snapshot(store.documents, collection_name="documents")
    chunks = _json_snapshot(store.chunk_metadata, collection_name="chunk_metadata")
    vector_index = _vector_index_identity(store.index, expected_count=len(chunks))
    payload: dict[str, Any] = {
        "schema_version": RETRIEVAL_CORPUS_SCHEMA_VERSION,
        "kind": RETRIEVAL_CORPUS_KIND,
        "documents": {
            "count": len(documents),
            "sha256": fingerprint_payload(documents),
        },
        "chunks": {
            "count": len(chunks),
            "sha256": fingerprint_payload(chunks),
        },
        "vector_index": vector_index,
        "chunking": {
            "size": int(store.chunk_size),
            "overlap": int(store.chunk_overlap),
        },
    }
    return RetrievalCorpusIdentity(
        payload={**payload, "fingerprint": fingerprint_payload(payload)}
    )


def validate_retrieval_corpus_identity(payload: Any) -> dict[str, Any]:
    identity = validate_subject_identity(payload)
    if identity["kind"] != RETRIEVAL_CORPUS_KIND:
        raise ValueError("Subject identity is not a retrieval corpus")
    if identity["schema_version"] != RETRIEVAL_CORPUS_SCHEMA_VERSION:
        raise ValueError("Retrieval corpus identity schema_version is unsupported")

    for collection_name in ("documents", "chunks"):
        collection = identity.get(collection_name)
        if not isinstance(collection, dict):
            raise ValueError(f"Retrieval corpus {collection_name} identity must be an object")
        if not _is_non_negative_int(collection.get("count")):
            raise ValueError(f"Retrieval corpus {collection_name} count must be non-negative")
        if not _is_sha256(collection.get("sha256")):
            raise ValueError(f"Retrieval corpus {collection_name} SHA-256 is invalid")

    vector_index = identity.get("vector_index")
    if not isinstance(vector_index, dict):
        raise ValueError("Retrieval corpus vector_index identity must be an object")
    status = vector_index.get("status")
    if status == "available":
        if not _is_non_negative_int(vector_index.get("count")):
            raise ValueError("Retrieval corpus vector count must be non-negative")
        if not _is_positive_int(vector_index.get("dimension")):
            raise ValueError("Retrieval corpus vector dimension must be positive")
        if not _is_sha256(vector_index.get("sha256")):
            raise ValueError("Retrieval corpus vector index SHA-256 is invalid")
        if vector_index["count"] != identity["chunks"]["count"]:
            raise ValueError("Retrieval corpus vector and chunk counts do not match")
    elif status == "absent":
        if vector_index != {"status": "absent"}:
            raise ValueError("Absent retrieval vector identity contains unexpected fields")
    else:
        raise ValueError("Retrieval corpus vector_index status is invalid")

    chunking = identity.get("chunking")
    if not isinstance(chunking, dict):
        raise ValueError("Retrieval corpus chunking identity must be an object")
    if not _is_positive_int(chunking.get("size")):
        raise ValueError("Retrieval corpus chunk size must be positive")
    if not _is_non_negative_int(chunking.get("overlap")):
        raise ValueError("Retrieval corpus chunk overlap must be non-negative")
    if chunking["overlap"] >= chunking["size"]:
        raise ValueError("Retrieval corpus chunk overlap must be smaller than chunk size")
    return identity


def _json_snapshot(
    rows: list[dict[str, Any]],
    *,
    collection_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Retrieval corpus {collection_name} must be a list of objects")
    try:
        return json.loads(
            json.dumps(
                rows,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Retrieval corpus {collection_name} must be JSON serializable"
        ) from exc


def _vector_index_identity(index: Any | None, *, expected_count: int) -> dict[str, Any]:
    if index is None:
        return {"status": "absent"}
    try:
        count = int(index.ntotal)
        dimension = int(index.d)
        serialized = faiss.serialize_index(index)
        digest = hashlib.sha256(serialized.tobytes()).hexdigest()
    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError("Retrieval corpus vector index cannot be fingerprinted") from exc
    if count != expected_count:
        raise ValueError("Retrieval corpus vector and chunk counts do not match")
    if dimension <= 0:
        raise ValueError("Retrieval corpus vector dimension must be positive")
    return {
        "status": "available",
        "count": count,
        "dimension": dimension,
        "sha256": digest,
    }


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_int(value: Any) -> bool:
    return _is_non_negative_int(value) and value > 0


__all__ = [
    "RetrievalCorpusIdentity",
    "build_retrieval_corpus_identity",
    "validate_retrieval_corpus_identity",
]
