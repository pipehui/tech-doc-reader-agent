from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.tenant import (
    current_tenant,
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
