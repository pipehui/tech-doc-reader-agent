from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.vectordb.memory_store_backend import MemoryStore


def test_memory_store_isolates_memories_by_tenant(tmp_path):
    store = MemoryStore(settings=Settings(DATA_PATH=str(tmp_path)))

    store.upsert_memory(
        kind="stuck_point",
        topic="LangGraph Reducer",
        content="用户容易把 reducer 当成普通覆盖更新。",
        user_id="user-a",
        namespace="tenant-docs",
    )
    store.upsert_memory(
        kind="review_hint",
        topic="LangGraph Reducer",
        content="复习 reducer 时先看 add_messages。",
        user_id="user-b",
        namespace="tenant-docs",
    )

    user_a_memories = store.read_by_query(
        "reducer",
        user_id="user-a",
        namespace="tenant-docs",
    )

    assert len(user_a_memories) == 1
    assert user_a_memories[0]["user_id"] == "user-a"
    assert user_a_memories[0]["kind"] == "stuck_point"


def test_memory_store_matches_natural_language_query(tmp_path):
    store = MemoryStore(settings=Settings(DATA_PATH=str(tmp_path)))
    store.upsert_memory(
        kind="learned",
        topic="Vector Distance",
        content="用户理解了向量距离、余弦相似度和 L2 distance 的区别。",
    )

    english_result = store.read_by_query("关于 Vector Distance 的学习轨迹")
    chinese_result = store.read_by_query("向量距离掌握情况")

    assert english_result[0]["topic"] == "Vector Distance"
    assert chinese_result[0]["topic"] == "Vector Distance"


def test_memory_store_upserts_same_kind_and_topic(tmp_path):
    store = MemoryStore(settings=Settings(DATA_PATH=str(tmp_path)))

    first = store.upsert_memory(
        kind="misconception",
        topic="LangGraph Checkpoint",
        content="用户混淆 checkpoint 和普通缓存。",
    )
    second = store.upsert_memory(
        kind="misconception",
        topic="LangGraph Checkpoint",
        content="用户已经纠正 checkpoint 和普通缓存的区别。",
        confidence=0.9,
    )

    assert first["id"] == second["id"]
    assert len(store.memories) == 1
    assert store.memories[0]["content"] == "用户已经纠正 checkpoint 和普通缓存的区别。"
    assert store.memories[0]["confidence"] == 0.9


def test_memory_store_persists_to_json(tmp_path):
    store = MemoryStore(settings=Settings(DATA_PATH=str(tmp_path)))
    store.upsert_memory(
        kind="learned",
        topic="FastAPI Depends",
        content="用户理解了 Depends 的依赖注入作用。",
    )
    assert store.save()

    reloaded = MemoryStore(settings=Settings(DATA_PATH=str(tmp_path)))
    assert reloaded.load()

    assert reloaded.read_by_query("Depends")[0]["topic"] == "FastAPI Depends"
