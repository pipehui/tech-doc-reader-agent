import asyncio
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from evals.manifests import (
    RuntimeIdentityLookup,
    approve_url_for,
    build_eval_run_manifest,
    context_compaction_eval_settings,
    fetch_runtime_identity,
    identity_url_for,
    online_eval_settings,
    retrieval_eval_settings,
    validate_eval_run_manifest,
    validate_runtime_identity,
)
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.assistants.identity import (
    build_runtime_execution_identity,
)


def _runtime_manifest() -> dict:
    return build_runtime_execution_identity(
        Settings(PRIMARY_MODEL="model-a")
    ).to_payload()


def test_runtime_identity_validation_recomputes_fingerprint_and_role_order():
    manifest = _runtime_manifest()

    assert validate_runtime_identity(manifest) == manifest

    tampered = json.loads(json.dumps(manifest))
    tampered["assistants"][0]["primary_model_id"] = "model-b"
    with pytest.raises(ValueError, match="fingerprint"):
        validate_runtime_identity(tampered)

    reordered = json.loads(json.dumps(manifest))
    reordered["assistants"].reverse()
    with pytest.raises(ValueError, match="roles"):
        validate_runtime_identity(reordered)


@pytest.mark.parametrize(
    ("status_code", "body", "expected_status"),
    [
        (200, _runtime_manifest(), "available"),
        (404, {"detail": "disabled"}, "disabled"),
        (503, {"detail": "unavailable"}, "unavailable"),
        (200, {"schema_version": 1}, "invalid"),
    ],
)
def test_fetch_runtime_identity_has_explicit_non_fallback_statuses(
    status_code,
    body,
    expected_status,
):
    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status_code, json=body)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_runtime_identity(
                client,
                "https://target.example/runtime/identity",
                timeout_s=1,
            )

    lookup = asyncio.run(run())

    assert lookup.status == expected_status
    if expected_status != "available":
        assert lookup.manifest is None


def test_eval_manifest_binds_dataset_settings_git_and_remote_identity(tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text('[{"id":"case-1"}]', encoding="utf-8")
    repository = tmp_path / "not-a-repository"
    repository.mkdir()
    lookup = RuntimeIdentityLookup(
        status="available",
        manifest=_runtime_manifest(),
    )

    manifest = build_eval_run_manifest(
        runner="online_agent_eval",
        dataset_path=dataset,
        settings={"timeout_s": 30, "api_token": "private-value"},
        runtime_identity=lookup,
        generated_at=datetime(2026, 7, 12, tzinfo=UTC),
        repository_root=repository,
    )

    assert manifest["dataset"] == {
        "name": "cases.json",
        "sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
    }
    assert manifest["runner_git"] == {"commit": None, "dirty": None}
    assert manifest["runtime_identity"]["manifest"] == _runtime_manifest()
    assert len(manifest["settings"]["fingerprint"]) == 64
    assert "private-value" not in str(manifest)
    assert manifest["generated_at"] == "2026-07-12T00:00:00+00:00"
    assert validate_eval_run_manifest(manifest) == manifest


def test_online_eval_settings_hashes_feedback_and_endpoint_hosts():
    args = SimpleNamespace(
        api_url="https://user:password@internal.example/chat?token=secret",
        approve_url=None,
        identity_url=None,
        timeout=30.0,
        limit=5,
        include_disabled=False,
        interrupt_policy="reject",
        max_interrupt_rounds=2,
        reject_feedback="private feedback",
        require_runtime_identity=True,
    )

    settings = online_eval_settings(args)
    serialized = json.dumps(settings)

    assert settings["api_endpoint"]["path"] == "/chat"
    assert settings["identity_endpoint"]["path"] == "/runtime/identity"
    assert settings["reject_feedback_sha256"] == hashlib.sha256(
        b"private feedback"
    ).hexdigest()
    assert "password" not in serialized
    assert "internal.example" not in serialized
    assert "private feedback" not in serialized


def test_identity_url_is_derived_from_the_actual_chat_prefix():
    assert identity_url_for("http://localhost:8000/chat", None) == (
        "http://localhost:8000/runtime/identity"
    )
    assert identity_url_for("http://localhost:8000/api/chat", None) == (
        "http://localhost:8000/api/runtime/identity"
    )
    assert identity_url_for(
        "https://user:password@target.example/api/chat?token=secret",
        None,
    ) == (
        "https://user:password@target.example/api/runtime/identity?token=secret"
    )
    assert identity_url_for("http://localhost:8000/chat", "http://meta/id") == (
        "http://meta/id"
    )


def test_approve_url_derivation_preserves_prefix_and_auth_query():
    assert approve_url_for("http://localhost:8000/chat", None) == (
        "http://localhost:8000/chat/approve"
    )
    assert approve_url_for("http://localhost:8000/api", None) == (
        "http://localhost:8000/api/chat/approve"
    )
    assert approve_url_for(
        "https://user:password@target.example/api/chat?token=secret",
        None,
    ) == (
        "https://user:password@target.example/api/chat/approve?token=secret"
    )


def test_offline_runner_settings_are_explicit_and_runtime_is_not_applicable():
    retrieval = retrieval_eval_settings(
        SimpleNamespace(
            mode="bm25",
            k=5,
            vector_top_k=8,
            limit=10,
            include_disabled=False,
        ),
        app_settings=Settings(
            HYBRID_RAG_BM25_TOP_K=9,
            HYBRID_RAG_VECTOR_TOP_K=7,
            HYBRID_RAG_RRF_K=61,
        ),
    )
    compaction = context_compaction_eval_settings(
        SimpleNamespace(
            limit=None,
            iterations=10,
            max_messages=12,
            max_serialized_bytes=0,
            keep_recent_turns=3,
            summary_max_chars=12_000,
        )
    )
    lookup = RuntimeIdentityLookup(status="not_applicable")

    assert retrieval == {
        "mode": "bm25",
        "top_k": 5,
        "bm25_top_k": 9,
        "vector_top_k": 8,
        "rrf_k": 61,
        "embedding": None,
        "limit": 10,
        "include_disabled": False,
    }
    assert compaction["answer_metric"] == "deterministic_marker_recall_proxy"
    assert compaction["token_metric"] == "langchain_count_tokens_approximately"
    assert lookup.to_payload() == {"status": "not_applicable"}


def test_retrieval_eval_settings_identify_effective_vector_configuration():
    args = SimpleNamespace(
        mode="hybrid",
        k=5,
        vector_top_k=None,
        limit=None,
        include_disabled=False,
    )
    settings = Settings(
        EMBEDDING_MODEL="embedding-model-a",
        EMBEDDING_API_BASE="https://user:password@embedding.example/v1?token=secret",
        HYBRID_RAG_BM25_TOP_K=9,
        HYBRID_RAG_VECTOR_TOP_K=11,
        HYBRID_RAG_RRF_K=63,
    )

    identity = retrieval_eval_settings(args, app_settings=settings)
    serialized = json.dumps(identity)

    assert identity["bm25_top_k"] == 9
    assert identity["vector_top_k"] == 11
    assert identity["rrf_k"] == 63
    assert identity["embedding"]["model_id"] == "embedding-model-a"
    assert identity["embedding"]["endpoint"]["path"] == "/v1"
    assert "password" not in serialized
    assert "embedding.example" not in serialized
    assert "secret" not in serialized
