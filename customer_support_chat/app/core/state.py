from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict, NotRequired
from langgraph.graph.message import AnyMessage, add_messages

WorkflowStep = Literal[
    "parser",
    "relation",
    "explanation",
    "examination",
    "summary",
]

def update_dialog_stack(left: list[str], right: Optional[str]) -> list[str]:
    """Push or pop the dialog state stack."""
    if right is None:
        return left
    if right == "pop":
        return left[:-1]
    return left + [right]

class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_info: str
    dialog_state: Annotated[list[WorkflowStep | Literal["primary"]], update_dialog_stack]
    learning_target: str
    workflow_plan: NotRequired[list[WorkflowStep]]
    plan_index: NotRequired[int]
    parser_result: NotRequired[str]
    relation_result: NotRequired[str]