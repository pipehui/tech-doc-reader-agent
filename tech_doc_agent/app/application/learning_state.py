from datetime import UTC, datetime

from tech_doc_agent.app.application.learning_commands import (
    UpdateLearningStateCommand,
    UpdateLearningStateResult,
)
from tech_doc_agent.app.application.learning_ports import (
    LearningRecordUpdaterPort,
    MemoryUpdaterPort,
)
from tech_doc_agent.app.application.learning_unit_of_work import (
    LearningStateSnapshot,
    LearningStateUnitOfWork,
)


class LearningStateService:
    def __init__(
        self,
        unit_of_work: LearningStateUnitOfWork,
        learning_records: LearningRecordUpdaterPort,
        memories: MemoryUpdaterPort,
    ) -> None:
        self.unit_of_work = unit_of_work
        self.learning_records = learning_records
        self.memories = memories

    def update(
        self,
        command: UpdateLearningStateCommand,
    ) -> UpdateLearningStateResult:
        def mutate(snapshot: LearningStateSnapshot) -> UpdateLearningStateResult:
            snapshot.records, learning_message = self.learning_records.prepare_upsert_record(
                snapshot.records,
                knowledge=command.knowledge,
                timestamp=command.timestamp,
                score=command.score,
                tenant=command.tenant,
            )

            memory_message = "No memory fragment written."
            memory_id = None
            if command.memory_content and command.memory_content.strip():
                snapshot.memories, memory = self.memories.prepare_upsert_memory(
                    snapshot.memories,
                    kind=command.memory_kind or "learned",
                    topic=command.memory_topic or command.knowledge,
                    content=command.memory_content,
                    confidence=command.memory_confidence,
                    source_session_id=command.session_id,
                    tenant=command.tenant,
                    timestamp=datetime.now(UTC).isoformat(),
                )
                memory_id = memory.id
                memory_message = f"Memory '{memory_id}' has been upserted."

            return UpdateLearningStateResult(
                learning_message=learning_message,
                memory_message=memory_message,
                memory_id=memory_id,
            )

        return self.unit_of_work.execute(command, mutate)


__all__ = ["LearningStateService"]
