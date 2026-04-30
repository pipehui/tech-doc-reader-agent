from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.vectordb.learning_store_backend import LearningStore


def test_learning_store_isolates_records_by_tenant(tmp_path):
    store = LearningStore(settings=Settings(DATA_PATH=str(tmp_path)))

    store.upsert_record(
        "LangGraph StateGraph",
        "2026-04-28T00:00:00Z",
        0.9,
        user_id="user-a",
        namespace="tenant-docs",
    )
    store.upsert_record(
        "LangGraph StateGraph",
        "2026-04-29T00:00:00Z",
        0.5,
        user_id="user-b",
        namespace="tenant-docs",
    )

    user_a_records = store.read_by_query(
        "LangGraph",
        user_id="user-a",
        namespace="tenant-docs",
    )
    user_b_records = store.read_overview(
        user_id="user-b",
        namespace="tenant-docs",
    )

    assert len(user_a_records) == 1
    assert user_a_records[0]["score"] == 0.9
    assert user_a_records[0]["user_id"] == "user-a"
    assert len(user_b_records) == 1
    assert user_b_records[0]["score"] == 0.5


def test_learning_store_matches_natural_language_query(tmp_path):
    store = LearningStore(settings=Settings(DATA_PATH=str(tmp_path)))

    store.upsert_record("LangGraph StateGraph", "2026-04-28T00:00:00Z", 0.9)
    store.upsert_record("FastAPI Depends", "2026-04-28T00:00:00Z", 0.8)

    records = store.read_by_query("关于 StateGraph 的学习记录")

    assert [record["knowledge"] for record in records] == ["LangGraph StateGraph"]


def test_learning_store_backfills_default_tenant_for_old_records(tmp_path):
    store = LearningStore(settings=Settings(DATA_PATH=str(tmp_path)))
    store.records = [
        {
            "knowledge": "FastAPI Depends",
            "timestamp": "2026-04-28T00:00:00Z",
            "score": 0.8,
        }
    ]

    records = store.read_overview()

    assert records[0]["user_id"] == "default"
    assert records[0]["namespace"] == "tech_docs"
    assert records[0]["reviewtimes"] == 0
    assert store.read_overview(user_id="user-a", namespace="tech_docs") == []

    store.upsert_record("FastAPI Depends", "2026-04-29T00:00:00Z", 0.9)

    assert store.records[0]["reviewtimes"] == 1
    assert store.records[0]["score"] == 0.9
