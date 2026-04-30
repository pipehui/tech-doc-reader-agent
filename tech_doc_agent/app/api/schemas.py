'''
设置数据进出格式
'''
from pydantic import BaseModel, Field
from tech_doc_agent.app.core.tenant import TENANT_ID_PATTERN

SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
TRACE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$"

class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, pattern=SESSION_ID_PATTERN)
    message: str = Field(min_length=1, max_length=8000)
    trace_id: str | None = Field(default=None, min_length=1, max_length=200, pattern=TRACE_ID_PATTERN)
    user_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=TENANT_ID_PATTERN)
    namespace: str | None = Field(default=None, min_length=1, max_length=128, pattern=TENANT_ID_PATTERN)


class ApproveRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, pattern=SESSION_ID_PATTERN)
    approved: bool
    feedback: str = Field(default="", max_length=2000)
    trace_id: str | None = Field(default=None, min_length=1, max_length=200, pattern=TRACE_ID_PATTERN)
    user_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=TENANT_ID_PATTERN)
    namespace: str | None = Field(default=None, min_length=1, max_length=128, pattern=TENANT_ID_PATTERN)

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
    user_id: str | None = None
    namespace: str | None = None
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
    user_id: str | None = None
    namespace: str | None = None
    learning_target: str | None = None
    pending_interrupt: bool
    message_count: int
    messages: list[HistoryViewItem] = Field(default_factory=list)

class SessionStateResponse(BaseModel):
    session_id: str
    user_id: str | None = None
    namespace: str | None = None
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
    user_id: str | None = None
    namespace: str | None = None

class LearningOverviewResponse(BaseModel):
    user_id: str | None = None
    namespace: str | None = None
    total: int
    average_score: float
    needs_review_count: int
    records: list[LearningRecord] = Field(default_factory=list)

class MemoryRecord(BaseModel):
    id: str
    user_id: str | None = None
    namespace: str | None = None
    kind: str
    topic: str
    content: str
    confidence: float
    source_session_id: str | None = None
    created_at: str
    updated_at: str

class LearningMemoryResponse(BaseModel):
    user_id: str | None = None
    namespace: str | None = None
    total: int
    memories: list[MemoryRecord] = Field(default_factory=list)
