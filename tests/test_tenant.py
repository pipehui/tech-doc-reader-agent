import pytest

from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.tenant import (
    TenantContext,
    current_tenant,
    normalize_tenant,
    parse_tenant,
    session_id_from_config,
    tenant_from_config,
    tenant_thread_id,
)
from tech_doc_agent.app.runtime.config import SessionConfigFactory


def test_tenant_defaults_match_existing_document_store_namespace():
    tenant = parse_tenant()

    assert tenant.user_id == "default"
    assert tenant.namespace == "tech_docs"
    assert tenant_thread_id("session-1", tenant) == "default:tech_docs:session-1"


def test_current_tenant_prefers_trace_context_over_fallback_values():
    with trace_context(user_id="user-a", namespace="tenant-docs"):
        tenant = current_tenant(
            fallback_user_id="tool-user",
            fallback_namespace="tool-namespace",
        )

    assert tenant.user_id == "user-a"
    assert tenant.namespace == "tenant-docs"


def test_tenant_from_config_prefers_metadata_over_context_var():
    config = {"metadata": {"user_id": "config-user", "namespace": "config-ns"}}
    with trace_context(user_id="ctx-user", namespace="ctx-ns"):
        tenant = tenant_from_config(config)

    assert tenant.user_id == "config-user"
    assert tenant.namespace == "config-ns"


def test_tenant_from_config_falls_back_to_context_var_when_metadata_missing():
    with trace_context(user_id="ctx-user", namespace="ctx-ns"):
        tenant = tenant_from_config({"metadata": {}})

    assert tenant.user_id == "ctx-user"
    assert tenant.namespace == "ctx-ns"


def test_tenant_from_config_handles_none_and_invalid_shapes():
    assert tenant_from_config(None).user_id == "default"
    assert tenant_from_config({}).user_id == "default"
    assert tenant_from_config({"metadata": "not-a-dict"}).user_id == "default"


@pytest.mark.parametrize(
    ("user_id", "namespace"),
    [
        ("", "tenant-docs"),
        (" user-a", "tenant-docs"),
        ("../user-a", "tenant-docs"),
        ("user-a", "docs/private"),
        (123, "tenant-docs"),
    ],
)
def test_parse_tenant_rejects_explicit_invalid_values(user_id, namespace):
    with pytest.raises(ValidationError) as raised:
        parse_tenant(user_id, namespace)

    assert raised.value.code == "invalid_tenant"
    assert raised.value.dependency == "tenant_context"


def test_tenant_context_itself_cannot_bypass_validation():
    with pytest.raises(ValidationError):
        TenantContext(user_id="../user", namespace="tenant-docs")


def test_normalize_tenant_is_an_explicit_legacy_fallback():
    tenant = normalize_tenant(" ../legacy-user ", "docs/private")

    assert tenant.user_id == "default"
    assert tenant.namespace == "tech_docs"
    assert normalize_tenant(123, " tenant-docs ") == TenantContext(
        user_id="123",
        namespace="tenant-docs",
    )


def test_invalid_config_metadata_does_not_fall_back_to_valid_context():
    with trace_context(user_id="context-user", namespace="context-ns"):
        with pytest.raises(ValidationError):
            tenant_from_config(
                {
                    "metadata": {
                        "user_id": "../invalid",
                        "namespace": "tenant-docs",
                    }
                }
            )


def test_runtime_config_rejects_invalid_internal_tenant_before_thread_key():
    factory = SessionConfigFactory(Settings())

    with pytest.raises(ValidationError):
        factory.build(
            "session-1",
            user_id="../invalid",
            namespace="tenant-docs",
        )

    with trace_context(user_id="../invalid", namespace="tenant-docs"):
        with pytest.raises(ValidationError):
            factory.build("session-1", user_id="fallback-user")


def test_session_id_from_config_prefers_metadata_then_context_var():
    config = {"metadata": {"session_id": "cfg-session"}}
    assert session_id_from_config(config) == "cfg-session"

    with trace_context(session_id="ctx-session"):
        assert session_id_from_config(None) == "ctx-session"
        assert session_id_from_config({"metadata": {}}) == "ctx-session"

    assert session_id_from_config(None) is None
    assert session_id_from_config({"metadata": {"session_id": "  "}}) is None
