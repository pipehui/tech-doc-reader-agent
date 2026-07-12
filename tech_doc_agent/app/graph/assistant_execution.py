from langchain_core.runnables import RunnableConfig, RunnableLambda

from tech_doc_agent.app.core.execution_budget import ExecutionBudgetExceeded

from .budgeting import WorkflowBudgetTracker
from .context_metrics import ContextMetricsTracker
from .message_scope import build_assistant_state
from .reflection import reflection_active_reset
from .state import State


def _prepare_assistant_call(
    state: State,
    config: RunnableConfig | None,
    assistant,
    *,
    scoped_messages: bool,
    budget_tracker: WorkflowBudgetTracker | None,
    context_tracker: ContextMetricsTracker | None,
):
    assistant_state = build_assistant_state(
        state,
        assistant.name,
        scoped_messages=scoped_messages,
    )
    context_snapshot = (
        context_tracker.snapshot(
            state,
            assistant_state,
            agent=assistant.name or "unknown",
            scope="scoped" if scoped_messages else "full",
        )
        if context_tracker is not None
        else None
    )
    current_usage = budget_tracker.current(state) if budget_tracker is not None else None
    before_llm_attempt = None
    if budget_tracker is not None and current_usage is not None:
        before_llm_attempt = lambda local_usages: budget_tracker.assert_before_llm_attempt(
            state,
            config,
            current=current_usage,
            local_usages=local_usages,
        )
    return assistant_state, context_snapshot, current_usage, before_llm_attempt


def _complete_assistant_call(
    state: State,
    config: RunnableConfig | None,
    update: dict,
    *,
    context_snapshot,
    current_usage,
    budget_tracker: WorkflowBudgetTracker | None,
    context_tracker: ContextMetricsTracker | None,
) -> dict:
    if context_tracker is not None and context_snapshot is not None:
        update = context_tracker.record_assistant(
            state,
            update,
            context_snapshot,
        )
    if budget_tracker is not None:
        update = budget_tracker.record_assistant(
            state,
            update,
            config=config,
            current=current_usage,
        )
    else:
        update.pop("_llm_usage", None)
        update.pop("_budget_decision", None)
    return _complete_reflection_state(state, update)


def _budget_stopped_update(error: ExecutionBudgetExceeded) -> dict:
    return {
        "_llm_usage": (),
        "_budget_decision": error.decision,
    }


def assistant_node(
    assistant,
    scoped_messages: bool = False,
    budget_tracker: WorkflowBudgetTracker | None = None,
    context_tracker: ContextMetricsTracker | None = None,
):
    def invoke(state: State, config: RunnableConfig | None = None):
        assistant_state, context_snapshot, current_usage, before_llm_attempt = (
            _prepare_assistant_call(
                state,
                config,
                assistant,
                scoped_messages=scoped_messages,
                budget_tracker=budget_tracker,
                context_tracker=context_tracker,
            )
        )
        try:
            update = assistant(
                assistant_state,
                config,
                before_llm_attempt=before_llm_attempt,
            )
        except ExecutionBudgetExceeded as exc:
            update = _budget_stopped_update(exc)
        return _complete_assistant_call(
            state,
            config,
            update,
            context_snapshot=context_snapshot,
            current_usage=current_usage,
            budget_tracker=budget_tracker,
            context_tracker=context_tracker,
        )

    async def ainvoke(state: State, config: RunnableConfig | None = None):
        assistant_state, context_snapshot, current_usage, before_llm_attempt = (
            _prepare_assistant_call(
                state,
                config,
                assistant,
                scoped_messages=scoped_messages,
                budget_tracker=budget_tracker,
                context_tracker=context_tracker,
            )
        )
        try:
            result = await assistant.ainvoke(
                assistant_state,
                config,
                before_llm_attempt=before_llm_attempt,
            )
        except ExecutionBudgetExceeded as exc:
            result = _budget_stopped_update(exc)
        return _complete_assistant_call(
            state,
            config,
            result,
            context_snapshot=context_snapshot,
            current_usage=current_usage,
            budget_tracker=budget_tracker,
            context_tracker=context_tracker,
        )

    return RunnableLambda(invoke, afunc=ainvoke, name=assistant.name)


def _complete_reflection_state(state: State, assistant_update: dict) -> dict:
    if state.get("reflection_status") not in {"repairing", "finalizing", "terminal"}:
        return assistant_update

    result = assistant_update.get("messages")
    last_message = result[-1] if isinstance(result, list) and result else result
    if getattr(last_message, "tool_calls", None):
        return assistant_update
    return {
        **assistant_update,
        **reflection_active_reset(),
    }


__all__ = ["assistant_node"]
