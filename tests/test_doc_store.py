import json

from tech_doc_agent.app.services.tools.doc_store import read_docs


def test_read_docs_returns_seed_document_for_exact_topic():
    raw = read_docs.invoke({"query": "LangGraph StateGraph"})
    documents = json.loads(raw)

    assert documents
    assert documents[0]["title"] == "LangGraph StateGraph"
