from typing import Optional
from langchain_core.runnables import Runnable, RunnableConfig
from customer_support_chat.app.core.state import State
from pydantic import BaseModel
from customer_support_chat.app.core.settings import get_settings
from langchain_openai import ChatOpenAI

settings = get_settings()

# Initialize the language model (shared among assistants)
primary_llm = ChatOpenAI(
    model=settings.PRIMARY_MODEL,
    openai_api_key=settings.OPENAI_API_KEY,
    openai_api_base=settings.OPENAI_BASE_URL,
    temperature=0,
)

backup_llm = ChatOpenAI(
    model=settings.BACKUP_MODEL,
    openai_api_key=settings.BACKUP_API_KEY,
    openai_api_base=settings.BACKUP_API_BASE,
    temperature=0,
)

llm = primary_llm.with_fallbacks([backup_llm])

class Assistant:
    def __init__(self, runnable: Runnable, name: str | None = None):
        self.runnable = runnable
        self.name = name

    def __call__(self, state: State, config: Optional[RunnableConfig] = None):
        while True:
            result = self.runnable.invoke(state, config)

            if not result.tool_calls and (
                not result.content
                or isinstance(result.content, list)
                and not result.content[0].get("text")
            ):
                messages = state["messages"] + [("user", "Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
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
