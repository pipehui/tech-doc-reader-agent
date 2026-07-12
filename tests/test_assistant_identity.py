import pytest

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.assistants.identity import (
    AssistantExecutionIdentity,
    build_runtime_execution_identity,
)
from tech_doc_agent.app.services.assistants.definition import (
    build_assistant_definition,
)
from tech_doc_agent.app.services.assistants.model_factory import (
    AssistantModelProvider,
)
from tech_doc_agent.app.services.assistants.prompt_registry import (
    build_prompt_registry,
)


PROMPT_SHA256 = "a" * 64


def test_assistant_execution_identity_emits_stable_trace_metadata():
    identity = AssistantExecutionIdentity(
        role="parser",
        prompt_id="tech-doc-reader.parser.v1",
        prompt_sha256=PROMPT_SHA256,
        model_provider_id="provider-a",
        primary_model_id="model-primary",
        backup_model_id="model-backup",
    )

    assert identity.to_metadata() == {
        "assistant_role": "parser",
        "prompt_id": "tech-doc-reader.parser.v1",
        "prompt_sha256": PROMPT_SHA256,
        "model_provider_id": "provider-a",
        "primary_model_id": "model-primary",
        "backup_model_id": "model-backup",
    }


def test_assistant_execution_identity_omits_unconfigured_model_ids():
    identity = AssistantExecutionIdentity(
        role="summary",
        prompt_id="tech-doc-reader.summary.v1",
        prompt_sha256=PROMPT_SHA256,
        model_provider_id="provider-a",
    )

    assert "primary_model_id" not in identity.to_metadata()
    assert "backup_model_id" not in identity.to_metadata()


def test_assistant_definition_rejects_prompt_from_another_role():
    prompt = build_prompt_registry().require("primary")

    with pytest.raises(ValueError, match="cannot use prompt role"):
        build_assistant_definition(
            prompt=prompt,
            models=AssistantModelProvider(primary=object()),
            name="parser",
            safe_tools=(),
        )


def test_runtime_execution_identity_is_versioned_deterministic_and_secret_free():
    settings = Settings(
        MODEL_PROVIDER_ID="provider-a",
        PRIMARY_MODEL="model-primary",
        BACKUP_MODEL="model-backup",
        BACKUP_API_KEY="private-backup-key",
        OPENAI_API_KEY="private-primary-key",
        OPENAI_BASE_URL="https://private-provider.example/v1",
    )

    first = build_runtime_execution_identity(settings)
    second = build_runtime_execution_identity(settings)
    payload = first.to_payload()

    assert first.fingerprint == second.fingerprint
    assert payload["schema_version"] == 1
    assert payload["fingerprint"] == first.fingerprint
    assert [item["assistant_role"] for item in payload["assistants"]] == [
        "primary",
        "parser",
        "relation",
        "explanation",
        "examination",
        "summary",
    ]
    assert all(
        item["model_provider_id"] == "provider-a"
        and item["primary_model_id"] == "model-primary"
        and item["backup_model_id"] == "model-backup"
        for item in payload["assistants"]
    )
    serialized = str(payload)
    assert "private-backup-key" not in serialized
    assert "private-primary-key" not in serialized
    assert "private-provider.example" not in serialized
    assert "You are" not in serialized


def test_runtime_execution_fingerprint_changes_with_active_model_route():
    primary = build_runtime_execution_identity(
        Settings(PRIMARY_MODEL="model-a")
    )
    changed = build_runtime_execution_identity(
        Settings(PRIMARY_MODEL="model-b")
    )
    inactive_backup = build_runtime_execution_identity(
        Settings(
            PRIMARY_MODEL="model-a",
            BACKUP_MODEL="model-backup",
            BACKUP_API_KEY="",
        )
    )

    assert primary.fingerprint != changed.fingerprint
    assert primary.fingerprint == inactive_backup.fingerprint


@pytest.mark.parametrize(
    "override",
    [
        {"prompt_id": " prompt"},
        {"prompt_sha256": "not-a-digest"},
        {"model_provider_id": ""},
        {"primary_model_id": " model"},
        {"backup_model_id": ""},
    ],
)
def test_assistant_execution_identity_rejects_unstable_identifiers(override):
    values = {
        "role": "primary",
        "prompt_id": "tech-doc-reader.primary.v1",
        "prompt_sha256": PROMPT_SHA256,
        "model_provider_id": "provider-a",
        "primary_model_id": "model-primary",
        "backup_model_id": None,
        **override,
    }

    with pytest.raises(ValueError):
        AssistantExecutionIdentity(**values)
