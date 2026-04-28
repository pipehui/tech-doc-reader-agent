from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.core.state import State

settings = get_settings()


def _api_base_or_none(value: str) -> str | None:
    return value or None


# Initialize the language model (shared among assistants)
primary_llm = ChatOpenAI(
    model=settings.PRIMARY_MODEL or "gpt-4o-mini",
    openai_api_key=settings.OPENAI_API_KEY or "not-set",
    openai_api_base=_api_base_or_none(settings.OPENAI_BASE_URL),
    temperature=0,
)

if settings.BACKUP_MODEL and settings.BACKUP_API_KEY:
    backup_llm = ChatOpenAI(
        model=settings.BACKUP_MODEL,
        openai_api_key=settings.BACKUP_API_KEY,
        openai_api_base=_api_base_or_none(settings.BACKUP_API_BASE),
        temperature=0,
    )
    llm = primary_llm.with_fallbacks([backup_llm])
else:
    backup_llm = None
    llm = primary_llm

def is_empty_assistant_output(result) -> bool:
    if getattr(result, "tool_calls", None):
        return False

    content = getattr(result, "content", None)
    if not content:
        return True

    if isinstance(content, list):
        if not content:
            return True

        return not any(
            isinstance(item, dict)
            and str(item.get("text", "")).strip()
            for item in content
        )

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

    def __call__(self, state: State, config: Optional[RunnableConfig] = None):
        result = None
        assistant_name = self.name or "unknown"

        for attempt in range(self.max_retries + 1):
            result = self.runnable.invoke(state, config)

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
                f"Assistant {assistant_name} returned empty output "
                f"after {self.max_retries + 1} attempts."
            )

        if self.name and not getattr(result, "name", None):
            if hasattr(result, "model_copy"):
                result = result.model_copy(update={"name": self.name})
            else:
                result = result.copy(update={"name": self.name})
        return {"messages": result}

# Define the CompleteOrEscalate tool
class CompleteOrEscalate(BaseModel):
    """A tool to mark the current task as completed or to escalate control to the main assistant."""
    cancel: bool = True
    reason: str
