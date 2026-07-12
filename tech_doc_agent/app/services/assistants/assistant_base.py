from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable, RunnableConfig

from tech_doc_agent.app.core.errors import classify_error
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.retry import RetryExecutor
from tech_doc_agent.app.core.state import State


def is_empty_assistant_output(result) -> bool:
    if getattr(result, "tool_calls", None):
        return False

    content = getattr(result, "content", None)
    if not content:
        return True

    if isinstance(content, list):
        if not content:
            return True

        return not any(isinstance(item, dict) and str(item.get("text", "")).strip() for item in content)

    if isinstance(content, str):
        return not content.strip()

    return False


class Assistant:
    def __init__(
        self,
        runnable: Runnable,
        name: str | None = None,
        max_empty_response_retries: int = 3,
        retry_executor: RetryExecutor | None = None,
    ):
        if max_empty_response_retries < 0:
            raise ValueError("max_empty_response_retries must be greater than or equal to 0")

        self.runnable = runnable
        self.name = name
        self.max_empty_response_retries = max_empty_response_retries
        self.retry_executor = retry_executor

    def _name_result(self, result):
        if self.name and not getattr(result, "name", None):
            if hasattr(result, "model_copy"):
                return result.model_copy(update={"name": self.name})
            return result.copy(update={"name": self.name})
        return result

    def __call__(self, state: State, config: Optional[RunnableConfig] = None):
        result = None
        assistant_name = self.name or "unknown"

        for attempt in range(self.max_empty_response_retries + 1):
            result = self._invoke_transport(state, config, assistant_name=assistant_name)

            if is_empty_assistant_output(result):
                log_event(
                    "assistant.empty_response",
                    assistant=assistant_name,
                    attempt=attempt + 1,
                    max_attempts=self.max_empty_response_retries + 1,
                )
                messages = state["messages"] + [HumanMessage(content="Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
        else:
            log_event(
                "assistant.empty_response.exhausted",
                assistant=assistant_name,
                max_attempts=self.max_empty_response_retries + 1,
            )
            raise RuntimeError(
                "Assistant "
                f"{assistant_name} returned empty output after "
                f"{self.max_empty_response_retries + 1} attempts."
            )

        result = self._name_result(result)
        return {"messages": result}

    async def ainvoke(self, state: State, config: Optional[RunnableConfig] = None):
        result = None
        assistant_name = self.name or "unknown"

        for attempt in range(self.max_empty_response_retries + 1):
            result = await self._ainvoke_transport(state, config, assistant_name=assistant_name)

            if is_empty_assistant_output(result):
                log_event(
                    "assistant.empty_response",
                    assistant=assistant_name,
                    attempt=attempt + 1,
                    max_attempts=self.max_empty_response_retries + 1,
                    async_runtime=True,
                )
                messages = state["messages"] + [HumanMessage(content="Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
        else:
            log_event(
                "assistant.empty_response.exhausted",
                assistant=assistant_name,
                max_attempts=self.max_empty_response_retries + 1,
                async_runtime=True,
            )
            raise RuntimeError(
                "Assistant "
                f"{assistant_name} returned empty output after "
                f"{self.max_empty_response_retries + 1} attempts."
            )

        result = self._name_result(result)
        return {"messages": result}

    def _invoke_transport(
        self,
        state: State,
        config: Optional[RunnableConfig],
        *,
        assistant_name: str,
    ):
        if self.retry_executor is not None:
            return self.retry_executor.run(
                lambda: self.runnable.invoke(state, config),
                operation_name=f"assistant.{assistant_name}.llm",
                dependency="llm",
                idempotent=True,
            )

        try:
            return self.runnable.invoke(state, config)
        except Exception as exc:
            raise classify_error(exc, dependency="llm") from exc

    async def _ainvoke_transport(
        self,
        state: State,
        config: Optional[RunnableConfig],
        *,
        assistant_name: str,
    ):
        if self.retry_executor is not None:
            return await self.retry_executor.arun(
                lambda: self.runnable.ainvoke(state, config),
                operation_name=f"assistant.{assistant_name}.llm",
                dependency="llm",
                idempotent=True,
            )

        try:
            return await self.runnable.ainvoke(state, config)
        except Exception as exc:
            raise classify_error(exc, dependency="llm") from exc
