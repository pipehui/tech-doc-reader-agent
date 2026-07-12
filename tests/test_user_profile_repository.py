import json

import pytest

from tech_doc_agent.app.application.profile_models import UserProfile
from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.tenant import TenantContext
from tech_doc_agent.app.infrastructure.persistence.user_profile_repository import (
    JsonUserProfileRepository,
)


TENANT = TenantContext("user-a", "tenant-docs")


def test_repository_round_trips_versioned_envelope(tmp_path):
    repository = JsonUserProfileRepository(tmp_path)
    profile = UserProfile.from_payload(
        {
            "experience_level": "进阶",
            "known_topics": ["StateGraph"],
        },
        tenant=TENANT,
    )

    repository.save(profile)

    path = tmp_path / "user_profiles" / "user-a" / "tenant-docs.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == 1
    assert envelope["profile"]["user_id"] == "user-a"
    assert "namespace" not in envelope["profile"]
    assert repository.get(TENANT) == profile


def test_repository_reads_flat_legacy_document_without_rewriting_it(tmp_path):
    path = tmp_path / "user_profiles" / "user-a" / "tenant-docs.json"
    path.parent.mkdir(parents=True)
    legacy_text = json.dumps({"experience_level": "进阶"})
    path.write_text(legacy_text, encoding="utf-8")
    repository = JsonUserProfileRepository(tmp_path)

    profile = repository.get(TENANT)

    assert profile.experience_level == "进阶"
    assert path.read_text(encoding="utf-8") == legacy_text


@pytest.mark.parametrize(
    ("document", "cause_type"),
    [
        ("{invalid json", "InvalidProfileJson"),
        ([], "InvalidProfileDocument"),
        (
            {"schema_version": 2, "profile": {}},
            "InvalidProfileEnvelope",
        ),
        (
            {"schema_version": 1, "profile": []},
            "InvalidProfileEnvelope",
        ),
    ],
)
def test_repository_rejects_invalid_versioned_document(
    tmp_path,
    document,
    cause_type,
):
    path = tmp_path / "user_profiles" / "user-a" / "tenant-docs.json"
    path.parent.mkdir(parents=True)
    encoded = document if isinstance(document, str) else json.dumps(document)
    path.write_text(encoded, encoding="utf-8")
    repository = JsonUserProfileRepository(tmp_path)

    with pytest.raises(ValidationError) as raised:
        repository.get(TENANT)

    assert raised.value.code == "user_profile_corrupt"
    assert raised.value.cause_type == cause_type
