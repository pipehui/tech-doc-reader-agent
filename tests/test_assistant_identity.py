import pytest

from tech_doc_agent.app.services.assistants.identity import (
    AssistantExecutionIdentity,
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
