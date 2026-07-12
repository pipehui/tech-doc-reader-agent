from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import PurePosixPath
from string import Formatter
from typing import Any, Literal, cast

from langchain_core.prompts import ChatPromptTemplate


AssistantRole = Literal[
    "primary",
    "parser",
    "relation",
    "explanation",
    "examination",
    "summary",
]
ResourceReader = Callable[[str], str]

PROMPT_PACKAGE = "tech_doc_agent.app.agents.prompts"
PROMPT_MANIFEST_RESOURCE = "manifest.json"
PROMPT_MANIFEST_SCHEMA_VERSION = 1
ASSISTANT_ROLES: tuple[AssistantRole, ...] = (
    "primary",
    "parser",
    "relation",
    "explanation",
    "examination",
    "summary",
)


class PromptRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PromptArtifact:
    role: AssistantRole
    prompt_id: str
    sha256: str
    resources: tuple[str, ...]
    required_placeholders: frozenset[str]
    system_template: str
    template: ChatPromptTemplate


class PromptRegistry:
    def __init__(self, artifacts: Mapping[AssistantRole, PromptArtifact]) -> None:
        self._artifacts = dict(artifacts)
        missing = set(ASSISTANT_ROLES) - set(self._artifacts)
        unexpected = set(self._artifacts) - set(ASSISTANT_ROLES)
        if missing or unexpected:
            raise PromptRegistryError(
                "Prompt registry roles do not match the supported assistant roles: "
                f"missing={sorted(missing)}, unexpected={sorted(unexpected)}."
            )

    def require(self, role: AssistantRole) -> PromptArtifact:
        try:
            return self._artifacts[role]
        except KeyError as exc:
            raise PromptRegistryError(f"Unknown assistant prompt role: {role}") from exc

    def artifacts(self) -> tuple[PromptArtifact, ...]:
        return tuple(self._artifacts[role] for role in ASSISTANT_ROLES)

    @classmethod
    def from_manifest(
        cls,
        manifest: Mapping[str, Any],
        *,
        read_resource: ResourceReader,
    ) -> PromptRegistry:
        schema_version = manifest.get("schema_version")
        if schema_version != PROMPT_MANIFEST_SCHEMA_VERSION:
            raise PromptRegistryError(
                "Unsupported prompt manifest schema_version: "
                f"{schema_version!r}. Expected {PROMPT_MANIFEST_SCHEMA_VERSION}."
            )

        raw_prompts = manifest.get("prompts")
        if not isinstance(raw_prompts, Mapping):
            raise PromptRegistryError("Prompt manifest field 'prompts' must be an object.")

        manifest_roles = set(raw_prompts)
        expected_roles = set(ASSISTANT_ROLES)
        if manifest_roles != expected_roles:
            raise PromptRegistryError(
                "Prompt manifest roles do not match the supported assistant roles: "
                f"missing={sorted(expected_roles - manifest_roles)}, "
                f"unexpected={sorted(manifest_roles - expected_roles)}."
            )

        artifacts: dict[AssistantRole, PromptArtifact] = {}
        prompt_ids: set[str] = set()
        for role in ASSISTANT_ROLES:
            raw_entry = raw_prompts[role]
            if not isinstance(raw_entry, Mapping):
                raise PromptRegistryError(f"Prompt manifest entry '{role}' must be an object.")
            artifact = _load_prompt_artifact(
                role,
                raw_entry,
                read_resource=read_resource,
            )
            if artifact.prompt_id in prompt_ids:
                raise PromptRegistryError(f"Prompt id '{artifact.prompt_id}' is duplicated in the manifest.")
            prompt_ids.add(artifact.prompt_id)
            artifacts[role] = artifact

        return cls(artifacts)


def build_prompt_registry() -> PromptRegistry:
    try:
        manifest_text = _read_package_resource(
            PROMPT_MANIFEST_RESOURCE,
            strip_terminal_newline=False,
        )
    except OSError as exc:
        raise PromptRegistryError("Prompt manifest could not be read.") from exc
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        raise PromptRegistryError("Prompt manifest is not valid JSON.") from exc
    if not isinstance(manifest, Mapping):
        raise PromptRegistryError("Prompt manifest root must be an object.")
    return PromptRegistry.from_manifest(
        manifest,
        read_resource=_read_package_resource,
    )


