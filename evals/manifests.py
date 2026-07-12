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
from tech_doc_agent.app.core.revisions import is_full_git_commit_sha
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.agents.prompt_registry import ASSISTANT_ROLES


RuntimeIdentityStatus = Literal[
    "available",
    "disabled",
    "unavailable",
    "invalid",
    "not_applicable",
]
EVAL_MANIFEST_SCHEMA_VERSION = 1


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
    actual_fingerprint = fingerprint_payload(identity)
    if fingerprint != actual_fingerprint:
        raise ValueError("Runtime identity fingerprint does not match its payload")
    return {**identity, "fingerprint": fingerprint}


def runtime_identity_is_verified(lookup: RuntimeIdentityLookup) -> bool:
    if lookup.status != "available" or lookup.manifest is None:
        return False
    deployment = lookup.manifest.get("deployment")
    return (
        isinstance(deployment, dict)
        and deployment.get("status") == "configured"
        and is_full_git_commit_sha(deployment.get("commit_sha"))
    )


def build_eval_run_manifest(
    *,
    runner: str,
    dataset_path: Path,
    settings: dict[str, Any],
    runtime_identity: RuntimeIdentityLookup,
    subject_identity: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(UTC)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    safe_settings = _safe_settings_payload(settings)
    git_state = _git_state(repository_root or Path(__file__).resolve().parents[1])
    manifest = {
        "schema_version": EVAL_MANIFEST_SCHEMA_VERSION,
        "runner": runner,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "runner_git": git_state,
        "dataset": {
            "name": dataset_path.name,
            "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        },
        "settings": {
            "values": safe_settings,
            "fingerprint": fingerprint_payload(safe_settings),
        },
        "runtime_identity": runtime_identity.to_payload(),
    }
    if subject_identity is not None:
        manifest["subject_identity"] = validate_subject_identity(subject_identity)
    return manifest


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


def retrieval_eval_settings(
    args: Any,
    *,
    app_settings: Settings | None = None,
) -> dict[str, Any]:
    vector_top_k = args.vector_top_k
    if vector_top_k is None and app_settings is not None:
        vector_top_k = app_settings.HYBRID_RAG_VECTOR_TOP_K

    embedding_identity = None
    if args.mode in ("vector", "hybrid") and app_settings is not None:
        embedding_identity = {
            "model_id": app_settings.EMBEDDING_MODEL or None,
            "endpoint": (
                _safe_endpoint(app_settings.EMBEDDING_API_BASE)
                if app_settings.EMBEDDING_API_BASE
                else {"kind": "sdk_default"}
            ),
        }

    return {
        "mode": args.mode,
        "top_k": args.k,
        "bm25_top_k": (
            app_settings.HYBRID_RAG_BM25_TOP_K
            if app_settings is not None
            else None
        ),
        "vector_top_k": vector_top_k,
        "rrf_k": (
            app_settings.HYBRID_RAG_RRF_K
            if app_settings is not None
            else None
        ),
        "embedding": embedding_identity,
        "limit": args.limit,
        "include_disabled": args.include_disabled,
    }


def context_compaction_eval_settings(args: Any) -> dict[str, Any]:
    return {
        "limit": args.limit,
        "iterations": args.iterations,
        "max_messages": args.max_messages,
        "max_serialized_bytes": args.max_serialized_bytes,
        "keep_recent_turns": args.keep_recent_turns,
        "summary_max_chars": args.summary_max_chars,
        "answer_metric": "deterministic_marker_recall_proxy",
        "token_metric": "langchain_count_tokens_approximately",
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


def fingerprint_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def validate_subject_identity(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Subject identity must be an object")
    identity = dict(payload)
    kind = identity.get("kind")
    schema_version = identity.get("schema_version")
    fingerprint = identity.pop("fingerprint", None)
    if not isinstance(kind, str) or not kind or kind != kind.strip():
        raise ValueError("Subject identity kind must be a non-empty trimmed string")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise ValueError("Subject identity schema_version must be a positive integer")
    if not _is_sha256(fingerprint):
        raise ValueError("Subject identity fingerprint must be a SHA-256 digest")
    if fingerprint != fingerprint_payload(identity):
        raise ValueError("Subject identity fingerprint does not match its payload")
    return {**identity, "fingerprint": fingerprint}


def validate_eval_run_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Eval run manifest must be an object")
    manifest = dict(payload)
    if manifest.get("schema_version") != EVAL_MANIFEST_SCHEMA_VERSION:
        raise ValueError("Eval run manifest schema_version is unsupported")

    runner = manifest.get("runner")
    if not isinstance(runner, str) or not runner or runner != runner.strip():
        raise ValueError("Eval run manifest runner must be a non-empty trimmed string")

    generated_at = manifest.get("generated_at")
    if not isinstance(generated_at, str):
        raise ValueError("Eval run manifest generated_at must be an ISO timestamp")
    try:
        generated_datetime = datetime.fromisoformat(generated_at)
    except ValueError as exc:
        raise ValueError("Eval run manifest generated_at must be an ISO timestamp") from exc
    if generated_datetime.tzinfo is None:
        raise ValueError("Eval run manifest generated_at must include a timezone")

    runner_git = manifest.get("runner_git")
    if not isinstance(runner_git, dict):
        raise ValueError("Eval run manifest runner_git must be an object")
    commit = runner_git.get("commit")
    dirty = runner_git.get("dirty")
    if commit is not None and not is_full_git_commit_sha(commit):
        raise ValueError(
            "Eval run manifest runner_git.commit must be a full Git commit SHA or null"
        )
    if dirty is not None and not isinstance(dirty, bool):
        raise ValueError("Eval run manifest runner_git.dirty must be a boolean or null")

    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("Eval run manifest dataset must be an object")
    if not isinstance(dataset.get("name"), str) or not dataset["name"]:
        raise ValueError("Eval run manifest dataset.name must be a non-empty string")
    if not _is_sha256(dataset.get("sha256")):
        raise ValueError("Eval run manifest dataset.sha256 must be a SHA-256 digest")

    settings = manifest.get("settings")
    if not isinstance(settings, dict) or not isinstance(settings.get("values"), dict):
        raise ValueError("Eval run manifest settings.values must be an object")
    if not _is_sha256(settings.get("fingerprint")):
        raise ValueError("Eval run manifest settings.fingerprint must be a SHA-256 digest")
    if settings["fingerprint"] != fingerprint_payload(settings["values"]):
        raise ValueError("Eval run manifest settings fingerprint does not match its payload")

    runtime_identity = manifest.get("runtime_identity")
    if not isinstance(runtime_identity, dict):
        raise ValueError("Eval run manifest runtime_identity must be an object")
    status = runtime_identity.get("status")
    if status not in (
        "available",
        "disabled",
        "unavailable",
        "invalid",
        "not_applicable",
    ):
        raise ValueError("Eval run manifest runtime identity status is invalid")
    runtime_manifest = runtime_identity.get("manifest")
    if status == "available":
        if runtime_manifest is None:
            raise ValueError("Available runtime identity must include a manifest")
        validate_runtime_identity(runtime_manifest)
    elif runtime_manifest is not None:
        raise ValueError("Unavailable runtime identity cannot include a manifest")
    cause_type = runtime_identity.get("cause_type")
    if cause_type is not None and not isinstance(cause_type, str):
        raise ValueError("Runtime identity cause_type must be a string")

    subject_identity = manifest.get("subject_identity")
    if subject_identity is not None:
        validate_subject_identity(subject_identity)
    return manifest


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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
    "EVAL_MANIFEST_SCHEMA_VERSION",
    "RuntimeIdentityLookup",
    "approve_url_for",
    "build_eval_run_manifest",
    "context_compaction_eval_settings",
    "fetch_runtime_identity",
    "fingerprint_payload",
    "identity_url_for",
    "online_eval_settings",
    "retrieval_eval_settings",
    "runtime_identity_is_verified",
    "validate_eval_run_manifest",
    "validate_runtime_identity",
    "validate_subject_identity",
]
