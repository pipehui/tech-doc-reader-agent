from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tech_doc_agent.app.application.profile_models import UserProfile
from tech_doc_agent.app.core.errors import ApplicationError, ValidationError
from tech_doc_agent.app.core.tenant import DEFAULT_NAMESPACE, TenantContext
from tech_doc_agent.app.infrastructure.persistence.atomic_json import (
    read_json,
    write_json_atomic,
)


USER_PROFILE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class JsonUserProfileRepository:
    data_path: Path

    def get(self, tenant: TenantContext) -> UserProfile:
        path = self._read_path(tenant)
        if not path.exists():
            return UserProfile.default(tenant)

        try:
            value = read_json(path)
        except ApplicationError as exc:
            if exc.cause_type == "JSONDecodeError":
                raise _corrupt_user_profile("InvalidProfileJson") from exc
            raise
        payload = self._profile_payload(value)
        return UserProfile.from_payload(payload, tenant=tenant)

    def save(self, profile: UserProfile) -> None:
        payload = profile.to_payload()
        payload.pop("namespace", None)
        write_json_atomic(
            self._path(profile.tenant),
            {
                "schema_version": USER_PROFILE_SCHEMA_VERSION,
                "profile": payload,
            },
        )

    def _profile_payload(self, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise _corrupt_user_profile("InvalidProfileDocument")

        if "schema_version" not in value:
            return value

        payload = value.get("profile")
        if (
            value.get("schema_version") != USER_PROFILE_SCHEMA_VERSION
            or not isinstance(payload, Mapping)
        ):
            raise _corrupt_user_profile("InvalidProfileEnvelope")
        return payload

    def _path(self, tenant: TenantContext) -> Path:
        return (
            self.data_path
            / "user_profiles"
            / _profile_path_segment(tenant.user_id)
            / f"{_profile_path_segment(tenant.namespace)}.json"
        )

    def _read_path(self, tenant: TenantContext) -> Path:
        path = self._path(tenant)
        if path.exists():
            return path

        legacy_path = self._legacy_path(tenant)
        if tenant.namespace == DEFAULT_NAMESPACE and legacy_path.exists():
            return legacy_path
        return path

    def _legacy_path(self, tenant: TenantContext) -> Path:
        return self.data_path / "user_profiles" / f"{tenant.user_id}.json"


def _profile_path_segment(value: str) -> str:
    return quote(value, safe="")


def _corrupt_user_profile(cause_type: str) -> ValidationError:
    return ValidationError(
        "The user profile document is invalid.",
        code="user_profile_corrupt",
        retryable=False,
        dependency="file_repository",
        cause_type=cause_type,
    )


__all__ = [
    "USER_PROFILE_SCHEMA_VERSION",
    "JsonUserProfileRepository",
]