def _load_prompt_artifact(
    role: AssistantRole,
    raw_entry: Mapping[str, Any],
    *,
    read_resource: ResourceReader,
) -> PromptArtifact:
    prompt_id = _required_string(raw_entry, "id", role=role)
    expected_sha256 = _required_string(raw_entry, "sha256", role=role)
    if len(expected_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_sha256):
        raise PromptRegistryError(f"Prompt '{role}' sha256 must be a lowercase 64-character hex digest.")

    resources = _required_string_list(raw_entry, "resources", role=role)
    if not resources:
        raise PromptRegistryError(f"Prompt '{role}' must declare at least one resource.")
    if len(set(resources)) != len(resources):
        raise PromptRegistryError(f"Prompt '{role}' contains duplicate resource paths.")
    for resource in resources:
        _validate_resource_path(resource, role=role)

    required_placeholders = frozenset(_required_string_list(raw_entry, "required_placeholders", role=role))
    if not required_placeholders:
        raise PromptRegistryError(f"Prompt '{role}' must declare required_placeholders.")

    sections: list[str] = []
    for resource in resources:
        try:
            sections.append(read_resource(resource))
        except OSError as exc:
            raise PromptRegistryError(f"Prompt '{role}' resource '{resource}' could not be read.") from exc
    system_template = "\n\n".join(sections)
    actual_sha256 = hashlib.sha256(system_template.encode("utf-8")).hexdigest()
    if actual_sha256 != expected_sha256:
        raise PromptRegistryError(
            f"Prompt '{role}' content hash mismatch: expected {expected_sha256}, got {actual_sha256}."
        )

    actual_placeholders = _template_placeholders(system_template) | {"messages"}
    if actual_placeholders != required_placeholders:
        raise PromptRegistryError(
            f"Prompt '{role}' placeholders do not match the manifest: "
            f"missing={sorted(required_placeholders - actual_placeholders)}, "
            f"unexpected={sorted(actual_placeholders - required_placeholders)}."
        )

    template = ChatPromptTemplate.from_messages(
        [
            ("system", system_template),
            ("placeholder", "{messages}"),
        ]
    ).partial(time=_current_time)
    return PromptArtifact(
        role=role,
        prompt_id=prompt_id,
        sha256=actual_sha256,
        resources=resources,
        required_placeholders=required_placeholders,
        system_template=system_template,
        template=template,
    )


def _required_string(
    entry: Mapping[str, Any],
    field: str,
    *,
    role: AssistantRole,
) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PromptRegistryError(f"Prompt '{role}' manifest field '{field}' must be a non-empty string.")
    return value


def _required_string_list(
    entry: Mapping[str, Any],
    field: str,
    *,
    role: AssistantRole,
) -> tuple[str, ...]:
    value = entry.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise PromptRegistryError(f"Prompt '{role}' manifest field '{field}' must be a list of non-empty strings.")
    return tuple(cast(list[str], value))


def _validate_resource_path(resource: str, *, role: AssistantRole) -> None:
    path = PurePosixPath(resource)
    if (
        not path.parts
        or resource == "."
        or resource.strip() != resource
        or path.is_absolute()
        or "\\" in resource
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise PromptRegistryError(f"Prompt '{role}' has an unsafe resource path: {resource!r}.")


def _template_placeholders(template: str) -> frozenset[str]:
    try:
        return frozenset(field_name for _, field_name, _, _ in Formatter().parse(template) if field_name)
    except ValueError as exc:
        raise PromptRegistryError("Prompt template contains invalid braces.") from exc


def _read_package_resource(
    resource: str,
    *,
    strip_terminal_newline: bool = True,
) -> str:
    path = files(PROMPT_PACKAGE)
    for part in PurePosixPath(resource).parts:
        path = path.joinpath(part)
    content = path.read_text(encoding="utf-8")
    if not strip_terminal_newline:
        return content
    if content.endswith("\r\n"):
        return content[:-2]
    if content.endswith("\n"):
        return content[:-1]
    return content


def _current_time() -> str:
    return datetime.now().isoformat(timespec="seconds")


__all__ = [
    "ASSISTANT_ROLES",
    "AssistantRole",
    "PromptArtifact",
    "PromptRegistry",
    "PromptRegistryError",
    "build_prompt_registry",
]
