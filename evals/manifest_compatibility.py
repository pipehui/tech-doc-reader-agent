from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from evals.manifests import validate_eval_run_manifest
from evals.retrieval_corpus import (
    RETRIEVAL_CORPUS_KIND,
    validate_retrieval_corpus_identity,
)


CompatibilityStatus = Literal[
    "compatible",
    "incompatible",
    "unverified",
    "invalid",
]


@dataclass(frozen=True, slots=True)
class CompatibilityIssue:
    code: str
    path: str
    message: str

    def to_payload(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ManifestCompatibilityResult:
    status: CompatibilityStatus
    differences: tuple[CompatibilityIssue, ...] = ()
    verification_issues: tuple[CompatibilityIssue, ...] = ()

    @property
    def compatible(self) -> bool:
        return self.status == "compatible"

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "compatible": self.compatible,
            "differences": [issue.to_payload() for issue in self.differences],
            "verification_issues": [
                issue.to_payload() for issue in self.verification_issues
            ],
        }


def compare_eval_run_manifests(
    baseline: Any,
    candidate: Any,
    *,
    allow_dirty: bool = False,
) -> ManifestCompatibilityResult:
    validation_issues: list[CompatibilityIssue] = []
    baseline_manifest = _validated_manifest(
        baseline,
        label="baseline",
        issues=validation_issues,
    )
    candidate_manifest = _validated_manifest(
        candidate,
        label="candidate",
        issues=validation_issues,
    )
    if validation_issues:
        return ManifestCompatibilityResult(
            status="invalid",
            verification_issues=tuple(validation_issues),
        )
    assert baseline_manifest is not None
    assert candidate_manifest is not None

    differences: list[CompatibilityIssue] = []
    verification_issues: list[CompatibilityIssue] = []
    _compare_value(
        baseline_manifest["runner"],
        candidate_manifest["runner"],
        path="runner",
        code="runner_mismatch",
        message="The manifests were produced by different eval runners.",
        differences=differences,
    )
    _compare_value(
        baseline_manifest["dataset"]["sha256"],
        candidate_manifest["dataset"]["sha256"],
        path="dataset.sha256",
        code="dataset_mismatch",
        message="The eval case datasets have different content.",
        differences=differences,
    )
    _compare_value(
        baseline_manifest["settings"]["fingerprint"],
        candidate_manifest["settings"]["fingerprint"],
        path="settings.fingerprint",
        code="settings_mismatch",
        message="The eval settings are different.",
        differences=differences,
    )
    _compare_runtime_identity(
        baseline_manifest,
        candidate_manifest,
        differences=differences,
        verification_issues=verification_issues,
    )
    _compare_subject_identity(
        baseline_manifest,
        candidate_manifest,
        differences=differences,
        verification_issues=verification_issues,
    )
    _check_git_provenance(
        baseline_manifest,
        label="baseline",
        allow_dirty=allow_dirty,
        issues=verification_issues,
    )
    _check_git_provenance(
        candidate_manifest,
        label="candidate",
        allow_dirty=allow_dirty,
        issues=verification_issues,
    )

    if differences:
        status: CompatibilityStatus = "incompatible"
    elif verification_issues:
        status = "unverified"
    else:
        status = "compatible"
    return ManifestCompatibilityResult(
        status=status,
        differences=tuple(differences),
        verification_issues=tuple(verification_issues),
    )


def _validated_manifest(
    value: Any,
    *,
    label: str,
    issues: list[CompatibilityIssue],
) -> dict[str, Any] | None:
    try:
        manifest = validate_eval_run_manifest(value)
        subject_identity = manifest.get("subject_identity")
        if (
            isinstance(subject_identity, dict)
            and subject_identity.get("kind") == RETRIEVAL_CORPUS_KIND
        ):
            validate_retrieval_corpus_identity(subject_identity)
        return manifest
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(
            CompatibilityIssue(
                code=f"{label}_manifest_invalid",
                path=label,
                message=str(exc),
            )
        )
        return None


def _compare_runtime_identity(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    differences: list[CompatibilityIssue],
    verification_issues: list[CompatibilityIssue],
) -> None:
    baseline_identity = baseline["runtime_identity"]
    candidate_identity = candidate["runtime_identity"]
    baseline_status = baseline_identity["status"]
    candidate_status = candidate_identity["status"]
    if baseline_status != candidate_status:
        differences.append(
            CompatibilityIssue(
                code="runtime_identity_status_mismatch",
                path="runtime_identity.status",
                message="Runtime identity availability differs between the runs.",
            )
        )
        return

    runner = baseline["runner"]
    if baseline_status == "available":
        _compare_value(
            baseline_identity["manifest"]["fingerprint"],
            candidate_identity["manifest"]["fingerprint"],
            path="runtime_identity.manifest.fingerprint",
            code="runtime_identity_mismatch",
            message="The evaluated runtime identities are different.",
            differences=differences,
        )
        if runner.startswith("offline_"):
            verification_issues.append(
                CompatibilityIssue(
                    code="unexpected_offline_runtime_identity",
                    path="runtime_identity.status",
                    message="An offline runner should declare runtime identity not applicable.",
                )
            )
        else:
            _compare_deployment_identity(
                baseline_identity["manifest"],
                candidate_identity["manifest"],
                differences=differences,
                verification_issues=verification_issues,
            )
    elif baseline_status == "not_applicable":
        if not runner.startswith("offline_"):
            verification_issues.append(
                CompatibilityIssue(
                    code="missing_online_runtime_identity",
                    path="runtime_identity.status",
                    message="The online eval target identity was not verified.",
                )
            )
    else:
        verification_issues.append(
            CompatibilityIssue(
                code="runtime_identity_unverified",
                path="runtime_identity.status",
                message="The evaluated runtime identity is unavailable or invalid.",
            )
        )


