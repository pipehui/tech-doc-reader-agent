from datetime import UTC, datetime

from tech_doc_agent.app.application.profile_models import (
    UserProfile,
    UserProfileUpdate,
)
from tech_doc_agent.app.application.profile_service import UserProfileService
from tech_doc_agent.app.core.tenant import TenantContext


TENANT = TenantContext("user-a", "tenant-docs")


def test_profile_factory_normalizes_legacy_payload_and_detaches_lists():
    profile = UserProfile.from_payload(
        {
            "profile_version": "invalid",
            "experience_level": "  ",
            "known_topics": ["StateGraph", " stategraph ", "", 3],
            "weak_topics": ("ignored-non-list",),
            "notes": None,
        },
        tenant=TENANT,
    )

    assert profile.profile_version == 1
    assert profile.experience_level == "初学者"
    assert profile.known_topics == ("StateGraph", "3")
    assert profile.weak_topics == ()
    assert profile.notes == ""

    payload = profile.to_payload()
    payload["known_topics"].append("mutated")

    assert profile.known_topics == ("StateGraph", "3")


def test_profile_update_is_immutable_and_reconciles_topic_sets():
    profile = UserProfile.from_payload(
        {
            "known_topics": ["Reducer"],
            "weak_topics": ["Checkpoint", "StateGraph"],
        },
        tenant=TENANT,
    )
    update = UserProfileUpdate.create(
        experience_level=" 进阶 ",
        known_topics=["StateGraph", "stategraph"],
        weak_topics=["Reducer", "Streaming"],
        resolved_weak_topics=["Checkpoint"],
        evidence=" 用户主动请求更新 ",
    )

    result = profile.apply(update, timestamp="2026-07-12T08:00:00+00:00")

    assert result.status == "updated"
    assert result.changed is True
    assert result.profile is not profile
    assert result.profile.experience_level == "进阶"
    assert result.profile.known_topics == ("Reducer", "StateGraph")
    assert result.profile.weak_topics == ("Streaming",)
    assert result.profile.last_update_reason == "用户主动请求更新"
    assert result.profile.updated_at == "2026-07-12T08:00:00+00:00"
    assert profile.experience_level == "初学者"
    assert profile.weak_topics == ("Checkpoint", "StateGraph")


def test_unchanged_profile_returns_same_model_and_service_skips_save():
    class Repository:
        def __init__(self):
            self.profile = UserProfile.default(TENANT)
            self.saved = []

        def get(self, tenant):
            assert tenant == TENANT
            return self.profile

        def save(self, profile):
            self.saved.append(profile)

    repository = Repository()
    service = UserProfileService(
        repository=repository,
        clock=lambda: datetime(2026, 7, 12, 8, 0, tzinfo=UTC),
    )

    result = service.update_profile(
        user_id=TENANT.user_id,
        namespace=TENANT.namespace,
    )

    assert result.status == "unchanged"
    assert result.profile is repository.profile
    assert repository.saved == []


def test_evidence_is_an_explicit_update_even_when_text_is_unchanged():
    profile = UserProfile.from_payload(
        {"last_update_reason": "same evidence"},
        tenant=TENANT,
    )

    result = profile.apply(
        UserProfileUpdate.create(evidence="same evidence"),
        timestamp="2026-07-12T08:00:00+00:00",
    )

    assert result.status == "updated"
    assert result.profile.updated_at == "2026-07-12T08:00:00+00:00"
