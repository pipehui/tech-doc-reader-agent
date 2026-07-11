import json
from types import SimpleNamespace

from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.tools import ToolDependencies, build_tool_bundle


class FakeDocumentStore:
    def __init__(self):
        self.documents = []
        self.save_calls = 0

    def add_documents(self, documents):
        self.documents.extend(documents)
        return {"added_chunks": len(documents)}

    def save(self):
        self.save_calls += 1


class FakeRetriever:
    def __init__(self, label):
        self.label = label
        self.calls = []
        self.refresh_calls = 0

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return [{"title": self.label, "content": query}]

    def refresh(self):
        self.refresh_calls += 1


class FakeLearningStore:
    def __init__(self):
        self.records = []
        self.save_calls = 0

    def read_by_query(self, query, *, user_id, namespace):
        return [{"knowledge": query, "user_id": user_id, "namespace": namespace}]

    def read_overview(self, *, user_id, namespace):
        return [{"knowledge": "overview", "user_id": user_id, "namespace": namespace}]

    def upsert_record(self, knowledge, timestamp, score, *, user_id, namespace):
        self.records.append(
            {
                "knowledge": knowledge,
                "timestamp": timestamp,
                "score": score,
                "user_id": user_id,
                "namespace": namespace,
            }
        )
        return "saved"

    def save(self):
        self.save_calls += 1


class FakeMemoryStore:
    def __init__(self):
        self.memories = []
        self.save_calls = 0

    def read_by_query(self, query, *, user_id, namespace, limit):
        return [
            {
                "kind": "learned",
                "topic": query,
                "content": namespace,
                "user_id": user_id,
            }
        ][:limit]

    def read_recent(self, *, user_id, namespace, limit):
        return self.read_by_query("recent", user_id=user_id, namespace=namespace, limit=limit)

    def upsert_memory(self, **values):
        memory = {"id": f"memory-{len(self.memories) + 1}", **values}
        self.memories.append(memory)
        return memory

    def save(self):
        self.save_calls += 1


class FakeWebSearch:
    def __init__(self, label):
        self.label = label

    def search(self, query):
        return [{"title": self.label, "query": query}]


class FakeProfileService:
    def get_profile(self, *, user_id, namespace):
        return {"user_id": user_id, "namespace": namespace}

    def update_profile(self, *, user_id, namespace, **updates):
        return {"user_id": user_id, "namespace": namespace, **updates}


def _dependencies(tmp_path, label):
    return ToolDependencies(
        document_store=FakeDocumentStore(),
        document_retriever=FakeRetriever(label),
        learning_store=FakeLearningStore(),
        memory_store=FakeMemoryStore(),
        profile_service=FakeProfileService(),
        web_search=FakeWebSearch(label),
    )


def test_tool_bundle_has_stable_unique_names(tmp_path):
    bundle = build_tool_bundle(_dependencies(tmp_path, "one"))

    assert bundle.names() == (
        "web_search",
        "read_docs",
        "save_docs",
        "search_related_docs",
        "read_learning_history",
        "read_all_learning_history",
        "read_user_memory",
        "upsert_learning_history",
        "upsert_learning_state",
        "read_user_profile",
        "update_user_profile",
    )
    assert len(set(bundle.names())) == len(bundle.names())


def test_tool_bundles_keep_resource_instances_isolated(tmp_path):
    dependencies_a = _dependencies(tmp_path, "tenant-a")
    dependencies_b = _dependencies(tmp_path, "tenant-b")
    tools_a = build_tool_bundle(dependencies_a)
    tools_b = build_tool_bundle(dependencies_b)

    result_a = json.loads(tools_a.read_docs.invoke({"query": "StateGraph"}))
    result_b = json.loads(tools_b.read_docs.invoke({"query": "StateGraph"}))

    assert result_a[0]["title"] == "tenant-a"
    assert result_b[0]["title"] == "tenant-b"
    assert dependencies_a.document_retriever.calls == [
        ("StateGraph", {"filters": {}}),
    ]
    assert dependencies_b.document_retriever.calls == [
        ("StateGraph", {"filters": {}}),
    ]


def test_bound_document_tools_write_and_refresh_the_same_dependencies(tmp_path):
    dependencies = _dependencies(tmp_path, "documents")
    tools = build_tool_bundle(dependencies)

    message = tools.save_docs.invoke(
        {
            "title": "RAG",
            "content": "Retrieval augmented generation",
            "category": "llm_engineering",
            "tags": ["rag"],
        }
    )

    assert "Added 1 chunks" in message
    assert dependencies.document_store.documents[0]["title"] == "RAG"
    assert dependencies.document_store.save_calls == 1
    assert dependencies.document_retriever.refresh_calls == 1


def test_learning_tools_use_runnable_config_tenant_without_global_resources(tmp_path):
    dependencies = _dependencies(tmp_path, "learning")
    tools = build_tool_bundle(dependencies)
    config = {"metadata": {"user_id": "config-user", "namespace": "config-ns"}}

    with trace_context(user_id="context-user", namespace="context-ns"):
        tools.upsert_learning_history.invoke(
            {
                "knowledge": "LangGraph",
                "timestamp": "2026-07-11T00:00:00Z",
                "score": 0.8,
            },
            config=config,
        )

    assert dependencies.learning_store.records == [
        {
            "knowledge": "LangGraph",
            "timestamp": "2026-07-11T00:00:00Z",
            "score": 0.8,
            "user_id": "config-user",
            "namespace": "config-ns",
        }
    ]


def test_tool_dependencies_can_be_adapted_from_resource_container(tmp_path):
    dependencies = _dependencies(tmp_path, "container")
    resources = SimpleNamespace(
        faiss_store=dependencies.document_store,
        hybrid_retriever=dependencies.document_retriever,
        learning_store=dependencies.learning_store,
        memory_store=dependencies.memory_store,
        profile_service=dependencies.profile_service,
        web_search_backend=dependencies.web_search,
    )

    adapted = ToolDependencies.from_container(resources)

    assert adapted == dependencies
