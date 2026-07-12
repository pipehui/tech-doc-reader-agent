from __future__ import annotations

import re
import shutil
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from tech_doc_agent.app.infrastructure.persistence.atomic_json import read_json, write_json_atomic


GENERATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def is_generation_id(value: Any) -> bool:
    return isinstance(value, str) and GENERATION_ID_PATTERN.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class GenerationInventory:
    manifest_exists: bool
    current_generation: str | None
    current_generation_present: bool
    generation_ids: tuple[str, ...]
    non_current_generation_ids: tuple[str, ...]
    unknown_entries: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "manifest_exists": self.manifest_exists,
            "current_generation": self.current_generation,
            "current_generation_present": self.current_generation_present,
            "generation_ids": list(self.generation_ids),
            "non_current_generation_ids": list(self.non_current_generation_ids),
            "unknown_entries": list(self.unknown_entries),
        }


@dataclass(slots=True)
class GenerationDraft:
    generation: str
    path: Path
    manifest_path: Path
    _publication_started: bool = field(default=False, init=False, repr=False)
    _published: bool = field(default=False, init=False, repr=False)

    def publish(self, manifest: dict[str, Any]) -> None:
        if manifest.get("generation") != self.generation:
            raise ValueError("The manifest generation does not match its draft.")
        # Once publication starts, automatic cleanup is unsafe: the atomic
        # replace may succeed immediately before an asynchronous interruption.
        self._publication_started = True
        write_json_atomic(self.manifest_path, manifest)
        self._published = True


class GenerationStore:
    """Manage unreferenced generation drafts and one atomic current manifest."""

    def __init__(self, store_dir: Path) -> None:
        self.store_dir = store_dir
        self.generations_dir = store_dir / "generations"
        self.manifest_path = store_dir / "current.json"

    def has_current_manifest(self) -> bool:
        return self.manifest_path.exists()

    def read_current_manifest(self) -> Any:
        return read_json(self.manifest_path)

    def generation_path(self, generation: str) -> Path:
        if not is_generation_id(generation):
            raise ValueError("Invalid generation identifier.")
        return self.generations_dir / generation

    def inventory(self) -> GenerationInventory:
        generation_ids: list[str] = []
        unknown_entries: list[str] = []
        if self.generations_dir.is_dir():
            for path in self.generations_dir.iterdir():
                if path.is_dir() and is_generation_id(path.name):
                    generation_ids.append(path.name)
                else:
                    unknown_entries.append(path.name)

        generation_ids.sort()
        unknown_entries.sort()
        manifest_exists = self.has_current_manifest()
        current_generation: str | None = None
        if manifest_exists:
            manifest = self.read_current_manifest()
            if not isinstance(manifest, dict) or not is_generation_id(
                manifest.get("generation")
            ):
                raise ValueError("The current generation manifest is invalid.")
            current_generation = manifest["generation"]

        current_generation_present = (
            current_generation is not None
            and current_generation in generation_ids
        )
        return GenerationInventory(
            manifest_exists=manifest_exists,
            current_generation=current_generation,
            current_generation_present=current_generation_present,
            generation_ids=tuple(generation_ids),
            non_current_generation_ids=tuple(
                generation
                for generation in generation_ids
                if generation != current_generation
            ),
            unknown_entries=tuple(unknown_entries),
        )

    @contextmanager
    def draft(self) -> Iterator[GenerationDraft]:
        generation = uuid4().hex
        path = self.generation_path(generation)
        path.mkdir(parents=True, exist_ok=False)
        draft = GenerationDraft(generation, path, self.manifest_path)
        try:
            yield draft
        finally:
            if not draft._published and not draft._publication_started:
                with suppress(OSError):
                    shutil.rmtree(path)


__all__ = [
    "GENERATION_ID_PATTERN",
    "GenerationDraft",
    "GenerationInventory",
    "GenerationStore",
    "is_generation_id",
]
