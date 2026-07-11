from langchain_core.runnables import RunnableConfig, RunnableLambda

from tech_doc_agent.app.core.tenant import tenant_from_values
from tech_doc_agent.app.services.message_scope import build_scoped_state
from tech_doc_agent.app.services.user_profile import get_user_context_summary

from .state import State


def assistant_node(assistant, scoped_messages: bool = False):
    def invoke(state: State, config: RunnableConfig | None = None):
        assistant_state = build_scoped_state(state, assistant.name) if scoped_messages else state
        return assistant(assistant_state, config)

    async def ainvoke(state: State, config: RunnableConfig | None = None):
        assistant_state = build_scoped_state(state, assistant.name) if scoped_messages else state
        return await assistant.ainvoke(assistant_state, config)

    return RunnableLambda(invoke, afunc=ainvoke, name=assistant.name)


def user_info(state: State, config: RunnableConfig):
    metadata = (config or {}).get("metadata", {}) if isinstance(config, dict) else {}
    tenant = tenant_from_values(
        state.get("user_id") or metadata.get("user_id"),
        state.get("namespace") or metadata.get("namespace"),
    )
    info_str = get_user_context_summary(
        user_id=tenant.user_id,
        namespace=tenant.namespace,
        memory_query=state.get("learning_target", ""),
    )
    update = {
        "user_info": info_str,
        "user_id": tenant.user_id,
        "namespace": tenant.namespace,
        "learning_target": state.get("learning_target", ""),
    }

    if state.get("examination_context") and not _last_ai_was_examination(state):
        update["examination_context"] = ""

    return update


def _last_ai_was_examination(state: State) -> bool:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", None) == "ai":
            return getattr(message, "name", None) == "examination"
    return False
