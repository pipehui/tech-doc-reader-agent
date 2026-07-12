from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .prompt_registry import AssistantRole


@dataclass(frozen=True, slots=True)
class AssistantExecutionIdentity:
    role: AssistantRole
    prompt_id: str
    prompt_sha256: str
    model_provider_id: str
    primary_model_id: str | None = None
    backup_model_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("prompt_id", "model_provider_id"):
            value = getattr(self, field_name)
            if not value or value != value.strip():
                raise ValueError(f"{field_name} must be a non-empty trimmed string")
        if len(self.prompt_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.prompt_sha256
        ):
            raise ValueError("prompt_sha256 must be a lowercase SHA-256 digest")
        for field_name in ("primary_model_id", "backup_model_id"):
            value = getattr(self, field_name)
            if value is not None and (not value or value != value.strip()):
                raise ValueError(
                    f"{field_name} must be None or a non-empty trimmed string"
                )

    def to_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "assistant_role": self.role,
            "prompt_id": self.prompt_id,
            "prompt_sha256": self.prompt_sha256,
            "model_provider_id": self.model_provider_id,
        }
        if self.primary_model_id is not None:
            metadata["primary_model_id"] = self.primary_model_id
        if self.backup_model_id is not None:
            metadata["backup_model_id"] = self.backup_model_id
        return metadata


__all__ = ["AssistantExecutionIdentity"]
