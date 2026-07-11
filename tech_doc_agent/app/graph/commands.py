from typing import Literal

from pydantic import BaseModel, Field


class CompleteOrEscalate(BaseModel):
    """A tool to mark the current task as completed or to escalate control to the main assistant."""

    cancel: bool = True
    reason: str


class ToDocParserAssistant(BaseModel):
    """Transfers work to a specialized assistant to handle document parsing."""

    content: str = Field(
        description="The content of the document that needs to be parsed, or a URL pointing to the document, or any other relevant information that can help the document parsing assistant understand what needs to be parsed."
    )
    request: str = Field(
        description="Any specific information the user wants to extract from the document or any particular questions they have about the document."
    )


class ToExplanationAssistant(BaseModel):
    """Transfers work to a specialized assistant to handle concept explanation."""

    concept: str = Field(description="The specific concept or topic that the user wants to understand better.")
    request: str = Field(description="Any specific questions the user has about the concept or any particular aspects they want the explanation to focus on.")


class ToRelationAssistant(BaseModel):
    """Transfers work to a specialized assistant to retrieve analogous or related knowledge that may help the user understand the target concept, even when the user did not explicitly ask for analogy."""

    entity: str = Field(description="The target concept, mechanism, or topic that needs analogical or relational retrieval.")
    request: str = Field(description="Why this relation retrieval is useful for the current learning goal, including any user question, confusion point, or context that should guide the retrieval.")


class ToExaminationAssistant(BaseModel):
    """Transfers work to a specialized assistant to handle examination and quiz generation."""

    topic: str = Field(description="The specific topic or subject that the user wants to be tested on.")
    request: str = Field(description="Any specific questions the user has about the topic or any particular types of questions they want the examination assistant to generate.")


class ToSummaryAssistant(BaseModel):
    """Transfers work to a specialized assistant to summarize the user's learning process."""

    request: str = Field(description="What kind of learning summary the user needs, including whether the focus should be on key takeaways, mistakes, corrections, or review suggestions.")


class PlanWorkflow(BaseModel):
    steps: list[Literal["parser", "relation", "explanation", "examination", "summary"]]
    goal: str = Field(description="The user's learning goal in this turn.")
    learning_target: str = Field(
        description="The canonical learning target for this turn. Use one stable, concise, reusable topic name. Prefer the exact term used by the user or document. Do not add suffixes like 'core concepts', 'basics', 'summary', or 'notes'."
    )
