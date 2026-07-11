from .state import State


def extract_last_message_text(state: State) -> str:
    last_message = state["messages"][-1]
    content = last_message.content

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()

    return str(content)
