AGENT_NODE_NAMES = {
    "primary",
    "primary_assistant",
    "parser",
    "relation",
    "explanation",
    "examination",
    "summary",
}


def infer_agent_from_metadata(metadata: dict) -> str | None:
    node_name = metadata.get("langgraph_node")
    if node_name:
        return node_name

    for key in ("langgraph_checkpoint_ns", "checkpoint_ns"):
        checkpoint_ns = metadata.get(key)
        if isinstance(checkpoint_ns, str) and checkpoint_ns:
            candidate = checkpoint_ns.split(":", 1)[0]
            if candidate in AGENT_NODE_NAMES:
                return candidate

    path = metadata.get("langgraph_path")
    if isinstance(path, list):
        for item in reversed(path):
            if isinstance(item, str) and item in AGENT_NODE_NAMES:
                return item
            if isinstance(item, (list, tuple)):
                for part in reversed(item):
                    if isinstance(part, str) and part in AGENT_NODE_NAMES:
                        return part

    return None


__all__ = ["AGENT_NODE_NAMES", "infer_agent_from_metadata"]
