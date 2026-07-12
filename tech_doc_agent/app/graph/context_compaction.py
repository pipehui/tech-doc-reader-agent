from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from tech_doc_agent.app.core.context_compaction import (
    ContextCompactionPolicy,
    plan_context_compaction,
)
from tech_doc_agent.app.core.context_serialization import estimate_serialized_bytes
from tech_doc_agent.app.core.conversation_summary import (
    ConversationSummarizer,
    ConversationSummary,
    SummarySourceRange,
    read_conversation_summary,
)
from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.observability import log_event

from .state import State


@dataclass(frozen=True, slots=True)
class ContextCompactor:
    policy: ContextCompactionPolicy
    summarizer: ConversationSummarizer
    event_logger: Callable[..., None] = log_event

    def __call__(self, state: State) -> dict:
        decision = plan_context_compaction(state, self.policy)
        if not decision.should_compact:
            if decision.skip_reason not in {"disabled", "threshold_not_exceeded"}:
                self.event_logger(
                    "context.compaction.skipped",
                    reason=decision.skip_reason,
                    checkpoint_message_count=len(state.get("messages", [])),
                )
            return {}

        plan = decision.plan
        assert plan is not None
        previous = read_conversation_summary(state.get("conversation_summary"))
        try:
            source_range = SummarySourceRange.from_messages(plan.source_messages)
        except ValidationError:
            self.event_logger(
                "context.compaction.skipped",
                reason="source_metadata_unavailable",
                checkpoint_message_count=plan.before_message_count,
            )
            return {}
        content = self.summarizer.summarize(
            previous=previous,
            messages=plan.source_messages,
            max_chars=self.policy.summary_max_chars,
        )
        summary = ConversationSummary.create(
            generator_id=self.summarizer.generator_id,
            content=content,
            source_range=source_range,
            previous=previous,
        )
        after_serialized_bytes = estimate_serialized_bytes(
            {
                "messages": plan.retained_messages,
                "conversation_summary": summary.to_state(),
            }
        )
        self.event_logger(
            "context.compacted",
            generator_id=summary.generator_id,
            removed_message_count=len(plan.source_messages),
            retained_message_count=len(plan.retained_messages),
            covered_message_count=summary.covered_message_count,
            before_serialized_bytes=plan.before_serialized_bytes,
            after_serialized_bytes=after_serialized_bytes,
        )
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *plan.retained_messages,
            ],
            "conversation_summary": summary.to_state(),
        }


__all__ = ["ContextCompactor"]
