import json
from types import SimpleNamespace

from tech_doc_agent.app.application.learning_models import LearningRecord, MemoryFragment
from tech_doc_agent.app.application.profile_models import (
    UserProfile,
    UserProfileUpdate,
)
from tech_doc_agent.app.application.learning_commands import UpdateLearningStateResult
from tech_doc_agent.app.application.retrieval import SearchQuery, SearchResult
from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.tenant import TenantContext
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

    def retrieve(self, request: SearchQuery) -> list[SearchResult]:
        self.calls.append(request)
        return [
            SearchResult(
                doc_id=self.label,
                title=self.label,
                content=request.query,
                source="test",
                metadata={},
                match_types=("bm25",),
                score=1.0,
                signals={"bm25": {"rank": 1, "score": 1.0}},
            )
        ]

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

    def query_records(self, query, *, user_id, namespace):
        return [
            LearningRecord.create(
                knowledge=query,
                timestamp="2026-07-12T00:00:00Z",
                score=0.5,
                tenant=TenantContext(user_id, namespace),
            )
        ]

    def list_records(self, *, user_id, namespace):
        return self.query_records("overview", user_id=user_id, namespace=namespace)

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

    def query_memories(self, query, *, user_id, namespace, limit):
        return [
            MemoryFragment.create(
                kind="learned",
                topic=query,
                content=namespace,
                confidence=0.7,
                source_session_id=None,
                tenant=TenantContext(user_id, namespace),
                timestamp="2026-07-12T00:00:00Z",
                memory_id="memory-query",
            )
        ][:limit]

    def recent_memories(self, *, user_id, namespace, limit):
        return self.query_memories(
            "recent",
            user_id=user_id,
            namespace=namespace,
            limit=limit,
        )

    def upsert_memory(self, **values):
        memory = {"id": f"memory-{len(self.memories) + 1}", **values}
        self.memories.append(memory)
        return memory

    def save(self):
        self.save_calls += 1


class FakeLearningStateService:
    def __init__(self, learning_store, memory_store):
        self.learning_store = learning_store
        self.memory_store = memory_store

    def update(self, command):
        learning_message = self.learning_store.upsert_record(
            command.knowledge,
            command.timestamp,
            command.score,
            user_id=command.tenant.user_id,
            namespace=command.tenant.namespace,
        )
        self.learning_store.save()
        return UpdateLearningStateResult(
            learning_message,
            "No memory fragment written.",
        )


class FakeWebSearch:
    def __init__(self, label):
        self.label = label

    def search(self, query):
        return [{"title": self.label, "query": query}]


class FakeProfileService:
    def get_profile(self, *, user_id, namespace):
        return UserProfile.default(TenantContext(user_id, namespace))

    def update_profile(self, *, user_id, namespace, **updates):
        profile = UserProfile.default(TenantContext(user_id, namespace))
        return profile.apply(
            UserProfileUpdate.create(**updates),
            timestamp="2026-07-12T00:00:00Z",
        )


def _dependencies(tmp_path, label):
    learning_store = FakeLearningStore()
    memory_store = FakeMemoryStore()
    return ToolDependencies(
        document_store=FakeDocumentStore(),
        document_retriever=FakeRetriever(label),
        learning_store=learning_store,
        memory_store=memory_store,
        learning_state_service=FakeLearningStateService(
            learning_store,
            memory_store,
        ),
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
        SearchQuery(query="StateGraph"),
    ]
    assert dependencies_b.document_retriever.calls == [
        SearchQuery(query="StateGraph"),
    ]


def test_related_document_tool_builds_typed_vector_query_at_boundary(tmp_path):
    dependencies = _dependencies(tmp_path, "related")
    tools = build_tool_bundle(dependencies)

    result = json.loads(
        tools.search_related_docs.invoke(
            {
                "query": "StateGraph",
                "k": 3,
                "category": "langgraph_core",
            }
        )
    )

    assert result[0]["title"] == "related"
    assert dependencies.document_retriever.calls == [
        SearchQuery(
            query="StateGraph",
            top_k=3,
            mode="vector",
            filters={"category": "langgraph_core"},
        )
    ]


def test_document_tool_leaves_taxonomy_normalization_to_retriever(tmp_path):
    dependencies = _dependencies(tmp_path, "raw-filter")
    tools = build_tool_bundle(dependencies)

    tools.read_docs.invoke(
        {
            "query": "hybrid retrieval",
            "category": "RAG",
            "tags": ["Hybrid Search"],
        }
    )

    assert dependencies.document_retriever.calls == [
        SearchQuery(
            query="hybrid retrieval",
            filters={
                "category": "RAG",
                "tags": ["Hybrid Search"],
            },
        )
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
                "name": "upsert_learning_history",
                "id": "call-learning",
                "type": "tool_call",
                "args": {
                    "knowledge": "LangGraph",
                    "timestamp": "2026-07-11T00:00:00Z",
                    "score": 0.8,
                },
            },
            config={
                "metadata": {
                    **config["metadata"],
                    "session_id": "session-learning",
                }
            },
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
        learning_state_service=dependencies.learning_state_service,
        profile_service=dependencies.profile_service,
        web_search_backend=dependencies.web_search,
    )

    adapted = ToolDependencies.from_container(resources)

    assert adapted == dependencies
