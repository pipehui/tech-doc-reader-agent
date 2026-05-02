from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.tenant import (
    current_tenant,
    session_id_from_config,
    tenant_from_config,
    tenant_from_values,
    tenant_thread_id,
)


def test_tenant_defaults_match_existing_document_store_namespace():
    tenant = tenant_from_values()

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


def test_session_id_from_config_prefers_metadata_then_context_var():
    config = {"metadata": {"session_id": "cfg-session"}}
    assert session_id_from_config(config) == "cfg-session"

    with trace_context(session_id="ctx-session"):
        assert session_id_from_config(None) == "ctx-session"
        assert session_id_from_config({"metadata": {}}) == "ctx-session"

    assert session_id_from_config(None) is None
    assert session_id_from_config({"metadata": {"session_id": "  "}}) is None
