from tech_doc_agent.app.application.retrieval import (
    DocumentRetrieverPort,
    SearchQuery,
    SearchResult,
)
from tech_doc_agent.app.services import retrieval as compatibility_retrieval


def test_service_facade_reexports_the_single_application_contract_types():
    assert compatibility_retrieval.SearchQuery is SearchQuery
    assert compatibility_retrieval.SearchResult is SearchResult


def test_document_retriever_port_accepts_application_contracts():
    class FakeRetriever:
        def __init__(self):
            self.requests = []

        def retrieve(self, request: SearchQuery) -> list[SearchResult]:
            self.requests.append(request)
            return []

        def refresh(self) -> None:
            pass

    fake = FakeRetriever()
    retriever: DocumentRetrieverPort = fake
    request = SearchQuery(query="StateGraph", filters={"category": "langgraph_core"})

    assert retriever.retrieve(request) == []
    assert fake.requests == [request]
