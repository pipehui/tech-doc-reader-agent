from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.revisions import is_full_git_commit_sha

from .prompt_registry import (
    ASSISTANT_ROLES,
    AssistantRole,
    PromptRegistry,
    build_prompt_registry,
)


@dataclass(frozen=True, slots=True)
class ModelRouteIdentity:
    provider_id: str
    primary_model_id: str
    backup_model_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("provider_id", "primary_model_id"):
            value = getattr(self, field_name)
            if not value or value != value.strip():
                raise ValueError(f"{field_name} must be a non-empty trimmed string")
        if self.backup_model_id is not None and (
            not self.backup_model_id
            or self.backup_model_id != self.backup_model_id.strip()
        ):
            raise ValueError(
                "backup_model_id must be None or a non-empty trimmed string"
            )


def build_model_route_identity(settings: Settings) -> ModelRouteIdentity:
    return ModelRouteIdentity(
        provider_id=settings.MODEL_PROVIDER_ID,
        primary_model_id=settings.PRIMARY_MODEL or "gpt-4o-mini",
        backup_model_id=(
            settings.BACKUP_MODEL
            if settings.BACKUP_MODEL and settings.BACKUP_API_KEY
            else None
        ),
    )


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


DeploymentIdentityStatus = Literal["configured", "unavailable"]


@dataclass(frozen=True, slots=True)
class RuntimeDeploymentIdentity:
    status: DeploymentIdentityStatus
    commit_sha: str | None = None

    def __post_init__(self) -> None:
        if self.status == "configured":
            if not is_full_git_commit_sha(self.commit_sha):
                raise ValueError(
                    "Configured runtime deployment identity requires a full "
                    "lowercase Git commit SHA"
                )
        elif self.status == "unavailable":
            if self.commit_sha is not None:
                raise ValueError(
                    "Unavailable runtime deployment identity cannot include a commit SHA"
                )
        else:
            raise ValueError("Runtime deployment identity status is invalid")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status}
        if self.commit_sha is not None:
            payload["commit_sha"] = self.commit_sha
        return payload


def build_runtime_deployment_identity(settings: Settings) -> RuntimeDeploymentIdentity:
    commit_sha = settings.DEPLOYMENT_COMMIT_SHA or settings.IMAGE_COMMIT_SHA
    if commit_sha:
        return RuntimeDeploymentIdentity(
            status="configured",
            commit_sha=commit_sha,
        )
    return RuntimeDeploymentIdentity(status="unavailable")


@dataclass(frozen=True, slots=True)
class RuntimeExecutionIdentity:
    assistants: tuple[AssistantExecutionIdentity, ...]
    deployment: RuntimeDeploymentIdentity
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("RuntimeExecutionIdentity only supports schema_version 2")
        roles = tuple(identity.role for identity in self.assistants)
        if roles != ASSISTANT_ROLES:
            raise ValueError(
                "RuntimeExecutionIdentity assistants must follow the complete "
                "supported role order"
            )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self._fingerprint_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        return {
            **self._fingerprint_payload(),
            "fingerprint": self.fingerprint,
        }

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "deployment": self.deployment.to_payload(),
            "assistants": [
                identity.to_metadata()
                for identity in self.assistants
            ],
        }


def build_runtime_execution_identity(
    settings: Settings,
    prompts: PromptRegistry | None = None,
) -> RuntimeExecutionIdentity:
    route = build_model_route_identity(settings)
    prompt_registry = prompts or build_prompt_registry()
    return RuntimeExecutionIdentity(
        deployment=build_runtime_deployment_identity(settings),
        assistants=tuple(
            AssistantExecutionIdentity(
                role=role,
                prompt_id=artifact.prompt_id,
                prompt_sha256=artifact.sha256,
                model_provider_id=route.provider_id,
                primary_model_id=route.primary_model_id,
                backup_model_id=route.backup_model_id,
            )
            for role in ASSISTANT_ROLES
            for artifact in (prompt_registry.require(role),)
        )
    )


__all__ = [
    "AssistantExecutionIdentity",
    "RuntimeDeploymentIdentity",
    "ModelRouteIdentity",
    "RuntimeExecutionIdentity",
    "build_model_route_identity",
    "build_runtime_deployment_identity",
    "build_runtime_execution_identity",
]
