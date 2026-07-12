from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable, RunnableConfig

from tech_doc_agent.app.core.budget import LlmUsage
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
        default_provider: str = "openai_compatible",
    ):
        if max_empty_response_retries < 0:
            raise ValueError("max_empty_response_retries must be greater than or equal to 0")

        self.runnable = runnable
        self.name = name
        self.max_empty_response_retries = max_empty_response_retries
        self.retry_executor = retry_executor
        self.default_provider = default_provider

    def _name_result(self, result):
        if self.name and not getattr(result, "name", None):
            if hasattr(result, "model_copy"):
                return result.model_copy(update={"name": self.name})
            return result.copy(update={"name": self.name})
        return result

    def __call__(self, state: State, config: Optional[RunnableConfig] = None):
        result = None
        assistant_name = self.name or "unknown"
        llm_usage: list[LlmUsage] = []

        for attempt in range(self.max_empty_response_retries + 1):
            result, transport_attempts = self._invoke_transport(
                state,
                config,
                assistant_name=assistant_name,
            )
            llm_usage.extend(self._usage_for_result(result, transport_attempts))

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
        return {"messages": result, "_llm_usage": tuple(llm_usage)}

    async def ainvoke(self, state: State, config: Optional[RunnableConfig] = None):
        result = None
        assistant_name = self.name or "unknown"
        llm_usage: list[LlmUsage] = []

        for attempt in range(self.max_empty_response_retries + 1):
            result, transport_attempts = await self._ainvoke_transport(
                state,
                config,
                assistant_name=assistant_name,
            )
            llm_usage.extend(self._usage_for_result(result, transport_attempts))

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
        return {"messages": result, "_llm_usage": tuple(llm_usage)}

    def _usage_for_result(self, result, transport_attempts: int) -> list[LlmUsage]:
        usage = []
        if transport_attempts > 1:
            usage.append(
                LlmUsage(
                    calls=transport_attempts - 1,
                    provider=self.default_provider,
                    model=None,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                )
            )
        usage.append(
            LlmUsage.from_message(
                result,
                default_provider=self.default_provider,
            )
        )
        return usage

    def _invoke_transport(
        self,
        state: State,
        config: Optional[RunnableConfig],
        *,
        assistant_name: str,
    ) -> tuple[object, int]:
        transport_attempts = 0

        def invoke():
            nonlocal transport_attempts
            transport_attempts += 1
            return self.runnable.invoke(state, config)

        if self.retry_executor is not None:
            result = self.retry_executor.run(
                invoke,
                operation_name=f"assistant.{assistant_name}.llm",
                dependency="llm",
                idempotent=True,
            )
            return result, transport_attempts

        try:
            return invoke(), transport_attempts
        except Exception as exc:
            raise classify_error(exc, dependency="llm") from exc

    async def _ainvoke_transport(
        self,
        state: State,
        config: Optional[RunnableConfig],
        *,
        assistant_name: str,
    ) -> tuple[object, int]:
        transport_attempts = 0

        async def invoke():
            nonlocal transport_attempts
            transport_attempts += 1
            return await self.runnable.ainvoke(state, config)

        if self.retry_executor is not None:
            result = await self.retry_executor.arun(
                invoke,
                operation_name=f"assistant.{assistant_name}.llm",
                dependency="llm",
                idempotent=True,
            )
            return result, transport_attempts

        try:
            return await invoke(), transport_attempts
        except Exception as exc:
            raise classify_error(exc, dependency="llm") from exc