def _compare_deployment_identity(
    baseline_runtime: dict[str, Any],
    candidate_runtime: dict[str, Any],
    *,
    differences: list[CompatibilityIssue],
    verification_issues: list[CompatibilityIssue],
) -> None:
    baseline_deployment = baseline_runtime.get("deployment")
    candidate_deployment = candidate_runtime.get("deployment")
    if baseline_deployment is None or candidate_deployment is None:
        verification_issues.append(
            CompatibilityIssue(
                code="deployment_identity_missing",
                path="runtime_identity.manifest.deployment",
                message="Both online runtimes must expose deployment commit identity.",
            )
        )
        return

    baseline_status = baseline_deployment["status"]
    candidate_status = candidate_deployment["status"]
    if baseline_status != candidate_status:
        differences.append(
            CompatibilityIssue(
                code="deployment_identity_status_mismatch",
                path="runtime_identity.manifest.deployment.status",
                message="Deployment commit availability differs between the runtimes.",
            )
        )
        return
    if baseline_status == "unavailable":
        verification_issues.append(
            CompatibilityIssue(
                code="deployment_commit_unverified",
                path="runtime_identity.manifest.deployment.status",
                message="The online runtime deployment commit is unavailable.",
            )
        )
        return
    _compare_value(
        baseline_deployment["commit_sha"],
        candidate_deployment["commit_sha"],
        path="runtime_identity.manifest.deployment.commit_sha",
        code="deployment_commit_mismatch",
        message="The online runtimes were built from different commits.",
        differences=differences,
    )


def _compare_subject_identity(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    differences: list[CompatibilityIssue],
    verification_issues: list[CompatibilityIssue],
) -> None:
    baseline_subject = baseline.get("subject_identity")
    candidate_subject = candidate.get("subject_identity")
    requires_corpus = baseline["runner"] == "offline_retrieval_eval"
    if baseline_subject is None or candidate_subject is None:
        if requires_corpus:
            verification_issues.append(
                CompatibilityIssue(
                    code="retrieval_corpus_identity_missing",
                    path="subject_identity",
                    message="Both retrieval runs must identify their corpus content.",
                )
            )
        elif baseline_subject is not candidate_subject:
            differences.append(
                CompatibilityIssue(
                    code="subject_identity_presence_mismatch",
                    path="subject_identity",
                    message="Only one run declares an evaluation subject identity.",
                )
            )
        return

    if requires_corpus and (
        baseline_subject.get("kind") != RETRIEVAL_CORPUS_KIND
        or candidate_subject.get("kind") != RETRIEVAL_CORPUS_KIND
    ):
        verification_issues.append(
            CompatibilityIssue(
                code="retrieval_corpus_identity_wrong_kind",
                path="subject_identity.kind",
                message="Retrieval runs must use a retrieval corpus subject identity.",
            )
        )

    _compare_value(
        baseline_subject["kind"],
        candidate_subject["kind"],
        path="subject_identity.kind",
        code="subject_identity_kind_mismatch",
        message="The evaluation subject kinds are different.",
        differences=differences,
    )
    _compare_value(
        baseline_subject["schema_version"],
        candidate_subject["schema_version"],
        path="subject_identity.schema_version",
        code="subject_identity_schema_mismatch",
        message="The evaluation subject schemas are different.",
        differences=differences,
    )
    _compare_value(
        baseline_subject["fingerprint"],
        candidate_subject["fingerprint"],
        path="subject_identity.fingerprint",
        code="subject_identity_mismatch",
        message="The evaluation subjects have different content identities.",
        differences=differences,
    )


def _check_git_provenance(
    manifest: dict[str, Any],
    *,
    label: str,
    allow_dirty: bool,
    issues: list[CompatibilityIssue],
) -> None:
    runner_git = manifest["runner_git"]
    if runner_git["commit"] is None:
        issues.append(
            CompatibilityIssue(
                code=f"{label}_commit_unknown",
                path=f"{label}.runner_git.commit",
                message=f"The {label} runner commit is unknown.",
            )
        )
    dirty = runner_git["dirty"]
    if dirty is None:
        issues.append(
            CompatibilityIssue(
                code=f"{label}_dirty_state_unknown",
                path=f"{label}.runner_git.dirty",
                message=f"The {label} worktree state is unknown.",
            )
        )
    elif dirty and not allow_dirty:
        issues.append(
            CompatibilityIssue(
                code=f"{label}_worktree_dirty",
                path=f"{label}.runner_git.dirty",
                message=f"The {label} run came from a dirty worktree.",
            )
        )


def _compare_value(
    baseline: Any,
    candidate: Any,
    *,
    path: str,
    code: str,
    message: str,
    differences: list[CompatibilityIssue],
) -> None:
    if baseline != candidate:
        differences.append(
            CompatibilityIssue(
                code=code,
                path=path,
                message=message,
            )
        )


__all__ = [
    "CompatibilityIssue",
    "ManifestCompatibilityResult",
    "compare_eval_run_manifests",
]
