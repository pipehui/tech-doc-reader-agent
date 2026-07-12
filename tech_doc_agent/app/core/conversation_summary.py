from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol, Sequence

from tech_doc_agent.app.core.context_serialization import serialized_sha256
from tech_doc_agent.app.core.errors import ValidationError


CONVERSATION_SUMMARY_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SummarySourceRange:
    start_message_id: str
    end_message_id: str
    message_count: int
    content_sha256: str

    @classmethod
    def from_messages(cls, messages: Sequence[Any]) -> SummarySourceRange:
        if not messages:
            raise _invalid_summary("source_messages")

        start_message_id = _message_id(messages[0])
        end_message_id = _message_id(messages[-1])
        content_sha256 = serialized_sha256(list(messages))
        if content_sha256 is None:
            raise _invalid_summary("source_digest")

        return cls(
            start_message_id=start_message_id,
            end_message_id=end_message_id,
            message_count=len(messages),
            content_sha256=content_sha256,
        )

    @classmethod
    def from_state(cls, payload: Any) -> SummarySourceRange:
        if not isinstance(payload, dict):
            raise _invalid_summary("source_range")
        try:
            source = cls(
                start_message_id=payload["start_message_id"],
                end_message_id=payload["end_message_id"],
                message_count=payload["message_count"],
                content_sha256=payload["content_sha256"],
            )
        except (KeyError, TypeError):
            raise _invalid_summary("source_range") from None
        source.validate()
        return source

    def validate(self) -> None:
        _validate_identifier(self.start_message_id, "start_message_id")
        _validate_identifier(self.end_message_id, "end_message_id")
        if isinstance(self.message_count, bool) or not isinstance(self.message_count, int):
            raise _invalid_summary("message_count")
        if self.message_count <= 0:
            raise _invalid_summary("message_count")
        if not isinstance(self.content_sha256, str) or not _SHA256_RE.fullmatch(
            self.content_sha256
        ):
            raise _invalid_summary("content_sha256")

    def to_state(self) -> dict[str, Any]:
        self.validate()
        return {
            "start_message_id": self.start_message_id,
            "end_message_id": self.end_message_id,
            "message_count": self.message_count,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    schema_version: int
    summary_id: str
    predecessor_summary_id: str | None
    generator_id: str
    content: str
    source_ranges: tuple[SummarySourceRange, ...]
    covered_message_count: int

    @classmethod
    def create(
        cls,
        *,
        generator_id: str,
        content: str,
        source_range: SummarySourceRange,
        previous: ConversationSummary | None = None,
    ) -> ConversationSummary:
        source_range.validate()
        source_ranges = (
            (*previous.source_ranges, source_range)
            if previous is not None
            else (source_range,)
        )
        covered_message_count = sum(item.message_count for item in source_ranges)
        predecessor_summary_id = previous.summary_id if previous is not None else None
        summary_id = _summary_id(
            generator_id=generator_id,
            content=content,
            source_ranges=source_ranges,
            predecessor_summary_id=predecessor_summary_id,
        )
        summary = cls(
            schema_version=CONVERSATION_SUMMARY_SCHEMA_VERSION,
            summary_id=summary_id,
            predecessor_summary_id=predecessor_summary_id,
            generator_id=generator_id,
            content=content,
            source_ranges=source_ranges,
            covered_message_count=covered_message_count,
        )
        summary.validate()
        return summary

    @classmethod
    def from_state(cls, payload: Any) -> ConversationSummary:
        if not isinstance(payload, dict):
            raise _invalid_summary()
        raw_ranges = payload.get("source_ranges")
        if not isinstance(raw_ranges, list):
            raise _invalid_summary("source_ranges")
        try:
            summary = cls(
                schema_version=payload["schema_version"],
                summary_id=payload["summary_id"],
                predecessor_summary_id=payload["predecessor_summary_id"],
                generator_id=payload["generator_id"],
                content=payload["content"],
                source_ranges=tuple(
                    SummarySourceRange.from_state(item) for item in raw_ranges
                ),
                covered_message_count=payload["covered_message_count"],
            )
        except (KeyError, TypeError):
            raise _invalid_summary() from None
        summary.validate()
        return summary

    def validate(self) -> None:
        if self.schema_version != CONVERSATION_SUMMARY_SCHEMA_VERSION:
            raise _invalid_summary("schema_version")
        if self.predecessor_summary_id is not None and (
            not isinstance(self.predecessor_summary_id, str)
            or not _SHA256_RE.fullmatch(self.predecessor_summary_id)
        ):
            raise _invalid_summary("predecessor_summary_id")
        _validate_identifier(self.generator_id, "generator_id")
        if not isinstance(self.content, str) or not self.content.strip():
            raise _invalid_summary("content")
        if not self.source_ranges:
            raise _invalid_summary("source_ranges")
        for source_range in self.source_ranges:
            source_range.validate()
        if (
            isinstance(self.covered_message_count, bool)
            or not isinstance(self.covered_message_count, int)
            or self.covered_message_count
            != sum(item.message_count for item in self.source_ranges)
        ):
            raise _invalid_summary("covered_message_count")
        expected_id = _summary_id(
            generator_id=self.generator_id,
            content=self.content,
            source_ranges=self.source_ranges,
            predecessor_summary_id=self.predecessor_summary_id,
        )
        if self.summary_id != expected_id:
            raise _invalid_summary("summary_id")

    def to_state(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "summary_id": self.summary_id,
            "predecessor_summary_id": self.predecessor_summary_id,
            "generator_id": self.generator_id,
            "content": self.content,
            "source_ranges": [item.to_state() for item in self.source_ranges],
            "covered_message_count": self.covered_message_count,
        }


class ConversationSummarizer(Protocol):
    @property
    def generator_id(self) -> str: ...

    def summarize(
        self,
        *,
        previous: ConversationSummary | None,
        messages: Sequence[Any],
        max_chars: int,
    ) -> str: ...


def read_conversation_summary(payload: Any) -> ConversationSummary | None:
    if payload is None:
        return None
    return ConversationSummary.from_state(payload)


def _summary_id(
    *,
    generator_id: str,
    content: str,
    source_ranges: Sequence[SummarySourceRange],
    predecessor_summary_id: str | None = None,
) -> str:
    digest = serialized_sha256(
        {
            "schema_version": CONVERSATION_SUMMARY_SCHEMA_VERSION,
            "generator_id": generator_id,
            "predecessor_summary_id": predecessor_summary_id,
            "content": content,
            "source_ranges": [item.to_state() for item in source_ranges],
        }
    )
    if digest is None:
        raise _invalid_summary("summary_id")
    return digest


def _message_id(message: Any) -> str:
    message_id = getattr(message, "id", None)
    if not isinstance(message_id, str) or not message_id or message_id != message_id.strip():
        raise _invalid_summary("message_id")
    return message_id


def _validate_identifier(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _invalid_summary(field_name)


def _invalid_summary(field_name: str | None = None) -> ValidationError:
    return ValidationError(
        "The persisted conversation summary is invalid.",
        code="conversation_summary_invalid",
        dependency="checkpoint",
        cause_type=field_name or "ConversationSummary",
    )


__all__ = [
    "CONVERSATION_SUMMARY_SCHEMA_VERSION",
    "ConversationSummarizer",
    "ConversationSummary",
    "SummarySourceRange",
    "read_conversation_summary",
]
