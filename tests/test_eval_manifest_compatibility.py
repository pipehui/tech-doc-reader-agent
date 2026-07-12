import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from evals.check_manifest_compatibility import main as compatibility_main
from evals.manifest_compatibility import compare_eval_run_manifests
from evals.manifests import (
    RuntimeIdentityLookup,
    build_eval_run_manifest,
    fingerprint_payload,
)
from evals.retrieval_corpus import build_retrieval_corpus_identity
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.assistants.identity import (
    build_runtime_execution_identity,
)


def _corpus_identity(title: str = "Document A") -> dict:
    store = SimpleNamespace(
        documents=[
            {
                "id": 1,
                "title": title,
                "content": "content",
                "source": "test",
            }
        ],
        chunk_metadata=[],
        index=None,
        chunk_size=300,
        chunk_overlap=20,
    )
    return build_retrieval_corpus_identity(store).to_payload()


def _manifest(
    tmp_path,
    *,
    runner: str = "offline_retrieval_eval",
    settings: dict | None = None,
    runtime_identity: RuntimeIdentityLookup | None = None,
    subject_identity: dict | None = None,
    commit: str = "a" * 40,
) -> dict:
    dataset = tmp_path / "cases.json"
    dataset.write_text('[{"id":"case-1"}]', encoding="utf-8")
    manifest = build_eval_run_manifest(
        runner=runner,
        dataset_path=dataset,
        settings=settings or {"mode": "bm25", "top_k": 5},
        runtime_identity=runtime_identity
        or RuntimeIdentityLookup(status="not_applicable"),
        subject_identity=(
            _corpus_identity()
            if subject_identity is None and runner == "offline_retrieval_eval"
            else subject_identity
        ),
        generated_at=datetime(2026, 7, 12, tzinfo=UTC),
        repository_root=tmp_path / "not-a-repository",
    )
    manifest["runner_git"] = {"commit": commit, "dirty": False}
    return manifest


def _online_identity(model: str) -> RuntimeIdentityLookup:
    return RuntimeIdentityLookup(
        status="available",
        manifest=build_runtime_execution_identity(
            Settings(PRIMARY_MODEL=model)
        ).to_payload(),
    )


def test_compatible_manifests_allow_different_clean_runner_commits(tmp_path):
    baseline = _manifest(tmp_path)
    candidate = copy.deepcopy(baseline)
    candidate["runner_git"]["commit"] = "b" * 40
    candidate["generated_at"] = "2026-07-13T00:00:00+00:00"

    result = compare_eval_run_manifests(baseline, candidate)

    assert result.status == "compatible"
    assert result.compatible is True
    assert result.differences == ()


def test_dataset_settings_runtime_and_corpus_changes_are_incompatible(tmp_path):
    baseline = _manifest(tmp_path)

    changed_dataset = copy.deepcopy(baseline)
    changed_dataset["dataset"]["sha256"] = "f" * 64
    assert compare_eval_run_manifests(baseline, changed_dataset).status == "incompatible"

    changed_settings = copy.deepcopy(baseline)
    changed_settings["settings"]["values"]["top_k"] = 6
    changed_settings["settings"]["fingerprint"] = fingerprint_payload(
        changed_settings["settings"]["values"]
    )
    assert compare_eval_run_manifests(baseline, changed_settings).status == "incompatible"

    changed_corpus = copy.deepcopy(baseline)
    changed_corpus["subject_identity"] = _corpus_identity("Document B")
    assert compare_eval_run_manifests(baseline, changed_corpus).status == "incompatible"

    online_baseline = _manifest(
        tmp_path,
        runner="online_agent_eval",
        runtime_identity=_online_identity("model-a"),
        subject_identity=None,
    )
    online_candidate = _manifest(
        tmp_path,
        runner="online_agent_eval",
        runtime_identity=_online_identity("model-b"),
        subject_identity=None,
        commit="b" * 40,
    )
    runtime_result = compare_eval_run_manifests(online_baseline, online_candidate)
    assert runtime_result.status == "incompatible"
    assert {issue.code for issue in runtime_result.differences} == {
        "runtime_identity_mismatch"
    }


def test_missing_identity_and_dirty_provenance_are_unverified(tmp_path):
    baseline = _manifest(tmp_path)
    candidate = copy.deepcopy(baseline)
    del baseline["subject_identity"]
    del candidate["subject_identity"]

    missing_corpus = compare_eval_run_manifests(baseline, candidate)

    assert missing_corpus.status == "unverified"
    assert {issue.code for issue in missing_corpus.verification_issues} == {
        "retrieval_corpus_identity_missing"
    }

    wrong_subject = {
        "schema_version": 1,
        "kind": "other_subject",
    }
    wrong_subject["fingerprint"] = fingerprint_payload(wrong_subject)
    baseline = _manifest(tmp_path)
    candidate = copy.deepcopy(baseline)
    baseline["subject_identity"] = wrong_subject
    candidate["subject_identity"] = copy.deepcopy(wrong_subject)
    wrong_kind = compare_eval_run_manifests(baseline, candidate)

    assert wrong_kind.status == "unverified"
    assert {issue.code for issue in wrong_kind.verification_issues} == {
        "retrieval_corpus_identity_wrong_kind"
    }

    baseline = _manifest(tmp_path)
    candidate = copy.deepcopy(baseline)
    candidate["runner_git"]["dirty"] = True
    dirty = compare_eval_run_manifests(baseline, candidate)
    allowed_dirty = compare_eval_run_manifests(
        baseline,
        candidate,
        allow_dirty=True,
    )

    assert dirty.status == "unverified"
    assert allowed_dirty.status == "compatible"


def test_unavailable_online_runtime_is_unverified(tmp_path):
    baseline = _manifest(
        tmp_path,
        runner="online_agent_eval",
        runtime_identity=RuntimeIdentityLookup(status="unavailable", cause_type="TimeoutError"),
        subject_identity=None,
    )
    candidate = copy.deepcopy(baseline)

    result = compare_eval_run_manifests(baseline, candidate)

    assert result.status == "unverified"
    assert {issue.code for issue in result.verification_issues} == {
        "runtime_identity_unverified"
    }


def test_tampered_manifest_is_invalid_instead_of_incompatible(tmp_path):
    baseline = _manifest(tmp_path)
    candidate = copy.deepcopy(baseline)
    candidate["settings"]["values"]["top_k"] = 999

    result = compare_eval_run_manifests(baseline, candidate)

    assert result.status == "invalid"
    assert result.differences == ()
    assert result.verification_issues[0].code == "candidate_manifest_invalid"


def test_manifest_compatibility_cli_uses_gate_exit_codes(tmp_path, capsys):
    baseline = _manifest(tmp_path)
    candidate = copy.deepcopy(baseline)
    candidate["runner_git"]["commit"] = "b" * 40
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    assert compatibility_main([str(baseline_path), str(candidate_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "compatible"

    candidate["dataset"]["sha256"] = "f" * 64
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    assert compatibility_main([str(baseline_path), str(candidate_path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "incompatible"
