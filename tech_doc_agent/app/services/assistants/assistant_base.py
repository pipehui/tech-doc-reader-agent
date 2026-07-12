from typing import Optional

from langchain_core.runnables import Runnable, RunnableConfig

from tech_doc_agent.app.core.errors import classify_error
from tech_doc_agent.app.core.observability import log_event
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
    def __init__(self, runnable: Runnable, name: str | None = None, max_retries: int = 3):
        if max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to 0")

        self.runnable = runnable
        self.name = name
        self.max_retries = max_retries

    def _name_result(self, result):
        if self.name and not getattr(result, "name", None):
            if hasattr(result, "model_copy"):
                return result.model_copy(update={"name": self.name})
            return result.copy(update={"name": self.name})
        return result

    def __call__(self, state: State, config: Optional[RunnableConfig] = None):
        result = None
        assistant_name = self.name or "unknown"

        for attempt in range(self.max_retries + 1):
            try:
                result = self.runnable.invoke(state, config)
            except Exception as exc:
                raise classify_error(exc, dependency="llm") from exc

            if is_empty_assistant_output(result):
                log_event(
                    "assistant.empty_response",
                    assistant=assistant_name,
                    attempt=attempt + 1,
                    max_attempts=self.max_retries + 1,
                )
                messages = state["messages"] + [("user", "Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
        else:
            log_event(
                "assistant.empty_response.exhausted",
                assistant=assistant_name,
                max_attempts=self.max_retries + 1,
            )
            raise RuntimeError(
                f"Assistant {assistant_name} returned empty output after {self.max_retries + 1} attempts."
            )

        result = self._name_result(result)
        return {"messages": result}

    async def ainvoke(self, state: State, config: Optional[RunnableConfig] = None):
        result = None
        assistant_name = self.name or "unknown"

        for attempt in range(self.max_retries + 1):
            try:
                result = await self.runnable.ainvoke(state, config)
            except Exception as exc:
                raise classify_error(exc, dependency="llm") from exc

            if is_empty_assistant_output(result):
                log_event(
                    "assistant.empty_response",
                    assistant=assistant_name,
                    attempt=attempt + 1,
                    max_attempts=self.max_retries + 1,
                    async_runtime=True,
                )
                messages = state["messages"] + [("user", "Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
        else:
            log_event(
                "assistant.empty_response.exhausted",
                assistant=assistant_name,
                max_attempts=self.max_retries + 1,
                async_runtime=True,
            )
            raise RuntimeError(
                f"Assistant {assistant_name} returned empty output after {self.max_retries + 1} attempts."
            )

        result = self._name_result(result)
        return {"messages": result}
