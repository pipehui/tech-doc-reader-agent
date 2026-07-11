from langgraph.checkpoint.memory import MemorySaver

from tech_doc_agent.app.graph import build_multi_agentic_graph


def test_graph_compiles_with_memory_checkpointer(graph_spec):
    graph = build_multi_agentic_graph(MemorySaver(), graph_spec)

    assert graph is not None
    assert hasattr(graph, "astream")
