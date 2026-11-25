import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import KnowledgeSource, KnowledgeStatus


class KnowledgeCreate(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    category: str | None = Field(default=None, max_length=64)
    source: KnowledgeSource = KnowledgeSource.GLPI_HISTORY
    glpi_ticket_id: int | None = None


class KnowledgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question: str
    answer: str
    category: str | None
    source: KnowledgeSource
    status: KnowledgeStatus
    glpi_ticket_id: int | None
    reuse_count: int
    created_at: datetime
    updated_at: datetime


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class SearchHit(BaseModel):
    knowledge: KnowledgeRead
    # Cosine similarity in [-1, 1] (1 = identical).
    similarity: float


class ReviewAction(StrEnum):
    REACTIVATE = "reactivate"  # the answer is right: back in the memory, ratings kept
    CORRECT = "correct"  # a fixed answer replaces it, as a new entry with no rating
    ARCHIVE = "archive"  # out of the memory for good


class ReviewItem(BaseModel):
    knowledge: KnowledgeRead
    # Smoothed feedback score in [0, 1], as the ranking reads it; None without rating.
    feedback: float | None
    ratings: int


class ReviewDecision(BaseModel):
    action: ReviewAction
    # The fixed answer: required by `correct`, refused otherwise.
    answer: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _answer_only_for_a_correction(self) -> "ReviewDecision":
        if (self.action is ReviewAction.CORRECT) != (self.answer is not None):
            raise ValueError("`answer` is required by `correct` and refused otherwise")
        return self
