import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
