from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from evals.artifacts import redact_artifact_rows
from tech_doc_agent.app.api.schemas import RuntimeExecutionIdentityResponse
from tech_doc_agent.app.services.assistants.prompt_registry import ASSISTANT_ROLES


RuntimeIdentityStatus = Literal[
    "available",
    "disabled",
    "unavailable",
    "invalid",
]


@dataclass(frozen=True, slots=True)
class RuntimeIdentityLookup:
    status: RuntimeIdentityStatus
    manifest: dict[str, Any] | None = None
    cause_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status}
        if self.manifest is not None:
            payload["manifest"] = self.manifest
        if self.cause_type is not None:
            payload["cause_type"] = self.cause_type
        return payload


def approve_url_for(api_url: str, approve_url: str | None) -> str:
    if approve_url:
        return approve_url
    parsed = urlsplit(api_url)
    normalized_path = parsed.path.rstrip("/")
    approve_path = (
        f"{normalized_path}/approve"
        if normalized_path.endswith("/chat")
        else f"{normalized_path}/chat/approve"
    )
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            approve_path,
            parsed.query,
            "",
        )
    )


def identity_url_for(api_url: str, identity_url: str | None) -> str:
    if identity_url:
        return identity_url
    parsed = urlsplit(api_url)
    normalized_path = parsed.path.rstrip("/")
    base_path = (
        normalized_path[: -len("/chat")]
        if normalized_path.endswith("/chat")
        else normalized_path
    )
    identity_path = f"{base_path}/runtime/identity"
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            identity_path,
            parsed.query,
            "",
        )
    )


async def fetch_runtime_identity(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout_s: float,
) -> RuntimeIdentityLookup:
    try:
        response = await client.get(url, timeout=timeout_s)
    except Exception as exc:
        return RuntimeIdentityLookup(
            status="unavailable",
            cause_type=type(exc).__name__,
        )

    if response.status_code == 404:
        return RuntimeIdentityLookup(status="disabled")
    if response.status_code != 200:
        return RuntimeIdentityLookup(
            status="unavailable",
            cause_type=f"HTTP{response.status_code}",
        )
    try:
        manifest = validate_runtime_identity(response.json())
    except (ValueError, ValidationError, json.JSONDecodeError) as exc:
        return RuntimeIdentityLookup(
            status="invalid",
            cause_type=type(exc).__name__,
        )
    return RuntimeIdentityLookup(status="available", manifest=manifest)


def validate_runtime_identity(payload: Any) -> dict[str, Any]:
    identity = RuntimeExecutionIdentityResponse.model_validate(payload).model_dump(
        mode="json",
        exclude_none=True,
    )
    roles = tuple(
        assistant["assistant_role"]
        for assistant in identity["assistants"]
    )
    if roles != ASSISTANT_ROLES:
        raise ValueError("Runtime identity roles are incomplete or out of order")

    fingerprint = identity.pop("fingerprint")
    actual_fingerprint = _fingerprint(identity)
    if fingerprint != actual_fingerprint:
        raise ValueError("Runtime identity fingerprint does not match its payload")
    return {**identity, "fingerprint": fingerprint}


def build_eval_run_manifest(
    *,
    runner: str,
    dataset_path: Path,
    settings: dict[str, Any],
    runtime_identity: RuntimeIdentityLookup,
    generated_at: datetime | None = None,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(UTC)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    safe_settings = _safe_settings_payload(settings)
    git_state = _git_state(repository_root or Path(__file__).resolve().parents[1])
    return {
        "schema_version": 1,
        "runner": runner,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "runner_git": git_state,
        "dataset": {
            "name": dataset_path.name,
            "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        },
        "settings": {
            "values": safe_settings,
            "fingerprint": _fingerprint(safe_settings),
        },
        "runtime_identity": runtime_identity.to_payload(),
    }


def online_eval_settings(args: Any) -> dict[str, Any]:
    return {
        "api_endpoint": _safe_endpoint(args.api_url),
        "approve_endpoint": _safe_endpoint(
            approve_url_for(args.api_url, args.approve_url)
        ),
        "identity_endpoint": _safe_endpoint(
            identity_url_for(args.api_url, args.identity_url)
        ),
        "timeout_s": args.timeout,
        "limit": args.limit,
        "include_disabled": args.include_disabled,
        "interrupt_policy": args.interrupt_policy,
        "max_interrupt_rounds": args.max_interrupt_rounds,
        "reject_feedback_sha256": hashlib.sha256(
            args.reject_feedback.encode("utf-8")
        ).hexdigest(),
        "require_runtime_identity": args.require_runtime_identity,
    }


def _safe_settings_payload(settings: dict[str, Any]) -> dict[str, Any]:
    serialized = json.loads(
        json.dumps(settings, ensure_ascii=False, sort_keys=True, default=str)
    )
    return redact_artifact_rows([serialized])[0]


def _safe_endpoint(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    return {
        "scheme": parsed.scheme,
        "host_sha256": hashlib.sha256(
            (parsed.hostname or "").encode("utf-8")
        ).hexdigest(),
        "path": parsed.path,
    }


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _git_state(repository_root: Path) -> dict[str, Any]:
    commit = _run_git(repository_root, "rev-parse", "HEAD")
    status = _run_git(repository_root, "status", "--porcelain")
    return {
        "commit": commit,
        "dirty": None if status is None else bool(status),
    }


def _run_git(repository_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository_root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


__all__ = [
    "RuntimeIdentityLookup",
    "approve_url_for",
    "build_eval_run_manifest",
    "fetch_runtime_identity",
    "identity_url_for",
    "online_eval_settings",
    "validate_runtime_identity",
]
