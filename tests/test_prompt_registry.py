import copy
import hashlib
import json
from importlib.resources import files
from pathlib import PurePosixPath

import pytest

import tech_doc_agent.app.services.assistants.prompt_registry as prompt_registry
from tech_doc_agent.app.services.assistants.prompt_registry import (
    ASSISTANT_ROLES,
    PROMPT_PACKAGE,
    PromptRegistry,
    PromptRegistryError,
    build_prompt_registry,
)


EXPECTED_PROMPTS = {
    "primary": {
        "id": "tech-doc-reader.primary.v1",
        "sha256": "034f2970a0d7fead2f8efdca693dbb6c59585c2400b839a0dc3dc9e9609ba9a3",
        "input_variables": ["user_info"],
    },
    "parser": {
        "id": "tech-doc-reader.parser.v1",
        "sha256": "208e4aa024549388a23d403f7f238cd470f45b5c2899607bc3f2458aba7d5873",
        "input_variables": [],
    },
    "relation": {
        "id": "tech-doc-reader.relation.v1",
        "sha256": "303cfb81a144940efef6ab7ea36881bb7bb6251c8897dff986f7da98881c634e",
        "input_variables": [],
    },
    "explanation": {
        "id": "tech-doc-reader.explanation.v1",
        "sha256": "052b3f2de5cc1759b217dc4f4aff5a7e3d4e74ac9f6cb013d66d5412fd3a72e0",
        "input_variables": [],
    },
    "examination": {
        "id": "tech-doc-reader.examination.v1",
        "sha256": "c9e3b3c334ca71a36873d2751e8d7adfcdfeadea4df876d4c2fb9562d61a908b",
        "input_variables": ["learning_target"],
    },
    "summary": {
        "id": "tech-doc-reader.summary.v1",
        "sha256": "497c761fb817c51e6487dc0d19b16f4cb6d3a61213a7444d34aaae783ab3198d",
        "input_variables": ["learning_target"],
    },
}


def test_packaged_prompts_match_pre_migration_hashes_and_variables():
    registry = build_prompt_registry()

    assert tuple(artifact.role for artifact in registry.artifacts()) == ASSISTANT_ROLES
    for role, expected in EXPECTED_PROMPTS.items():
        artifact = registry.require(role)

        assert artifact.prompt_id == expected["id"]
        assert artifact.sha256 == expected["sha256"]
        assert artifact.sha256 == hashlib.sha256(artifact.system_template.encode("utf-8")).hexdigest()
        assert artifact.template.messages[0].prompt.template == artifact.system_template
        assert artifact.template.input_variables == expected["input_variables"]
        assert artifact.template.optional_variables == ["messages"]
        assert set(artifact.template.partial_variables) == {"messages", "time"}

    assert len(registry.require("primary").resources) == 16


def test_registry_rejects_resource_hash_drift():
    manifest = _manifest()

    def tampered_reader(resource: str) -> str:
        content = _resource_text(resource)
        return f"{content} changed" if resource == "parser.md" else content

    with pytest.raises(PromptRegistryError, match="content hash mismatch"):
        PromptRegistry.from_manifest(manifest, read_resource=tampered_reader)


def test_registry_rejects_missing_required_placeholder_after_hash_validation():
    manifest = _manifest()
    original = _resource_text("summary.md")
    changed = original.replace("{learning_target}", "learning target")
    manifest["prompts"]["summary"]["sha256"] = hashlib.sha256(changed.encode("utf-8")).hexdigest()

    def changed_reader(resource: str) -> str:
        return changed if resource == "summary.md" else _resource_text(resource)

    with pytest.raises(PromptRegistryError, match=r"missing=\['learning_target'\]"):
        PromptRegistry.from_manifest(manifest, read_resource=changed_reader)


def test_registry_rejects_missing_role_and_unsafe_resource_path():
    missing_role_manifest = _manifest()
    del missing_role_manifest["prompts"]["parser"]
    with pytest.raises(PromptRegistryError, match=r"missing=\['parser'\]"):
        PromptRegistry.from_manifest(
            missing_role_manifest,
            read_resource=_resource_text,
        )

    unsafe_path_manifest = _manifest()
    unsafe_path_manifest["prompts"]["parser"]["resources"] = ["../parser.md"]
    with pytest.raises(PromptRegistryError, match="unsafe resource path"):
        PromptRegistry.from_manifest(
            unsafe_path_manifest,
            read_resource=_resource_text,
        )


def test_registry_maps_missing_manifest_to_startup_validation_error(monkeypatch):
    def missing_manifest(*args, **kwargs):
        raise FileNotFoundError("manifest missing")

    monkeypatch.setattr(
        prompt_registry,
        "_read_package_resource",
        missing_manifest,
    )

    with pytest.raises(PromptRegistryError, match="manifest could not be read"):
        build_prompt_registry()


def _manifest() -> dict:
    return copy.deepcopy(json.loads(files(PROMPT_PACKAGE).joinpath("manifest.json").read_text(encoding="utf-8")))


def _resource_text(resource: str) -> str:
    path = files(PROMPT_PACKAGE)
    for part in PurePosixPath(resource).parts:
        path = path.joinpath(part)
    return path.read_text(encoding="utf-8").removesuffix("\n")
