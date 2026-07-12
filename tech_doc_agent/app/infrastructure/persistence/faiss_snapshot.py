from __future__ import annotations

import os
import re
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import faiss

from tech_doc_agent.app.core.errors import ApplicationError, ValidationError, classify_error
from tech_doc_agent.app.infrastructure.persistence.atomic_json import read_json, write_json_atomic


SNAPSHOT_SCHEMA_VERSION = 1
_GENERATION_PATTERN = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class FaissSnapshot:
    index: Any
    documents: list[dict[str, Any]]
    chunk_metadata: list[dict[str, Any]]
    generation: str | None


class FaissSnapshotRepository:
    """Publish and load internally consistent FAISS snapshot generations."""

    def __init__(self, store_dir: Path) -> None:
        self.store_dir = store_dir
        self.generations_dir = store_dir / "generations"
        self.manifest_path = store_dir / "current.json"
        self.legacy_index_path = store_dir / "index.faiss"
        self.legacy_documents_path = store_dir / "documents.json"
        self.legacy_metadata_path = store_dir / "chunk_metadata.json"

    def save(
        self,
        index: Any,
        documents: list[dict[str, Any]],
        chunk_metadata: list[dict[str, Any]],
    ) -> str:
        generation = uuid4().hex
        generation_dir = self.generations_dir / generation
        generation_created = False
        published = False
        try:
            manifest = self._build_manifest(
                generation,
                index,
                documents,
                chunk_metadata,
            )
            candidate = FaissSnapshot(index, documents, chunk_metadata, generation)
            self._validate_snapshot(candidate, manifest)
            generation_dir.mkdir(parents=True, exist_ok=False)
            generation_created = True
            self._write_index(index, generation_dir / "index.faiss")
            self._write_documents(documents, generation_dir / "documents.json")
            self._write_chunk_metadata(
                chunk_metadata,
                generation_dir / "chunk_metadata.json",
            )

            # Validate the exact bytes that will become current, not only the in-memory state.
            self._load_generation(manifest)
            self._publish_manifest(manifest)
            published = True
            return generation
        except ApplicationError:
            raise
        except Exception as exc:
            raise classify_error(exc, dependency="file_repository") from exc
        finally:
            if generation_created and not published:
                # A failed generation is unreachable because current.json was not switched.
                with suppress(OSError):
                    shutil.rmtree(generation_dir)

    def load(self) -> FaissSnapshot | None:
        try:
            if self.manifest_path.exists():
                manifest = self._read_manifest()
                return self._load_generation(manifest)

            legacy_paths = (
                self.legacy_index_path,
                self.legacy_documents_path,
                self.legacy_metadata_path,
            )
            existing_legacy_paths = [path for path in legacy_paths if path.exists()]
            if not existing_legacy_paths:
                return None
            if len(existing_legacy_paths) != len(legacy_paths):
                raise _corrupt_snapshot("IncompleteLegacySnapshot")
            return self._load_legacy()
        except ApplicationError:
            raise
        except Exception as exc:
            raise classify_error(exc, dependency="file_repository") from exc

    def _load_generation(self, manifest: dict[str, Any]) -> FaissSnapshot:
        generation = manifest["generation"]
        generation_dir = self.generations_dir / generation
        snapshot = self._load_files(
            generation_dir / "index.faiss",
            generation_dir / "documents.json",
            generation_dir / "chunk_metadata.json",
            generation=generation,
        )
        self._validate_snapshot(snapshot, manifest)
        return snapshot

    def _load_legacy(self) -> FaissSnapshot:
        snapshot = self._load_files(
            self.legacy_index_path,
            self.legacy_documents_path,
            self.legacy_metadata_path,
            generation=None,
        )
        self._validate_snapshot(snapshot, expected_manifest=None)
        return snapshot

    def _load_files(
        self,
        index_path: Path,
        documents_path: Path,
        metadata_path: Path,
        *,
        generation: str | None,
    ) -> FaissSnapshot:
        if not (index_path.is_file() and documents_path.is_file() and metadata_path.is_file()):
            raise _corrupt_snapshot("MissingSnapshotFile")

        index = faiss.read_index(str(index_path))
        documents = _rows(read_json(documents_path), "InvalidDocuments")
        chunk_metadata = _rows(read_json(metadata_path), "InvalidChunkMetadata")
        return FaissSnapshot(index, documents, chunk_metadata, generation)

    def _read_manifest(self) -> dict[str, Any]:
        value = read_json(self.manifest_path)
        if not isinstance(value, dict):
            raise _corrupt_snapshot("InvalidManifest")

        generation = value.get("generation")
        counts = value.get("counts")
        if (
            value.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
            or not isinstance(generation, str)
            or _GENERATION_PATTERN.fullmatch(generation) is None
            or not isinstance(value.get("created_at"), str)
            or not isinstance(counts, dict)
            or not _is_non_negative_int(counts.get("vectors"))
            or not _is_non_negative_int(counts.get("documents"))
            or not _is_non_negative_int(counts.get("chunk_metadata"))
            or not _is_positive_int(value.get("dimension"))
        ):
            raise _corrupt_snapshot("InvalidManifest")

        return value

    def _build_manifest(
        self,
        generation: str,
        index: Any,
        documents: list[dict[str, Any]],
        chunk_metadata: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "generation": generation,
            "created_at": datetime.now(UTC).isoformat(),
            "dimension": int(index.d),
            "counts": {
                "vectors": int(index.ntotal),
                "documents": len(documents),
                "chunk_metadata": len(chunk_metadata),
            },
        }

    def _validate_snapshot(
        self,
        snapshot: FaissSnapshot,
        expected_manifest: dict[str, Any] | None,
    ) -> None:
        try:
            dimension = int(snapshot.index.d)
            vector_count = int(snapshot.index.ntotal)
        except (AttributeError, TypeError, ValueError) as exc:
            raise _corrupt_snapshot(type(exc).__name__) from exc

        if dimension <= 0 or vector_count < 0:
            raise _corrupt_snapshot("InvalidIndexShape")
        if vector_count != len(snapshot.chunk_metadata):
            raise _corrupt_snapshot("IndexChunkCountMismatch")

        document_ids: set[str] = set()
        for document in snapshot.documents:
            document_id = document.get("id")
            key = "" if document_id is None else str(document_id).strip()
            if not key or key in document_ids:
                raise _corrupt_snapshot("InvalidDocumentId")
            document_ids.add(key)

        for chunk in snapshot.chunk_metadata:
            document_id = chunk.get("doc_id")
            key = "" if document_id is None else str(document_id).strip()
            if not key or key not in document_ids:
                raise _corrupt_snapshot("MissingChunkDocument")

        if expected_manifest is None:
            return

        counts = expected_manifest["counts"]
        if (
            expected_manifest["dimension"] != dimension
            or counts["vectors"] != vector_count
            or counts["documents"] != len(snapshot.documents)
            or counts["chunk_metadata"] != len(snapshot.chunk_metadata)
        ):
            raise _corrupt_snapshot("ManifestSnapshotMismatch")

    def _write_index(self, index: Any, path: Path) -> None:
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary_path = Path(file.name)

            faiss.write_index(index, str(temporary_path))
            # Windows requires a writable descriptor for fsync.
            with temporary_path.open("r+b") as file:
                os.fsync(file.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink()

    def _write_documents(self, documents: list[dict[str, Any]], path: Path) -> None:
        write_json_atomic(path, documents)

    def _write_chunk_metadata(
        self,
        chunk_metadata: list[dict[str, Any]],
        path: Path,
    ) -> None:
        write_json_atomic(path, chunk_metadata)

    def _publish_manifest(self, manifest: dict[str, Any]) -> None:
        write_json_atomic(self.manifest_path, manifest)


def _rows(value: Any, cause_type: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise _corrupt_snapshot(cause_type)
    return [dict(row) for row in value]


def _is_non_negative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _is_positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _corrupt_snapshot(cause_type: str) -> ValidationError:
    return ValidationError(
        "The vector store snapshot is invalid.",
        code="vector_store_corrupt",
        retryable=False,
        dependency="file_repository",
        cause_type=cause_type,
    )


__all__ = [
    "FaissSnapshot",
    "FaissSnapshotRepository",
    "SNAPSHOT_SCHEMA_VERSION",
]
