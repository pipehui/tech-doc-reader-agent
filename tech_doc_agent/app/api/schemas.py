'''
设置数据进出格式
'''
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator
from tech_doc_agent.app.core.revisions import FULL_GIT_COMMIT_PATTERN
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


class AssistantExecutionIdentityResponse(BaseModel):
    assistant_role: Literal[
        "primary",
        "parser",
        "relation",
        "explanation",
        "examination",
        "summary",
    ]
    prompt_id: str
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_provider_id: str
    primary_model_id: str | None = None
    backup_model_id: str | None = None


class RuntimeDeploymentIdentityResponse(BaseModel):
    status: Literal["configured", "unavailable"]
    commit_sha: str | None = Field(
        default=None,
        pattern=FULL_GIT_COMMIT_PATTERN,
    )

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.status == "configured" and self.commit_sha is None:
            raise ValueError("Configured deployment identity requires commit_sha")
        if self.status == "unavailable" and self.commit_sha is not None:
            raise ValueError("Unavailable deployment identity cannot include commit_sha")
        return self


class RuntimeExecutionIdentityResponse(BaseModel):
    schema_version: Literal[1, 2]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    assistants: list[AssistantExecutionIdentityResponse]
    deployment: RuntimeDeploymentIdentityResponse | None = None

    @model_validator(mode="after")
    def validate_versioned_deployment(self) -> Self:
        if self.schema_version == 1 and self.deployment is not None:
            raise ValueError("Runtime identity schema v1 cannot include deployment")
        if self.schema_version == 2 and self.deployment is None:
            raise ValueError("Runtime identity schema v2 requires deployment")
        return self

class HistoryMessage(BaseModel):
    id: str | None = None
    role: Literal["user", "assistant", "system", "tool"]
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
    role: Literal["user", "assistant", "system", "tool"]
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
    budget_usage: dict[str, Any] | None = None
    budget_status: Literal["active", "terminating", "terminated"] | None = None
    budget_termination: dict[str, Any] | None = None
    context_metrics: dict[str, Any] | None = None
    provider_retry_usage: dict[str, Any] | None = None

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

class UserProfileResponse(BaseModel):
    profile_version: int
    user_id: str | None = None
    namespace: str | None = None
    experience_level: str
    explanation_style: str
    depth: str
    language: str
    known_topics: list[str] = Field(default_factory=list)
    weak_topics: list[str] = Field(default_factory=list)
    notes: str = ""
    last_update_reason: str | None = None
    updated_at: str | None = None
