from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel

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

class Assistant:
    def __init__(self, runnable: Runnable, name: str | None = None, max_retries: int = 3):
        self.runnable = runnable
        self.name = name
        self.max_retries = max_retries

    def __call__(self, state: State, config: Optional[RunnableConfig] = None):
        result = None
        for _ in range(self.max_retries + 1):
            result = self.runnable.invoke(state, config)

            if not result.tool_calls and (
                not result.content
                or isinstance(result.content, list)
                and (not result.content or not result.content[0].get("text"))
            ):
                messages = state["messages"] + [("user", "Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
        else:
            raise RuntimeError(
                f"Assistant {self.name or 'unknown'} returned empty output "
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
