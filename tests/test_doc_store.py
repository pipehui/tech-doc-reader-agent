import json
from types import SimpleNamespace

from tech_doc_agent.app.services.tools.doc_store import read_docs
from tech_doc_agent.app.services.resources import override_app_resources


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


def test_read_docs_returns_seed_document_for_exact_topic():
    resources = SimpleNamespace(
        faiss_store=FakeFaissStore(),
        learning_store=None,
        web_search_backend=None,
    )

    with override_app_resources(resources):
        raw = read_docs.invoke({"query": "LangGraph StateGraph"})

    documents = json.loads(raw)

    assert documents
    assert documents[0]["title"] == "LangGraph StateGraph"
    assert documents[0]["match_type"]
    assert documents[0]["score"] > 0


def test_read_docs_applies_category_filter():
    resources = SimpleNamespace(
        faiss_store=FakeFaissStore(),
        learning_store=None,
        web_search_backend=None,
    )

    with override_app_resources(resources):
        raw = read_docs.invoke({"query": "seed content", "category": "fastapi"})

    documents = json.loads(raw)

    assert [item["title"] for item in documents] == ["FastAPI Depends"]
    assert documents[0]["metadata"]["category"] == "fastapi"
