import json

from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.infrastructure.retrieval import HybridRetriever
from tech_doc_agent.app.tools import ToolDependencies, build_tool_bundle


class FakeFaissStore:
    def __init__(self):
        self.documents = [
            {
                "id": 1,
                "title": "LangGraph StateGraph",
                "content": "StateGraph seed content",
                "source": "test",
                "metadata": {"category": "langgraph_core", "tags": ["stategraph"]},
            },
            {
                "id": 2,
                "title": "FastAPI Depends",
                "content": "Depends seed content",
                "source": "test",
                "metadata": {"category": "fastapi", "tags": ["depends"]},
            }
        ]

    def search_related(self, query: str, k: int = 3):
        return []

    def add_documents(self, docs: list[dict]):
        self.added_docs = docs
        return {"added_documents": len(docs), "added_chunks": len(docs)}

    def save(self):
        self.saved = True
        return True


class FakeHybridRetriever:
    def __init__(self):
        self.refreshed = False

    def refresh(self):
        self.refreshed = True


def _document_tools(store, retriever=None):
    settings = Settings()
    dependencies = ToolDependencies(
        document_store=store,
        document_retriever=retriever or HybridRetriever(store, settings=settings),
        learning_store=None,
        memory_store=None,
        learning_state_service=None,
        profile_service=None,
        web_search=None,
    )
    return build_tool_bundle(dependencies)


def test_read_docs_returns_seed_document_for_exact_topic():
    tools = _document_tools(FakeFaissStore())
    raw = tools.read_docs.invoke({"query": "LangGraph StateGraph"})

    documents = json.loads(raw)

    assert documents
    assert documents[0]["title"] == "LangGraph StateGraph"
    assert documents[0]["match_type"]
    assert documents[0]["score"] > 0


def test_read_docs_applies_category_filter():
    tools = _document_tools(FakeFaissStore())
    raw = tools.read_docs.invoke({"query": "seed content", "category": "fastapi"})

    documents = json.loads(raw)

    assert [item["title"] for item in documents] == ["FastAPI Depends"]
    assert documents[0]["metadata"]["category"] == "fastapi"


def test_read_docs_uses_shared_corpus_across_trace_context_tenants():
    store = FakeFaissStore()
    store.documents = [
        {
            "id": 1,
            "title": "Tenant A Secret",
            "content": "shared keyword",
            "source": "test",
            "metadata": {"user_id": "user-a", "namespace": "tenant-docs"},
        },
        {
            "id": 2,
            "title": "Tenant B Secret",
            "content": "shared keyword",
            "source": "test",
            "metadata": {"user_id": "user-b", "namespace": "tenant-docs"},
        },
    ]
    tools = _document_tools(store)
    with trace_context(user_id="user-a", namespace="tenant-docs"):
        raw = tools.read_docs.invoke({"query": "shared keyword"})

    documents = json.loads(raw)

    assert [item["title"] for item in documents] == ["Tenant A Secret", "Tenant B Secret"]


def test_save_docs_writes_shared_document_ignoring_trace_context_tenant():
    store = FakeFaissStore()
    retriever = FakeHybridRetriever()
    tools = _document_tools(store, retriever)
    with trace_context(user_id="user-a", namespace="tenant-docs"):
        result = tools.save_docs.invoke(
            {
                "title": "Tenant Doc",
                "content": "content",
            }
        )

    assert "Tenant Doc" in result
    assert "user_id" not in store.added_docs[0]
    assert "namespace" not in store.added_docs[0]
    assert store.saved is True
    assert retriever.refreshed is True
