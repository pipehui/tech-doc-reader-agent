'''
设置数据进出格式
'''
from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    session_id: str
    message: str


class ApproveRequest(BaseModel):
    session_id: str
    approved: bool
    feedback: str = ""

class HistoryMessage(BaseModel):
    id: str | None = None
    role: str
    raw_type: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)

class HistoryResponse(BaseModel):
    session_id: str
    learning_target: str | None = None
    pending_interrupt: bool
    message_count: int
    messages: list[HistoryMessage] = Field(default_factory=list)

class HistoryViewItem(BaseModel):
    id: str | None = None
    role: str
    kind: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None

class HistoryViewResponse(BaseModel):
    session_id: str
    learning_target: str | None = None
    pending_interrupt: bool
    message_count: int
    messages: list[HistoryViewItem] = Field(default_factory=list)

class SessionStateResponse(BaseModel):
    session_id: str
    exists: bool
    pending_interrupt: bool
    learning_target: str | None = None
    message_count: int
    current_agent: str | None = None
    workflow_plan: list[str] = Field(default_factory=list)
    plan_index: int = 0

class LearningRecord(BaseModel):
    knowledge: str
    timestamp: str
    score: float
    reviewtimes: int

class LearningOverviewResponse(BaseModel):
    total: int
    average_score: float
    needs_review_count: int
    records: list[LearningRecord] = Field(default_factory=list)
